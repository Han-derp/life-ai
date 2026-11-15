"""她的规则（DEVELOPMENT_PLAN v2.0 · S7）。

规则是一个**你能直接操作的实体**，不是她的内部状态（MEMORY_DESIGN §5.4-B）。
四条硬规则（§5.4）：

1. 聊天改的规则**必须显示来源原话**（``source_text``）——你才看得出她有没有理解错
2. 必须有**期限**（``expires_at``）："我今天不想被打扰" ≠ "以后都别打扰"
3. **AI 不能凭空新增规则**，只能从你的原话提取 → 从聊天来的规则没有原话就不许建
4. **界面手改的（manual）优先于她从聊天理解的（chat）**

第 4 条落在 :func:`apply_chat_rule`：撞上人工规则时它**不会覆盖**，
而是如实报告"被人工规则挡住了"。于是"AI 改不掉你定过的规矩"
不是靠 prompt 约束，而是靠代码保证——这是这一层存在的意义。
"""

from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import (
    local_datetime_to_utc_iso,
    local_expiry_to_utc,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.schemas.rule import RuleCreate, RuleFromChatRequest, RuleUpdate

RULE_SOURCES = ("manual", "chat")
RULE_STATUSES = ("active", "paused", "revoked")


def _now_iso() -> str:
    return to_utc_iso(utc_now())


def _normalize_expiry(value: str | None) -> str | None:
    """把"期限"归一化成 UTC。

    **委托给 ``time_utils.local_expiry_to_utc``**：规则和承诺的"期限"语义
    必须一模一样（尤其是"传日期＝当日有效"那条），各写一份迟早会漂移。

    - ``"2026-06-01"``  —— **当日有效**（到次日 00:00 当地时间为止）
    - 具体时刻          —— 到那一刻为止
    - ``None`` / 空串   —— 永久
    """
    try:
        return local_expiry_to_utc(value, APP_TIMEZONE)
    except ValueError as exc:
        raise DomainValidationError(f"期限格式无效：{value!r}") from exc


def _normalize_effective_from(value: str | None) -> str:
    if value is None or not value.strip():
        return _now_iso()
    try:
        return local_datetime_to_utc_iso(value.strip(), APP_TIMEZONE)
    except ValueError as exc:
        raise DomainValidationError("生效时间格式无效。") from exc


def _row_to_dict(row, now: str | None = None) -> dict:
    reference = now or _now_iso()
    expires_at = row["expires_at"]
    effective_from = row["effective_from"]

    expired = expires_at is not None and expires_at <= reference
    not_started = effective_from > reference

    return {
        "id": row["id"],
        "content": row["content"],
        "source_text": row["source_text"],
        "source": row["source"],
        "effective_from": effective_from,
        "effective_from_local": utc_to_local_iso(effective_from, APP_TIMEZONE),
        "expires_at": expires_at,
        "expires_at_local": (
            utc_to_local_iso(expires_at, APP_TIMEZONE) if expires_at else None
        ),
        "status": row["status"],
        "note": row["note"],
        "is_permanent": expires_at is None,
        # 生效与否**实时算**，不落库——和 V1 的 upcoming/overdue 一个道理。
        "in_effect": row["status"] == "active" and not expired and not not_started,
    }


def create_rule(data: RuleCreate, source: str = "manual") -> dict:
    if source not in RULE_SOURCES:
        raise DomainValidationError(f"不支持的创建方式：{source}")

    content = data.content.strip()
    if not content:
        raise DomainValidationError("规则内容不能为空。")

    now = _now_iso()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO her_rules (
                content, source_text, source, effective_from, expires_at,
                status, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                content,
                (data.source_text or "").strip() or None,
                source,
                _normalize_effective_from(data.effective_from_local),
                _normalize_expiry(data.expires_at_local),
                (data.note or "").strip() or None,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM her_rules WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return _row_to_dict(row)


def list_rules(include_inactive: bool = True) -> list[dict]:
    """规则列表。

    默认**包含**已失效 / 已撤销的——"我关过什么"要可查（和 S2 的排除同理）。
    ``include_inactive=False`` 时才只给当前生效的。
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM her_rules ORDER BY id DESC").fetchall()

    now = _now_iso()
    rules = [_row_to_dict(row, now) for row in rows]
    if include_inactive:
        return rules
    return [rule for rule in rules if rule["in_effect"]]


def get_rule(rule_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM her_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise NotFoundError("规则不存在。")
    return _row_to_dict(row)


def update_rule(rule_id: int, data: RuleUpdate) -> dict:
    """界面手改。

    **改过之后这条规则就归你管**：``source`` 被改成 ``manual``，
    从此她从聊天里理解出的东西再也覆盖不了它（硬规则 4）。
    """
    provided = data.model_fields_set

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM her_rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise NotFoundError("规则不存在。")

        content = row["content"]
        if "content" in provided and data.content is not None:
            content = data.content.strip()
            if not content:
                raise DomainValidationError("规则内容不能为空。")

        source_text = row["source_text"]
        if "source_text" in provided:
            source_text = (data.source_text or "").strip() or None

        expires_at = row["expires_at"]
        if "expires_at_local" in provided:
            expires_at = _normalize_expiry(data.expires_at_local)

        status = row["status"]
        if "status" in provided and data.status is not None:
            if data.status not in RULE_STATUSES:
                raise DomainValidationError(f"不支持的状态：{data.status}")
            status = data.status

        note = row["note"]
        if "note" in provided:
            note = (data.note or "").strip() or None

        conn.execute(
            """
            UPDATE her_rules
            SET content = ?, source_text = ?, source = 'manual',
                expires_at = ?, status = ?, note = ?, updated_at = ?
            WHERE id = ?
            """,
            (content, source_text, expires_at, status, note, _now_iso(), rule_id),
        )
        updated = conn.execute(
            "SELECT * FROM her_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return _row_to_dict(updated)


def delete_rule(rule_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM her_rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise NotFoundError("规则不存在。")
        conn.execute("DELETE FROM her_rules WHERE id = ?", (rule_id,))


def apply_chat_rule(data: RuleFromChatRequest) -> dict:
    """她从聊天里理解出的一条规则。

    **没有来源原话就不许建**（硬规则 3）——她只能从你的原话提取，不能推断。

    **界面手改优先**（硬规则 4）：如果已经有一条内容相同的人工规则，
    这里不会覆盖它，而是返回 ``overridden_by_manual=True``，
    交给上层决定要不要告诉你"她想改你定过的规矩，但被挡住了"。
    """
    if not (data.source_text or "").strip():
        raise DomainValidationError(
            "从聊天提取的规则必须带来源原话——没有原话就不许新增规则（硬规则 3）。"
        )

    content = data.content.strip()
    if not content:
        raise DomainValidationError("规则内容不能为空。")

    with get_connection() as conn:
        manual = conn.execute(
            """
            SELECT * FROM her_rules
            WHERE source = 'manual' AND content = ? AND status <> 'revoked'
            ORDER BY id ASC
            LIMIT 1
            """,
            (content,),
        ).fetchone()

    if manual is not None:
        return {
            "rule": _row_to_dict(manual),
            "created": False,
            "overridden_by_manual": True,
        }

    rule = create_rule(
        RuleCreate(
            content=content,
            source_text=data.source_text,
            expires_at_local=data.expires_at_local,
        ),
        source="chat",
    )
    return {"rule": rule, "created": True, "overridden_by_manual": False}
