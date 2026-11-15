"""主动联系（DEVELOPMENT_PLAN v2.0 · S8）。

两套机制**必须分开**（MEMORY_DESIGN §5.2）——混在一起，她会显得"越没人理越粘"：

| | 机制一：惦记 | 机制二：留存 |
|---|---|---|
| 触发 | 3 天 → 7 天 | 满 1 个月 |
| 目的 | **她关心你** | **你回来时有个东西接住你** |
| 上限 | **2 次，然后彻底安静** | 可以继续，但形态不同 |
| 能不能催 | **绝对不可以** | 不催，只呈现 |

核心规矩：**"惦记"的力量来自它会停。** 一个会停下来的惦记才是真的；
不会停的，那是运营。所以 ``silence_3d`` + ``silence_7d`` 之后**绝不会有第三次**。

判断"这次沉默期里已经发过什么"**不需要额外状态表**：
``created_at > 最后一条用户消息的时间`` 就属于当前沉默期。
你回一句话，沉默期自然结束、计数器自然归零。

另一条硬约束由**代码**保证而不是 prompt：:data:`FORBIDDEN_PHRASES`。
模型偶尔就是会写出"你怎么不见了"，而那句话一旦出现，整套设计就崩了——
所以生成完必须过 :func:`sanitize`。
"""

import json
from datetime import date, datetime

from app.core.config import APP_TIMEZONE
from app.ai.context.assembler import plan_for_request
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import local_date_of, local_day_range_utc, to_utc_iso
from app.database.connection import get_connection
from app.llm import factory
from app.scheduler import clock
from app.services import chat_service
from app.workflows import tone

# 沉默分级（MEMORY_DESIGN §5.2）
SILENCE_SHORT_DAYS = 3
SILENCE_LONG_DAYS = 7
ESSAY_AFTER_DAYS = 30

# 日常惦记的时刻（当地时间，分钟）
CARE_SLOTS = (("noon", 13 * 60), ("evening", 20 * 60))
# 连续这么多次没回 → 降频为一天一次
CARE_DOWNGRADE_AFTER = 2

KIND_CARE = "care"
KIND_SILENCE_SHORT = "silence_3d"
KIND_SILENCE_LONG = "silence_7d"
KIND_ESSAY = "essay"

# 语气底线（禁止项 + 兜底文案）住在 `workflows/tone.py`：
# S8 的**两套**主动开口（主动联系 + 基线预警）共用同一份，各写一份迟早漂移。
FORBIDDEN_PHRASES = tone.FORBIDDEN_PHRASES
FALLBACK_TEXT = tone.FALLBACK_TEXT

# 她主动开口也是"她在说话"，所以走 `chat`。
_provider = factory.current_provider("chat")


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "content": row["content"],
        "slot": row["slot"],
        "created_at": row["created_at"],
        "delivered_at": row["delivered_at"],
        "responded_at": row["responded_at"],
        "delivered": row["delivered_at"] is not None,
    }


# ---------- 读 ----------


def latest_user_message_at() -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT created_at FROM messages
            WHERE role = 'user'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return row["created_at"] if row else None


def list_contacts(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM proactive_contacts
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def pending_contacts() -> list[dict]:
    """还没给你看过的东西（目前只有机制二的小作文）。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM proactive_contacts
            WHERE delivered_at IS NULL
            ORDER BY id ASC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _has_contact_since(kind: str, since_iso: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM proactive_contacts
            WHERE kind = ? AND created_at > ?
            LIMIT 1
            """,
            (kind, since_iso),
        ).fetchone()
    return row is not None


def _care_sent_today(slot: str, moment: datetime) -> bool:
    day = clock.local_date(moment).isoformat()
    start_utc, end_utc = local_day_range_utc(day, APP_TIMEZONE)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM proactive_contacts
            WHERE kind = ? AND slot = ?
              AND created_at >= ? AND created_at < ?
            LIMIT 1
            """,
            (KIND_CARE, slot, start_utc, end_utc),
        ).fetchone()
    return row is not None


def _care_unanswered_streak() -> int:
    """最近几次日常惦记里，有多少次是没被回的。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT responded_at FROM proactive_contacts
            WHERE kind = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (KIND_CARE, CARE_DOWNGRADE_AFTER),
        ).fetchall()
    return sum(1 for row in rows if row["responded_at"] is None)


def _silent_days(moment: datetime, last_user_iso: str) -> int:
    """按**当地自然日**算沉默天数。

    用自然日而不是 24 小时：你昨天深夜说过话，今天就不该被"惦记"。
    """
    today = clock.local_date(moment)
    last_day = date.fromisoformat(local_date_of(last_user_iso, APP_TIMEZONE))
    return (today - last_day).days


def _due_care_slot(moment: datetime) -> tuple[str, int] | None:
    local = clock.to_local(moment)
    minute = local.hour * 60 + local.minute
    due = None
    for name, slot_minute in CARE_SLOTS:
        if minute >= slot_minute:
            due = (name, slot_minute)
    return due


def _refresh_responses(moment: datetime) -> None:
    """把"她说过之后你又说过话"的主动联系标成已回应。

    不需要在聊天链路里挂钩子——**"你回过话"从数据本身就能推出来**：
    任何早于最后一条用户消息的主动联系，都已经算被回应了。
    """
    last_user = latest_user_message_at()
    if last_user is None:
        return
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE proactive_contacts
            SET responded_at = ?
            WHERE responded_at IS NULL AND created_at < ?
            """,
            (last_user, last_user),
        )


# ---------- 文案安全（硬约束） ----------


def sanitize(text: str, kind: str) -> str | None:
    """把生成出来的话过一遍禁止项。

    实现在 `workflows/tone.py`——**代码强制，不是 prompt 建议**。
    模型偶尔就是会写出"你怎么不见了"，而那句话会让"惦记"变成"考勤"。

    小作文没有兜底文案可退，``None`` 表示**这条不发**。
    """
    return tone.sanitize(text, kind)


def _generate(kind: str, silent_days: int, moment: datetime) -> str | None:
    if not _provider.available:
        # 小作文没法用罐头文案顶替：宁可先不写，也不要给一段假的东西。
        return None if kind == KIND_ESSAY else FALLBACK_TEXT.get(kind)

    instruction = load_prompt("proactive.md").format(
        kind=kind,
        silent_days=silent_days,
        local_time=clock.to_local(moment).isoformat(timespec="minutes"),
    )

    # ⚠️ **必须走同一套上下文**（设计裁决 R2①）。
    # 如果主动联系自己拼一个短 prompt，她会"在主动找你的时候显得特别空" ——
    # 而那时恰恰是你最需要她像个人的时刻。
    #
    # 顺带一个好处：S2 的排除规则对她同样生效 ——
    # 你在面板里关掉的块，她主动找你时也看不到。
    #
    # 注意这里**刻意不调用** `context_exclusion_service.consume_once_ids()`：
    # `exclude_once` 的语义是"下次**聊天**不发"，她主动说话不该把它花掉。
    # （不消费 = 下次聊天仍然遵守，方向是安全的。）
    plan = plan_for_request(chat_service.recent_history())

    messages = [{"role": "system", "content": plan.system_prompt}]
    messages.extend(plan.history)
    messages.append({"role": "user", "content": instruction})

    try:
        raw = _provider.chat(messages, temperature=1.0)
    except ExternalServiceError:
        return None if kind == KIND_ESSAY else FALLBACK_TEXT.get(kind)

    return sanitize(raw, kind)


# ---------- 决策 ----------


def next_action(moment: datetime | None = None) -> dict | None:
    """现在该做什么。``None`` = 什么都不该做。

    只做**决策**；唯一的副作用是把已回应的记录标一下（幂等）。
    """
    now = moment or clock.now_utc()
    _refresh_responses(now)

    last_user = latest_user_message_at()
    if last_user is None:
        # 从没说过话：没什么可惦记的，也不能凭空找上门。
        return None

    silent_days = _silent_days(now, last_user)

    # ---- 机制二：你回来了，把没给过的小作文给你看 ----
    if silent_days < SILENCE_SHORT_DAYS:
        pending = pending_contacts()
        if pending:
            return {"action": "deliver_essay", "contact": pending[0]}

    # ---- 机制一：沉默升级（两个加起来最多 2 次） ----
    if silent_days >= ESSAY_AFTER_DAYS:
        if not _has_contact_since(KIND_ESSAY, last_user):
            return {"action": "write_essay", "silent_days": silent_days}
        return None  # 写过了。彻底安静，等她回来

    if silent_days >= SILENCE_LONG_DAYS:
        if not _has_contact_since(KIND_SILENCE_LONG, last_user):
            return {
                "action": "send",
                "kind": KIND_SILENCE_LONG,
                "silent_days": silent_days,
            }
        return None  # 第 2 次之后彻底安静——**绝不发第三次**

    if silent_days >= SILENCE_SHORT_DAYS:
        if not _has_contact_since(KIND_SILENCE_SHORT, last_user):
            return {
                "action": "send",
                "kind": KIND_SILENCE_SHORT,
                "silent_days": silent_days,
            }
        return None

    # ---- 活跃期：日常惦记（午 / 晚各一次） ----
    slot = _due_care_slot(now)
    if slot is not None and not _care_sent_today(slot[0], now):
        if slot[0] == "noon" and _care_unanswered_streak() >= CARE_DOWNGRADE_AFTER:
            # 连续两次没回 → 降频：今天只留晚上那一次
            return None
        return {
            "action": "send",
            "kind": KIND_CARE,
            "slot": slot[0],
            "silent_days": silent_days,
        }

    return None


# ---------- 执行 ----------


def _insert(kind: str, content: str, slot: str | None, moment: datetime, delivered: bool) -> dict:
    now = to_utc_iso(moment)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO proactive_contacts (
                kind, content, slot, created_at, delivered_at, responded_at
            ) VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (kind, content, slot, now, now if delivered else None),
        )
        row = conn.execute(
            "SELECT * FROM proactive_contacts WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        result = _row_to_dict(row)

    if delivered:
        # 现在就给她看的那些，也写进聊天记录（设计裁决 R2③），并带 mechanism 标记。
        # ⚠️ 小作文（delivered=False）**不能**在这里写 ——
        # 它的规矩是"写下来，但先不给你看"，提前落进聊天记录就破坏机制二了。
        # 它要等到 deliver_contact() 那一刻才变成"她说过的话"。
        chat_service.record_proactive_message(content, mechanism=kind)

    return result


def _send(kind: str, slot: str | None, silent_days: int, moment: datetime) -> dict | None:
    content = _generate(kind, silent_days, moment)
    if not content:
        return None
    return _insert(kind, content, slot, moment, delivered=True)


def _write_essay(silent_days: int, moment: datetime) -> dict | None:
    """机制二：写小作文，但**先不给**——等你回来才展示，从不推送。"""
    content = _generate(KIND_ESSAY, silent_days, moment)
    if not content:
        return None
    return _insert(KIND_ESSAY, content, None, moment, delivered=False)


def deliver_contact(contact_id: int, moment: datetime | None = None) -> dict:
    """把一条主动联系标成"已经给你看过了"。幂等。

    小作文**在这里**才写进聊天记录 —— 这是它第一次出现在你眼前。
    """
    now = to_utc_iso(moment or clock.now_utc())
    first_delivery = False

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM proactive_contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("这条主动联系不存在。")

        if row["delivered_at"] is None:
            conn.execute(
                "UPDATE proactive_contacts SET delivered_at = ? WHERE id = ?",
                (now, contact_id),
            )
            first_delivery = True

        updated = conn.execute(
            "SELECT * FROM proactive_contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        result = _row_to_dict(updated)

    if first_delivery:
        chat_service.record_proactive_message(result["content"], mechanism=result["kind"])

    return result


def tick(moment: datetime | None = None) -> dict | None:
    """走一次：决策并执行。返回做了什么；``None`` 表示这次什么都不该做。"""
    now = moment or clock.now_utc()
    decision = next_action(now)

    if decision is None:
        return None
    if decision["action"] == "deliver_essay":
        return deliver_contact(decision["contact"]["id"], now)
    if decision["action"] == "write_essay":
        return _write_essay(decision["silent_days"], now)

    return _send(
        decision["kind"],
        decision.get("slot"),
        decision["silent_days"],
        now,
    )
