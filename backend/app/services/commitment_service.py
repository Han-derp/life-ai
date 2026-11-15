"""承诺（DEVELOPMENT_PLAN v2.0 · S7 · 关系记忆 LR）。

承诺 = **她答应过你什么**。这是关系记忆里最该先做的一项，因为
**一个能被验证守约的恋人，比一个记忆更全的恋人更像人**（MEMORY_DESIGN §4.5）。

三件事让"可验证"成立：

1. ``source_text`` —— 你的原话。她要答应什么，得先有你那句话
2. ``structured``  —— 可执行的结构。目前只支持 ``quiet_hours``：
   她承诺在这段时间**不主动找你**
3. :func:`violations_for` —— 拿"她主动找你"的时刻去核对，越界即违约

⚠️ 承诺是"**不主动联系**"，不是"不理你"：**你自己来找她时她的回复不算违约**。
所以这个检查器只应该喂**主动联系**的时刻（S8 的主动联系会产生它们）。
现在就把检查器写好并用测试锁住，是因为"可验证"这个承诺本身不该等到 S8。
"""

import json
from datetime import time
from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import (
    local_datetime_to_utc_iso,
    local_expiry_to_utc,
    parse_utc,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.schemas.commitment import (
    CommitmentCreate,
    CommitmentFromChatRequest,
    CommitmentReviseRequest,
)

QUIET_HOURS = "quiet_hours"


def _now_iso() -> str:
    return to_utc_iso(utc_now())


def _format_hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_hhmm(value) -> int:
    try:
        parsed = time.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise DomainValidationError(f"时间格式无效：{value!r}（应为 HH:MM）") from exc
    return parsed.hour * 60 + parsed.minute


def _normalize_expiry(value: str | None) -> str | None:
    try:
        return local_expiry_to_utc(value, APP_TIMEZONE)
    except ValueError as exc:
        raise DomainValidationError(f"期限格式无效：{value!r}") from exc


def _normalize_structured(raw) -> dict | None:
    """校验并归一化"可执行结构"。

    做成**白名单**而不是"随便塞 JSON"：只有能被程序检查的结构，
    才配叫"可验证"。目前只支持一件事——她承诺某段时间不主动找你：

    - ``{"quiet_hours": {"start": "23:00", "end": "08:00"}}``  跨午夜
    - ``{"quiet_hours": {"start": "23:00", "end": null}}``      当天 23:00 之后
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DomainValidationError("structured 必须是对象。")
    if not raw:
        return None

    unknown = sorted(set(raw) - {QUIET_HOURS})
    if unknown:
        raise DomainValidationError(
            f"不认识的结构化字段：{'、'.join(unknown)}。目前只支持 {QUIET_HOURS}。"
        )

    window = raw.get(QUIET_HOURS)
    if not isinstance(window, dict):
        raise DomainValidationError("quiet_hours 必须是对象。")

    start_raw = window.get("start")
    if not start_raw:
        raise DomainValidationError("quiet_hours 必须有 start，例如 23:00。")
    start_minute = _parse_hhmm(start_raw)

    end_raw = window.get("end")
    end_minute = _parse_hhmm(end_raw) if end_raw else None

    return {
        QUIET_HOURS: {
            "start": _format_hhmm(start_minute),
            "end": _format_hhmm(end_minute) if end_minute is not None else None,
        }
    }


def _within_quiet_hours(local_time: time, window: dict) -> bool:
    """给定的**当地时刻**是否落在"别找我"的窗口里。

    - ``end`` 为空   → 从 ``start`` 到当天结束
    - ``end <= start`` → 跨午夜（例如 23:00 → 08:00）
    """
    minute = local_time.hour * 60 + local_time.minute
    start = _parse_hhmm(window["start"])
    end_raw = window.get("end")

    if end_raw is None:
        return minute >= start

    end = _parse_hhmm(end_raw)
    if end <= start:
        return minute >= start or minute < end
    return start <= minute < end


def _window_text(structured) -> str | None:
    if not structured or QUIET_HOURS not in structured:
        return None
    window = structured[QUIET_HOURS]
    start = window["start"]
    end = window.get("end")
    if end is None:
        return f"{start} 之后不主动找你"
    return f"{start} – {end} 不主动找你"


def _row_to_dict(row, now: str | None = None) -> dict:
    reference = now or _now_iso()
    expires_at = row["expires_at"]
    effective_from = row["effective_from"]
    structured = json.loads(row["structured_json"]) if row["structured_json"] else None

    expired = expires_at is not None and expires_at <= reference
    not_started = effective_from > reference

    return {
        "id": row["id"],
        "content": row["content"],
        "source_text": row["source_text"],
        "structured": structured,
        "window_text": _window_text(structured),
        "effective_from": effective_from,
        "effective_from_local": utc_to_local_iso(effective_from, APP_TIMEZONE),
        "expires_at": expires_at,
        "expires_at_local": (
            utc_to_local_iso(expires_at, APP_TIMEZONE) if expires_at else None
        ),
        "status": row["status"],
        "created_via": row["created_via"],
        "superseded_by": row["superseded_by"],
        "is_permanent": expires_at is None,
        # 生效与否实时算，不落库
        "in_effect": row["status"] == "active" and not expired and not not_started,
    }


def create_commitment(data: CommitmentCreate, created_via: str = "manual") -> dict:
    if created_via not in ("chat", "manual"):
        raise DomainValidationError(f"不支持的创建方式：{created_via}")

    content = data.content.strip()
    if not content:
        raise DomainValidationError("承诺内容不能为空。")

    structured = _normalize_structured(data.structured)
    now = _now_iso()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO commitments (
                content, source_text, structured_json, effective_from, expires_at,
                status, created_via, superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?)
            """,
            (
                content,
                (data.source_text or "").strip() or None,
                json.dumps(structured, ensure_ascii=False) if structured else None,
                _normalize_effective_from(data.effective_from_local),
                _normalize_expiry(data.expires_at_local),
                created_via,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return _row_to_dict(row)


def _normalize_effective_from(value: str | None) -> str:
    if value is None or not value.strip():
        return _now_iso()
    try:
        return local_datetime_to_utc_iso(value.strip(), APP_TIMEZONE)
    except ValueError as exc:
        raise DomainValidationError(f"生效时间格式无效：{value!r}") from exc


def list_commitments(include_inactive: bool = True) -> list[dict]:
    """承诺列表。默认包含已改口 / 已撤销的——"她改过什么口"也是关系记忆。"""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM commitments ORDER BY id DESC").fetchall()

    now = _now_iso()
    items = [_row_to_dict(row, now) for row in rows]
    if include_inactive:
        return items
    return [item for item in items if item["in_effect"]]


def get_commitment(commitment_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (commitment_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("承诺不存在。")
    return _row_to_dict(row)


def revise_commitment(commitment_id: int, data: CommitmentReviseRequest) -> dict:
    """改一条承诺：**生成新版本，旧版本永久保留**（MEMORY_DESIGN §4.5）。

    旧条 → ``status='changed'`` + ``superseded_by``；新条继承没被改动的字段。
    这和印象的代际是同一套思路——"她改过口"本身就是关系记忆的一部分。
    """
    provided = data.model_fields_set
    now = _now_iso()

    with get_connection() as conn:
        old = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (commitment_id,),
        ).fetchone()
        if old is None:
            raise NotFoundError("承诺不存在。")
        if old["status"] == "changed":
            raise DomainValidationError("这一版承诺已经被取代，不能再改。")

        content = old["content"]
        if "content" in provided and data.content is not None:
            content = data.content.strip()
            if not content:
                raise DomainValidationError("承诺内容不能为空。")

        source_text = old["source_text"]
        if "source_text" in provided:
            source_text = (data.source_text or "").strip() or None

        structured_json = old["structured_json"]
        if "structured" in provided:
            normalized = _normalize_structured(data.structured)
            structured_json = (
                json.dumps(normalized, ensure_ascii=False) if normalized else None
            )

        expires_at = old["expires_at"]
        if "expires_at_local" in provided:
            expires_at = _normalize_expiry(data.expires_at_local)

        cursor = conn.execute(
            """
            INSERT INTO commitments (
                content, source_text, structured_json, effective_from, expires_at,
                status, created_via, superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', 'manual', NULL, ?, ?)
            """,
            (
                content,
                source_text,
                structured_json,
                old["effective_from"],
                expires_at,
                now,
                now,
            ),
        )
        new_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE commitments
            SET status = 'changed', superseded_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_id, now, commitment_id),
        )

        row = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (new_id,),
        ).fetchone()
        return _row_to_dict(row)


def revoke_commitment(commitment_id: int) -> dict:
    now = _now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (commitment_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("承诺不存在。")

        conn.execute(
            """
            UPDATE commitments
            SET status = 'revoked', updated_at = ?
            WHERE id = ?
            """,
            (now, commitment_id),
        )
        updated = conn.execute(
            "SELECT * FROM commitments WHERE id = ?",
            (commitment_id,),
        ).fetchone()
        return _row_to_dict(updated)


def apply_chat_commitment(data: CommitmentFromChatRequest) -> dict:
    """她从聊天里答应你的一件事。

    ``source_text``（你的原话）**必填**。撞上同内容的人工承诺时不覆盖，
    只报告 ``overridden_by_manual``——和规则是同一套"界面手改优先"。
    """
    if not (data.source_text or "").strip():
        raise DomainValidationError("从聊天来的承诺必须带你的原话。")

    content = data.content.strip()
    if not content:
        raise DomainValidationError("承诺内容不能为空。")

    with get_connection() as conn:
        manual = conn.execute(
            """
            SELECT * FROM commitments
            WHERE created_via = 'manual' AND content = ? AND status <> 'revoked'
            ORDER BY id ASC
            LIMIT 1
            """,
            (content,),
        ).fetchone()

    if manual is not None:
        return {
            "commitment": _row_to_dict(manual),
            "created": False,
            "overridden_by_manual": True,
        }

    commitment = create_commitment(
        CommitmentCreate(
            content=content,
            source_text=data.source_text,
            structured=data.structured,
            expires_at_local=data.expires_at_local,
        ),
        created_via="chat",
    )
    return {
        "commitment": commitment,
        "created": True,
        "overridden_by_manual": False,
    }


def violations_for(commitment_id: int, moments: list[str]) -> dict:
    """核对这条承诺有没有被她违反。

    ``moments`` 是**她主动找你**的 UTC 时间戳（S8 的主动联系会产生它们）。
    窗口按**当地时间**判断，所以这里会先转成当地时刻。

    ⚠️ 再强调一次：**你自己来找她时她的回复不算违约**。所以调用方只能喂
    主动联系的时刻，别把 `messages` 里的 assistant 回复当成"她来找你"。
    """
    commitment = get_commitment(commitment_id)
    structured = commitment["structured"]

    if not structured or QUIET_HOURS not in structured:
        return {
            "commitment_id": commitment_id,
            "checkable": False,
            "window": None,
            "checked": 0,
            "violations": [],
        }

    window = structured[QUIET_HOURS]
    zone = ZoneInfo(APP_TIMEZONE)
    violations = []

    for moment in moments:
        try:
            parsed = parse_utc(moment)
        except ValueError as exc:
            raise DomainValidationError(f"时间格式无效：{moment!r}") from exc

        local = parsed.astimezone(zone)
        if _within_quiet_hours(local.time(), window):
            violations.append(
                {
                    "moment": moment,
                    "moment_local": local.isoformat(timespec="minutes"),
                }
            )

    return {
        "commitment_id": commitment_id,
        "checkable": True,
        "window": window,
        "checked": len(moments),
        "violations": violations,
    }


# ---------- 接上真实数据（S8 落地之后） ----------

# 哪些主动联系算"她来找你"。
# **不含 essay**：小作文是写下来、等你回来才看的，不是她主动找上门；
# 把它算进来就是冤枉她。
CONTACT_KINDS = ("care", "silence_3d", "silence_7d")


def moments_from_proactive() -> list[str]:
    """"她主动找你"的真实时刻（S8 的 ``proactive_contacts``）。

    这就是 S7 那条"违规核对还没有数据源"的解法——数据现在有了。
    """
    placeholders = ", ".join("?" for _ in CONTACT_KINDS)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT created_at FROM proactive_contacts
            WHERE kind IN ({placeholders})
            ORDER BY created_at ASC
            """,
            CONTACT_KINDS,
        ).fetchall()
    return [row["created_at"] for row in rows]


def violations_against_proactive(commitment_id: int) -> dict:
    """拿她**真实的主动联系记录**核对这条承诺有没有被违反。

    ⚠️ 再强调一次：**你自己来找她时她的回复不算违约**，
    所以这里只喂主动联系的时刻，绝不碰 `messages`。
    """
    return violations_for(commitment_id, moments_from_proactive())
