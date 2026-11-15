"""S2 上下文排除 + 统一预算。

对应 DEVELOPMENT_PLAN v2.0 · S2 的硬规则与完成判据：

- 被排除的块保留显示（灰掉），不消失
- 排除后注入「不可见内容」边界说明，让她的"不知道"是诚实的
- 超预算按 §1.5 决定 3 的顺序整块丢弃
- 总计 token 与实际请求一致
"""

import pytest

from app.ai.context import assembler
from app.ai.context.blocks import (
    KIND_PERSONA,
    KIND_TODAY,
    STATE_EXCLUDE_ALWAYS,
    STATE_EXCLUDE_ONCE,
    STATE_INCLUDE,
    ContextBlock,
)
from app.core.token_budget import estimate_tokens
from app.services import context_exclusion_service
from app.services.context_exclusion_service import (
    list_excluded_ids,
    list_once_ids,
    set_excluded,
    set_once,
)


@pytest.fixture(autouse=True)
def reset_once_exclusions():
    """exclude_once 是模块级状态，测试之间必须隔离。"""
    context_exclusion_service.clear_once_ids()
    yield
    context_exclusion_service.clear_once_ids()


def _blk(block_id: str, kind: str, content: str, priority: int) -> ContextBlock:
    return ContextBlock(
        id=block_id,
        kind=kind,
        label=block_id,
        content=content,
        tokens=estimate_tokens(content),
        priority=priority,
    )


def _excluded(block_id: str, kind: str, label: str, content: str, priority: int) -> ContextBlock:
    return ContextBlock(
        id=block_id,
        kind=kind,
        label=label,
        content=content,
        tokens=estimate_tokens(content),
        state=STATE_EXCLUDE_ALWAYS,
        priority=priority,
    )


# ---------- exclude_always（落库、可逆） ----------


def test_exclude_always_is_persisted_and_reversible():
    assert list_excluded_ids() == set()

    set_excluded("today:habits", True)
    assert list_excluded_ids() == {"today:habits"}

    set_excluded("today:habits", False)
    assert list_excluded_ids() == set()


def test_resolve_blocks_applies_exclude_always():
    set_excluded("today:timeline", True)

    blocks = {b.id: b for b in assembler.resolve_blocks()}

    assert blocks["today:timeline"].state == STATE_EXCLUDE_ALWAYS
    assert blocks["today:timeline"].is_excluded is True
    assert blocks["persona"].state == STATE_INCLUDE


def test_resolve_blocks_accepts_preloaded_ids():
    blocks = {b.id: b for b in assembler.resolve_blocks({"today:pomodoro"})}

    assert blocks["today:pomodoro"].state == STATE_EXCLUDE_ALWAYS
    assert blocks["persona"].state == STATE_INCLUDE


# ---------- exclude_once（一次性，不落库） ----------


def test_exclude_once_applies_then_is_consumed():
    set_once("today:habits", True)
    assert list_once_ids() == {"today:habits"}
    assert list_excluded_ids() == set()  # 不落库

    plan = assembler.plan_for_request([])
    blocked = {b.id: b for b in plan.system_blocks}
    assert blocked["today:habits"].state == STATE_EXCLUDE_ONCE
    assert blocked["today:habits"].is_excluded is True

    assert context_exclusion_service.consume_once_ids() == {"today:habits"}
    assert list_once_ids() == set()

    # 消费之后恢复发送
    again = {b.id: b for b in assembler.resolve_blocks()}
    assert again["today:habits"].state == STATE_INCLUDE


# ---------- 边界说明 ----------


def test_boundary_notice_lists_excluded_labels():
    blocks = [
        _blk("persona", KIND_PERSONA, "人格", assembler.PRIORITY_PERSONA),
        _excluded(
            "today:habits",
            KIND_TODAY,
            "今日习惯",
            "习惯",
            assembler.PRIORITY_TODAY_HABITS,
        ),
    ]

    notice = assembler.boundary_notice(blocks)

    assert notice is not None
    assert "今日习惯" in notice
    assert "不要推测" in notice


def test_boundary_notice_is_none_without_exclusions():
    blocks = [_blk("persona", KIND_PERSONA, "人格", assembler.PRIORITY_PERSONA)]

    assert assembler.boundary_notice(blocks) is None


def test_assemble_appends_boundary_notice_for_excluded_block():
    blocks = [
        _blk("persona", KIND_PERSONA, "人格", assembler.PRIORITY_PERSONA),
        _excluded(
            "today:timeline",
            KIND_TODAY,
            "实际时间轴",
            "时间轴内容",
            assembler.PRIORITY_TODAY_TIMELINE,
        ),
    ]

    prompt = assembler.assemble(blocks, budget=1000)

    assert "人格" in prompt
    assert "时间轴内容" not in prompt  # 内容本身不发送
    assert "【不可见内容】" in prompt  # 但告诉她这段看不到
    assert "实际时间轴" in prompt  # 用标签说明是什么看不到


# ---------- 统一预算与丢弃顺序 ----------


def test_drop_order_follows_plan_decision():
    """预算收紧时，丢弃顺序符合 DEVELOPMENT_PLAN §1.5 决定 3。"""
    blocks = [
        _blk("persona", KIND_PERSONA, "人" * 30, assembler.PRIORITY_PERSONA),
        _blk("today:schedule", KIND_TODAY, "日" * 30, assembler.PRIORITY_TODAY_SCHEDULE),
        _blk("today:pomodoro", KIND_TODAY, "番" * 30, assembler.PRIORITY_TODAY_POMODORO),
        _blk("today:habits", KIND_TODAY, "惯" * 30, assembler.PRIORITY_TODAY_HABITS),
        _blk("today:timeline", KIND_TODAY, "轴" * 30, assembler.PRIORITY_TODAY_TIMELINE),
    ]
    # 预算刚好够 persona + schedule
    budget = blocks[0].tokens + blocks[1].tokens

    plan = assembler.plan_context(blocks, [], budget=budget)

    assert plan.kept_system_ids == {"persona", "today:schedule"}


def test_history_is_dropped_before_schedule():
    """更早聊天（60）优先级低于今日日程（70），所以先被丢。"""
    persona = _blk("persona", KIND_PERSONA, "人格" * 10, assembler.PRIORITY_PERSONA)
    schedule = _blk("today:schedule", KIND_TODAY, "日程" * 10, assembler.PRIORITY_TODAY_SCHEDULE)
    history = [{"role": "user", "content": "旧话" * 5}]

    budget = persona.tokens + schedule.tokens
    plan = assembler.plan_context([persona, schedule], history, budget=budget)

    assert schedule.content in plan.system_prompt
    assert plan.history == []
    assert plan.history_dropped is True


def test_history_is_trimmed_message_by_message():
    """历史按条丢（从最旧开始），不会把一条切成半句。"""
    persona = _blk("persona", KIND_PERSONA, "人格" * 10, assembler.PRIORITY_PERSONA)
    history = [
        {"role": "user", "content": "第一句" * 5},
        {"role": "assistant", "content": "第二句" * 5},
        {"role": "user", "content": "第三句" * 5},
    ]
    budget = persona.tokens + sum(
        estimate_tokens(m["content"]) + assembler._HISTORY_MESSAGE_OVERHEAD
        for m in history[1:]
    )

    plan = assembler.plan_context([persona], history, budget=budget)

    assert [m["content"] for m in plan.history] == [m["content"] for m in history[1:]]
    assert all(m["content"].endswith("句") for m in plan.history)


def test_total_tokens_matches_selection():
    persona = _blk("persona", KIND_PERSONA, "人格" * 10, assembler.PRIORITY_PERSONA)
    history = [{"role": "user", "content": "旧话" * 5}]

    plan = assembler.plan_context([persona], history, budget=100000)

    assert plan.total_tokens == persona.tokens + assembler._history_tokens(history)
    assert plan.over_budget is False


def test_exclude_history_keeps_it_visible_but_unsent():
    persona = _blk("persona", KIND_PERSONA, "人格" * 10, assembler.PRIORITY_PERSONA)
    history = [{"role": "user", "content": "旧话" * 5}]

    plan = assembler.plan_context([persona], history, budget=100000, exclude_history=True)

    assert plan.history == []
    assert plan.history_block.is_excluded is True
    assert plan.history_block.tokens == 0  # 确实不占预算、不发送
    assert "旧话" in plan.history_block.content  # 面板里仍能看到（灰掉）
    assert "【不可见内容】" in plan.system_prompt
    assert "最近聊天" in plan.system_prompt


def test_plan_for_request_uses_persisted_exclusions():
    set_excluded("today:timeline", True)

    plan = assembler.plan_for_request([])

    assert "today:timeline" not in plan.kept_system_ids
    assert plan.system_prompt.count("【实际时间轴】") == 0
