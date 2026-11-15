"""上下文块组装（DEVELOPMENT_PLAN v2.0 · S1）。

职责：

1. ``get_context_blocks()`` 把人格 / 规则 / 今日数据拆成 :class:`ContextBlock`。
2. ``assemble(blocks, budget)`` 过滤被排除的块、按优先级**整块丢弃**超预算的部分，
   再按稳定顺序拼接成 system prompt。

依赖方向：``ai/context → services``（查询接口），符合方案 §4.2。
本模块只从 services 取查询接口（``get_today_context``），其余依赖仅限 core；
它不反向被任何 service 依赖。
"""

from dataclasses import dataclass

from app.ai.context.blocks import (
    KIND_HISTORY,
    KIND_MEMORY,
    KIND_PERSONA,
    KIND_RULES,
    KIND_TODAY,
    SENSITIVITY_NORMAL,
    SENSITIVITY_SENSITIVE,
    STATE_EXCLUDE_ALWAYS,
    STATE_EXCLUDE_ONCE,
    STATE_INCLUDE,
    ContextBlock,
)
from app.core.config import CONTEXT_SYSTEM_BUDGET, CONTEXT_TOTAL_BUDGET
from app.core.persona_loader import load_persona_text, persona_display_name
from app.core.prompt_loader import load_prompt
from app.core.token_budget import estimate_tokens
from app.services import context_exclusion_service
from app.services.context_service import get_today_context
from app.services.memory_service import (
    list_impressions,
    list_observations,
    search_memories,
)
from app.services.persona_service import active_persona_name

# 优先级：越大越先保留。丢弃时按优先级升序丢。
# 依据 DEVELOPMENT_PLAN §1.5 决定 3：
#   实际时间轴 → 今日习惯 → 番茄 → 更早聊天 → 今日日程
#   → 低置信度记忆 → 高置信度记忆 → 人格与规则（最后丢）
PRIORITY_PERSONA = 100
PRIORITY_RULES = 95
PRIORITY_MEMORY_HIGH = 90  # S4 记忆表接入后使用
PRIORITY_MEMORY_LOW = 80  # S4 记忆表接入后使用
PRIORITY_TODAY_DEADLINES = 75
PRIORITY_TODAY_SCHEDULE = 70
PRIORITY_HISTORY = 60  # 最近聊天；S2 起纳入统一预算与丢弃顺序
PRIORITY_TODAY_POMODORO = 50
PRIORITY_TODAY_HABITS = 40
PRIORITY_TODAY_TIMELINE = 30

# 历史消息每条的固定开销。口径沿用旧的 `take_recent_messages`（那个函数已按 R9 删除），
# 面板底部显示的"合计"才是与实际请求一致的数。
_HISTORY_MESSAGE_OVERHEAD = 8

# 人格与规则是"她是谁"，永远最后丢；即使超预算也不丢、不切分。
_NEVER_DROP_KINDS = (KIND_PERSONA, KIND_RULES)


def _make_block(
    block_id: str,
    kind: str,
    label: str,
    content: str,
    priority: int,
    source: str = "",
    sensitivity: str = SENSITIVITY_NORMAL,
) -> ContextBlock:
    return ContextBlock(
        id=block_id,
        kind=kind,
        label=label,
        content=content,
        tokens=estimate_tokens(content),
        source=source,
        sensitivity=sensitivity,
        state=STATE_INCLUDE,
        priority=priority,
    )


def _render_meta(context: dict) -> str:
    """今日数据的表头。

    S1 起不再把精确 UTC 时间发给模型（见 CONTEXT_CONTROL_PLAN §11-2）：
    她不需要知道精确 UTC，时间锚点用消息时间戳就够，而 ``now`` 会让
    "今日数据"这一整块每秒都变，KV 缓存全部失效。
    """
    return (
        "【当前真实生活状态】\n"
        f"用户当地日期：{context['local_date']}（{context['timezone']}）"
    )


def _render_schedule(context: dict) -> str:
    lines = ["【今日日程】"]
    if context["schedule"]:
        for item in context["schedule"]:
            lines.append(
                f"- #{item['id']} {item['title']} | {item['start_at_local']}"
                f"{(' ~ ' + item['end_at_local']) if item['end_at_local'] else ''}"
                f" | {item['completion_status']} / {item['time_status']}"
            )
    else:
        lines.append("- 无")
    return "\n".join(lines)


def _render_deadlines(context: dict) -> str:
    lines = ["【DDL：逾期 + 未来7天】"]
    if context["deadlines"]:
        for item in context["deadlines"]:
            lines.append(
                f"- #{item['id']} {item['title']} | 截止 {item['due_at_local']}"
                f" | {item['time_status']} | priority={item['priority']}"
            )
    else:
        lines.append("- 无")
    return "\n".join(lines)


def _render_habits(context: dict) -> str:
    lines = ["【今日习惯】"]
    if context["habits"]:
        for item in context["habits"]:
            if item["tracking_type"] == "boolean":
                current = "完成" if item["current_bool"] else "未完成"
            elif item["tracking_type"] == "time":
                current = item["current_text"] or "未记录"
            else:
                current = (
                    "未记录"
                    if item["current_number"] is None
                    else f"{item['current_number']} {item['unit'] or ''}".strip()
                )
            lines.append(f"- {item['name']}：{current} | 达标={item['met']}")
    else:
        lines.append("- 无")
    return "\n".join(lines)


def _render_pomodoro(context: dict) -> str:
    pomodoro = context["pomodoro"]
    lines = [f"【番茄】完成 {pomodoro['count']} 个，共 {pomodoro['minutes']} 分钟"]
    for group in pomodoro["groups"]:
        lines.append(f"- {group['label']}：{group['count']} 个 / {group['minutes']} 分钟")
    if pomodoro.get("running"):
        lines.append(f"- 正在进行：{pomodoro['running']['target_label']}")
    return "\n".join(lines)


def _render_timeline(context: dict) -> str:
    lines = ["【实际时间轴】"]
    if context["timeline"]:
        for item in context["timeline"][-12:]:
            lines.append(
                f"- {item['start_at_local']} {item['title']}"
                f"{(' ~ ' + item['end_at_local']) if item['end_at_local'] else ''}"
                f" | 来源={item['source_type']}"
            )
    else:
        lines.append("- 无")
    return "\n".join(lines)


# 记忆块最多放几条——它是 system prompt 的一部分，不是数据库导出。
MEMORY_IMPRESSION_LIMIT = 8
MEMORY_OBSERVATION_LIMIT = 12
# 低于这个置信度的印象不往 prompt 里塞：她不该拿自己瞎猜的东西当依据说话。
MEMORY_MIN_CONFIDENCE = 0.4


def _pick_impressions(query: str | None) -> list[dict]:
    """给上下文挑判断：**有查询就按相关度，查不到再退回最近的**。

    退回那一步很重要：记忆是**短句**、查询是**长句**，lexical 检索经常
    一个窗口都命中不了 —— 而"最近的判断"在那种情况下始终是有用的。
    """
    if query:
        found = search_memories(query, limit=MEMORY_IMPRESSION_LIMIT * 2)["impressions"]
        if found:
            return found
    return list_impressions(include_superseded=False)


def _pick_observations(query: str | None) -> list[dict]:
    """同上，挑事实。"""
    if query:
        found = search_memories(
            query, limit=MEMORY_OBSERVATION_LIMIT * 2
        )["observations"]
        if found:
            return found
    return list_observations(limit=MEMORY_OBSERVATION_LIMIT)


def _render_impressions(query: str | None = None) -> str:
    """她当前的判断（L3）。

    只放**当前代际**里置信度够的。有异议的必须带上"你不同意"——
    不变量 6：判断可以标异议，但不能装作没有异议。
    """
    lines: list[str] = []
    for impression in _pick_impressions(query):
        # ⚠️ 这里**只能**排除 superseded，不要写成 `status != "active"`。
        # `set_impression_attitude(disputed)` 会把 status 置成 "disputed"
        # （memory_service.py 那一行 `status = "disputed" if ...`），
        # 那样写会把"你有异议的判断"整个从 prompt 里抹掉——
        # 而不变量 6 说的是：判断可以被标异议，**但不能被删**，
        # 她必须看得见它，并且带着"你不同意"把它说出来。
        if impression["status"] == "superseded":
            continue
        if impression["confidence"] < MEMORY_MIN_CONFIDENCE:
            continue

        note = ""
        if impression["attitude"] == "disputed":
            note = "（你对此有异议，别当成共识）"
        elif impression["attitude"] == "agreed":
            note = "（你认同过）"

        lines.append(
            f"- {impression['content']}"
            f"（置信度 {impression['confidence']:.1f}，"
            f"依据 {len(impression['evidence'])} 条）{note}"
        )
        if len(lines) >= MEMORY_IMPRESSION_LIMIT:
            break

    if not lines:
        return ""
    return (
        "关于你的判断（每条都有依据，可以被引用；你也可以推翻）：\n"
        + "\n".join(lines)
    )


def _render_observations(query: str | None = None) -> str:
    """发生过的具体事（L2）。都可追溯——没有出处的东西不许出现在这里。

    ``query`` 给了就按相关度挑（可能命中几个月前那句），
    否则按时间取最近的。
    """
    observations = _pick_observations(query)
    if not observations:
        return ""

    lines = [f"- {item['local_date']}：{item['content']}" for item in observations]
    return "最近发生过的具体事（都是你说过或记下的）：\n" + "\n".join(lines)


def get_context_blocks(query: str | None = None) -> list[ContextBlock]:
    """构建 system prompt 侧的块，顺序固定（缓存友好）。

    ``query`` = 这次请求要**检索什么**（聊天就是用户刚说的那句）。
    给了它就按相关度挑记忆（FTS5 —— 没有它，三个月前那句话永远检索不到）；
    不给就按时间取最近的 —— 主动联系没有"用户这句话"，那边因此保持原行为。

    顺序：persona → rules → memory:impressions → memory:observations →
    today:meta → today:schedule → today:deadlines → today:habits →
    today:pomodoro → today:timeline

    这个顺序同时是"变化频率从低到高"：人格与规则基本不变，今日数据一天变几次。
    稳定的前缀是 DeepSeek KV 缓存命中的前提。记忆夹在两者之间——
    它比"她是谁"变得勤，比"今天几点吃饭"稳。

    - 人格（S3）来自 ``backend/personas/<name>/persona.md``；
      "当前是哪一个"由 ``persona_service.active_persona_name()`` 决定。
    - 行为规则是**通用骨架**，不随人格变，也不应被人格文件覆盖。
    - 记忆（S4）**标成 sensitive**：那是她记得的你，是这里最私密的一块。
      它同时是 S9 那条"回复体现记忆内容"的落点——记忆不进 prompt，
      她就只能靠人格说话，那不叫记得你。
    """
    context = get_today_context()
    persona_name = active_persona_name()

    blocks = [
        _make_block(
            "persona",
            KIND_PERSONA,
            f"人格（{persona_name}）",
            load_persona_text(persona_name),
            PRIORITY_PERSONA,
            source=f"personas/{persona_name}/persona.md",
        ),
        _make_block(
            "rules",
            KIND_RULES,
            "行为规则",
            load_prompt("behavior_rules.md"),
            PRIORITY_RULES,
            source="behavior_rules.md",
        ),
        _make_block(
            "memory:impressions",
            KIND_MEMORY,
            "关于你的判断",
            _render_impressions(query),
            PRIORITY_MEMORY_HIGH,
            source="impressions",
            sensitivity=SENSITIVITY_SENSITIVE,
        ),
        _make_block(
            "memory:observations",
            KIND_MEMORY,
            "最近的记忆",
            _render_observations(query),
            PRIORITY_MEMORY_LOW,
            source="observations",
            sensitivity=SENSITIVITY_SENSITIVE,
        ),
        _make_block(
            "today:meta",
            KIND_TODAY,
            "当前生活状态",
            _render_meta(context),
            PRIORITY_TODAY_SCHEDULE,
        ),
        _make_block(
            "today:schedule",
            KIND_TODAY,
            "今日日程",
            _render_schedule(context),
            PRIORITY_TODAY_SCHEDULE,
        ),
        _make_block(
            "today:deadlines",
            KIND_TODAY,
            "近期 DDL",
            _render_deadlines(context),
            PRIORITY_TODAY_DEADLINES,
        ),
        _make_block(
            "today:habits",
            KIND_TODAY,
            "今日习惯",
            _render_habits(context),
            PRIORITY_TODAY_HABITS,
        ),
        _make_block(
            "today:pomodoro",
            KIND_TODAY,
            "番茄",
            _render_pomodoro(context),
            PRIORITY_TODAY_POMODORO,
        ),
        _make_block(
            "today:timeline",
            KIND_TODAY,
            "实际时间轴",
            _render_timeline(context),
            PRIORITY_TODAY_TIMELINE,
        ),
    ]

    # 记忆为空时不留空块：面板上不该出现"她眼中的你（空）"这种占位。
    # 顺带一个好处——全新安装的块列表和 S1/S2 时代完全一致。
    return [
        block
        for block in blocks
        if block.kind != KIND_MEMORY or block.content.strip()
    ]


# 合成块的固定 id。让"最近聊天"也能像其他块一样被排除、被面板列出。
HISTORY_BLOCK_ID = "history"


def resolve_blocks(
    excluded_ids: set[str] | None = None,
    query: str | None = None,
) -> list[ContextBlock]:
    """``get_context_blocks()`` + 应用用户的排除状态（S2）。

    - ``exclude_always``：读 ``context_exclusions`` 表（永久，可恢复）
    - ``exclude_once``：读进程内的一次性集合（由 chat 请求消费）

    ``excluded_ids`` 允许调用方传入已读到的集合，避免同一个请求重复查库。
    """
    blocks = get_context_blocks(query)
    if excluded_ids is None:
        excluded_ids = context_exclusion_service.list_excluded_ids()
    excluded_once = context_exclusion_service.list_once_ids()

    for block in blocks:
        if block.id in excluded_ids:
            block.state = STATE_EXCLUDE_ALWAYS
        elif block.id in excluded_once:
            block.state = STATE_EXCLUDE_ONCE
    return blocks


#: 低清图（`detail: "low"`，缩到 512×512）在请求里的固定成本。
#: 官方口径是 85 token 量级 —— 这里用保守值，宁可高估也不让预算撒谎。
IMAGE_TOKEN_ESTIMATE = 120


def _history_tokens(messages: list[dict]) -> int:
    """历史消息的 token 成本。口径沿用旧的 `take_recent_messages`（已按 R9 删除）。

    ⚠️ 带图的消息 content 是**数组**。直接 `str()` 会得到几千个字符的假数字，
    面板的预算就变成撒谎 ✗。图片按固定值估，文字部分照常估。
    """
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            total += IMAGE_TOKEN_ESTIMATE
            text = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            total += estimate_tokens(text)
        else:
            total += estimate_tokens(str(content))
        total += _HISTORY_MESSAGE_OVERHEAD
    return total


def _render_history(messages: list[dict]) -> str:
    """把历史消息渲染成面板可读的文本（仅用于展示与预览，不发给模型）。

    ⚠️ 说话人用**当前人格的名字**，不写死代词（设计裁决 R3）。
    这个项目会分发给别人、人格由每个人自己配 —— 写死"他/她"，
    换个人格就错。
    """
    lines = ["【最近聊天】"]
    if not messages:
        lines.append("- 无")
        return "\n".join(lines)

    speaker = persona_display_name(active_persona_name())
    for message in messages:
        who = speaker if message.get("role") == "assistant" else "你"
        content = message.get("content", "")
        if not isinstance(content, str):
            # 带图的消息 content 是数组。面板里只写一句占位 ——
            # 图既上不了面板，也不该上（它只在每次真实请求里发给模型）。
            content = "（一张图片）"
        lines.append(f"- {who}：{content}")
    return "\n".join(lines)


def boundary_notice(blocks: list[ContextBlock]) -> str | None:
    """被排除内容的边界说明（CONTEXT_CONTROL_PLAN §5）。

    "关掉一条内容" ≠ "她应该假装从来没听过"。必须告诉她这段她看不到，
    否则她会**编造**——那正是记忆设计原则 3 要禁止的。
    """
    excluded = [b for b in blocks if b.is_excluded]
    if not excluded:
        return None

    labels = "、".join(b.label for b in excluded)
    return (
        "【不可见内容】\n"
        f"本次对话中，以下内容被用户设为不发送：{labels}。\n"
        "如果你被问到相关内容，直接说不清楚，不要推测。"
    )


@dataclass
class ContextPlan:
    """一次请求的完整上下文计划（S2）。

    同时供两条链路使用：chat（真正发送）与 preview（面板展示）。
    两者走**同一套**选择逻辑，所以面板显示的"合计"与实际请求一致。
    """

    system_prompt: str
    history: list[dict]
    system_blocks: list[ContextBlock]  # 全部 system 块（含被排除的，供面板灰显）
    history_block: ContextBlock
    kept_system_ids: set[str]
    history_dropped: bool
    total_tokens: int
    budget: int

    @property
    def over_budget(self) -> bool:
        return self.total_tokens > self.budget


def plan_context(
    system_blocks: list[ContextBlock],
    history_messages: list[dict] | None = None,
    budget: int | None = None,
    exclude_history: bool = False,
) -> ContextPlan:
    """统一预算下选择要发送的内容。

    丢弃顺序严格按 DEVELOPMENT_PLAN §1.5 决定 3：优先级低 → 高。
    system 块**整块**丢弃；历史消息**按条**丢弃（从最旧的开始）——
    两者都是"整个单位"丢弃，都不会把一句话切成两半。
    人格与规则最后丢：真到那一步宁可略超预算，也不丢、不切。
    """
    limit = CONTEXT_TOTAL_BUDGET if budget is None else budget
    active = [b for b in system_blocks if b.state == STATE_INCLUDE]

    original_history = [dict(m) for m in (history_messages or [])]
    history = [] if exclude_history else [dict(m) for m in original_history]
    had_history = bool(original_history)

    dropped_ids: set[str] = set()
    total = sum(b.tokens for b in active) + _history_tokens(history)

    while total > limit:
        # 每轮挑"当前优先级最低、且还没被丢"的一项，丢一个单位。
        candidates: list[tuple[int, str, ContextBlock | None]] = [
            (b.priority, b.id, b) for b in active if b.id not in dropped_ids
        ]
        if history:
            candidates.append((PRIORITY_HISTORY, HISTORY_BLOCK_ID, None))
        candidates.sort(key=lambda item: (item[0], item[1]))

        progressed = False
        for _priority, _key, block in candidates:
            if block is None:
                removed = history.pop(0)  # 历史：丢最旧的一条
                total -= (
                    estimate_tokens(str(removed.get("content", "")))
                    + _HISTORY_MESSAGE_OVERHEAD
                )
                progressed = True
                break
            if block.kind in _NEVER_DROP_KINDS:
                continue
            dropped_ids.add(block.id)
            total -= block.tokens
            progressed = True
            break

        if not progressed:
            # 只剩人格与规则：宁超预算，也不丢、不切。
            break

    kept = [b for b in active if b.id not in dropped_ids]
    parts = [b.content for b in kept]

    # 被排除的块保留在面板里（灰掉），内容用 original_history 渲染，
    # 但 tokens 记 0——它确实不会发出去。
    history_block = ContextBlock(
        id=HISTORY_BLOCK_ID,
        kind=KIND_HISTORY,
        label="最近聊天",
        content=_render_history(original_history),
        tokens=0 if exclude_history else _history_tokens(history),
        source=f"{len(history)} / {len(original_history)} 条消息",
        state=STATE_EXCLUDE_ALWAYS if exclude_history else STATE_INCLUDE,
        priority=PRIORITY_HISTORY,
    )

    notice = boundary_notice([*system_blocks, history_block])
    if notice:
        parts.append(notice)

    return ContextPlan(
        system_prompt="\n\n".join(parts),
        history=history,
        system_blocks=list(system_blocks),
        history_block=history_block,
        kept_system_ids={b.id for b in kept},
        history_dropped=had_history and not history,
        total_tokens=total,
        budget=limit,
    )


def plan_for_request(
    history_messages: list[dict] | None = None,
    budget: int | None = None,
    query: str | None = None,
) -> ContextPlan:
    """chat 与 preview 共用的入口：应用排除状态 + 统一预算。

    system prompt 与"最近聊天"来自同一次选择，所以面板看到的一定就是
    下一次真实请求会发送的内容。
    """
    excluded = context_exclusion_service.list_excluded_ids()
    return plan_context(
        resolve_blocks(excluded, query),
        history_messages,
        budget,
        exclude_history=HISTORY_BLOCK_ID in excluded,
    )


def assemble(blocks: list[ContextBlock], budget: int | None = None) -> str:
    """仅拼 system prompt（不含最近聊天）。

    S1 的入口；S2 起 chat 走 ``plan_for_request``（含最近聊天与统一预算）。
    默认预算保持 S1 语义（``CONTEXT_SYSTEM_BUDGET``）。
    """
    limit = CONTEXT_SYSTEM_BUDGET if budget is None else budget
    return plan_context(blocks, [], limit).system_prompt
