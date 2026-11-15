from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import (
    local_today,
    minutes_from_midnight,
    normalize_time_minutes,
    to_utc_iso,
    utc_now,
)
from app.database.connection import get_connection
from app.schemas.habit import HabitCreate, HabitLogRequest


_ALLOWED_TYPES = {"boolean", "numeric", "duration", "time"}
_ALLOWED_AGG = {"sum", "latest", "max", "min"}
_ALLOWED_OP = {">=", "<=", "=", ">", "<"}


def _compare(value: float | None, target: float | None, operator: str | None) -> bool | None:
    if value is None or target is None or operator is None:
        return None
    if operator == ">=":
        return value >= target
    if operator == "<=":
        return value <= target
    if operator == "=":
        return value == target
    if operator == ">":
        return value > target
    if operator == "<":
        return value < target
    return None


def _habit_row_to_today(row) -> dict:
    tracking_type = row["tracking_type"]
    current_bool = None if row["value_bool"] is None else bool(row["value_bool"])
    current_number = row["value_number"]
    current_text = row["value_text"]

    if tracking_type == "boolean":
        met = current_bool if current_bool is not None else False
        progress = 1.0 if met else 0.0
    else:
        met = _compare(current_number, row["target_value"], row["target_operator"])
        if row["target_value"] not in (None, 0) and current_number is not None:
            if tracking_type == "time" and row["target_operator"] == "<=":
                progress = 1.0 if met else max(0.0, min(1.0, row["target_value"] / max(current_number, 1)))
            else:
                progress = max(0.0, min(1.0, current_number / row["target_value"]))
        else:
            progress = None

    return {
        "id": row["id"],
        "name": row["name"],
        "tracking_type": tracking_type,
        "aggregation_mode": row["aggregation_mode"],
        "unit": row["unit"],
        "target_value": row["target_value"],
        "target_text": row["target_text"],
        "target_operator": row["target_operator"],
        "time_anchor_minute": row["time_anchor_minute"],
        "current_bool": current_bool,
        "current_number": current_number,
        "current_text": current_text,
        "met": met,
        "progress": progress,
        # 「养成中 / 已结束」靠它区分 ✓（`active = 1` / `0` ✓）。
        # 归档**不删记录** ✓ —— 所以已结束的习惯仍然带着历史 ✓。
        "active": bool(row["active"]),
    }


def create_habit(data: HabitCreate) -> dict:
    tracking_type = data.tracking_type.strip().lower()
    if tracking_type not in _ALLOWED_TYPES:
        raise DomainValidationError("不支持的习惯类型。")

    aggregation = data.aggregation_mode
    if aggregation is None:
        aggregation = "latest" if tracking_type in {"boolean", "time"} else "sum"
    if aggregation not in _ALLOWED_AGG:
        raise DomainValidationError("不支持的聚合方式。")

    operator = data.target_operator
    if tracking_type == "boolean":
        operator = None
    elif operator is None:
        operator = "<=" if tracking_type == "time" else ">="
    if operator is not None and operator not in _ALLOWED_OP:
        raise DomainValidationError("不支持的目标比较方式。")

    target_value = data.target_value
    target_text = data.target_text
    time_anchor_minute = None

    if tracking_type == "time":
        if not target_text:
            raise DomainValidationError("time 类型习惯必须提供 target_text，例如 23:30。")
        if not data.time_anchor:
            raise DomainValidationError("time 类型习惯必须提供 time_anchor，例如 18:00。")
        try:
            time_anchor_minute = minutes_from_midnight(data.time_anchor)
            target_value = float(normalize_time_minutes(target_text, time_anchor_minute))
        except ValueError as exc:
            raise DomainValidationError("time 习惯的目标时间或锚点格式无效。") from exc

    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO habits (
                name, tracking_type, aggregation_mode, unit,
                target_value, target_text, target_operator,
                time_anchor_minute, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                data.name.strip(),
                tracking_type,
                aggregation,
                data.unit.strip() if data.unit else None,
                target_value,
                target_text,
                operator,
                time_anchor_minute,
                now,
                now,
            ),
        )
        habit_id = cursor.lastrowid
    return get_habit_today(habit_id)


#: 「养成中 / 已结束」→ `habits.active` 的取值。
#: ⚠️ 归档 = `active = 0` ✓，**打卡记录一条都不删** ✓（那个是 `remove_habit()` 的事 ✗）。
_HABIT_STATUSES = {"active": 1, "archived": 0}


def list_habits(status: str | None = None, local_date: str | None = None) -> list[dict]:
    """**全部**习惯，含已经收起来的那些。

    ⚠️ 和 `list_habits_today()` 回答的是**两个问题** ✗：

    | 函数 | 问题 |
    |---|---|
    | `list_habits_today()` ✓ | "**今天**要打哪些卡" ✓（只给 `active = 1` ✓） |
    | `list_habits()` ✓ | "我一共有哪些习惯、哪些已经结束了" ✓ |

    `status` 可省略 ✓（= 全部 ✓），或 `active`（养成中）/ `archived`（已结束）✓。
    """
    day = local_date or local_today(APP_TIMEZONE)
    query = """
        SELECT
            h.*,
            l.value_bool,
            l.value_number,
            l.value_text
        FROM habits h
        LEFT JOIN habit_logs l
            ON l.habit_id = h.id
            AND l.local_date = ?
    """
    params: tuple = (day,)
    if status:
        if status not in _HABIT_STATUSES:
            raise DomainValidationError("无效的习惯状态。")
        query += " WHERE h.active = ?"
        params += (_HABIT_STATUSES[status],)
    query += " ORDER BY h.id ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_habit_row_to_today(row) for row in rows]


def list_habits_today(local_date: str | None = None) -> list[dict]:
    return list_habits("active", local_date)


def get_habit_today(habit_id: int, local_date: str | None = None) -> dict:
    day = local_date or local_today(APP_TIMEZONE)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                h.*,
                l.value_bool,
                l.value_number,
                l.value_text
            FROM habits h
            LEFT JOIN habit_logs l
                ON l.habit_id = h.id
                AND l.local_date = ?
            WHERE h.id = ? AND h.active = 1
            """,
            (day, habit_id),
        ).fetchone()
    if row is None:
        raise NotFoundError("习惯不存在。")
    return _habit_row_to_today(row)


def _aggregate(existing: float | None, incoming: float, mode: str) -> float:
    if existing is None:
        return incoming
    if mode == "sum":
        return existing + incoming
    if mode == "latest":
        return incoming
    if mode == "max":
        return max(existing, incoming)
    if mode == "min":
        return min(existing, incoming)
    return incoming


def log_habit(habit_id: int, data: HabitLogRequest, local_date: str | None = None) -> dict:
    day = local_date or local_today(APP_TIMEZONE)
    now = to_utc_iso(utc_now())

    with get_connection() as conn:
        habit = conn.execute("SELECT * FROM habits WHERE id = ? AND active = 1", (habit_id,)).fetchone()
        if habit is None:
            raise NotFoundError("习惯不存在。")

        existing = conn.execute(
            "SELECT * FROM habit_logs WHERE habit_id = ? AND local_date = ?",
            (habit_id, day),
        ).fetchone()

        tracking_type = habit["tracking_type"]
        value_bool = existing["value_bool"] if existing else None
        value_number = existing["value_number"] if existing else None
        value_text = existing["value_text"] if existing else None

        if tracking_type == "boolean":
            if data.value_bool is None:
                raise DomainValidationError("boolean 习惯必须提供 value_bool。")
            value_bool = 1 if data.value_bool else 0
            value_number = None
            value_text = None

        elif tracking_type in {"numeric", "duration"}:
            if data.value_number is None:
                raise DomainValidationError("数值习惯必须提供 value_number。")
            value_number = _aggregate(
                value_number,
                float(data.value_number),
                habit["aggregation_mode"] or "latest",
            )
            value_text = None
            value_bool = None

        elif tracking_type == "time":
            if not data.value_text:
                raise DomainValidationError("time 习惯必须提供 value_text，例如 23:45。")
            anchor = habit["time_anchor_minute"]
            if anchor is None:
                raise DomainValidationError("该 time 习惯缺少 time_anchor_minute。")
            try:
                value_number = float(normalize_time_minutes(data.value_text, anchor))
            except ValueError as exc:
                raise DomainValidationError("时间格式无效。") from exc
            value_text = data.value_text
            value_bool = None

        if existing is None:
            conn.execute(
                """
                INSERT INTO habit_logs (
                    habit_id, local_date, value_bool, value_number,
                    value_text, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    habit_id,
                    day,
                    value_bool,
                    value_number,
                    value_text,
                    data.note,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE habit_logs
                SET value_bool = ?, value_number = ?, value_text = ?, note = ?, updated_at = ?
                WHERE habit_id = ? AND local_date = ?
                """,
                (
                    value_bool,
                    value_number,
                    value_text,
                    data.note if data.note is not None else existing["note"],
                    now,
                    habit_id,
                    day,
                ),
            )

    return get_habit_today(habit_id, day)


def archive_habit(habit_id: int) -> None:
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE habits SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
            (now, habit_id),
        )
    if cursor.rowcount == 0:
        raise NotFoundError("习惯不存在或已归档。")


def restore_habit(habit_id: int) -> dict:
    """把**已结束**的习惯重新放回「养成中」（`active: 0 → 1`）。

    ## 为什么需要它

    界面上新增了「已结束」那一类 ✓ —— 如果没有恢复，
    **归档就成了单向操作** ✗：点错一次就只能去改数据库 ✓。

    ⚠️ 打卡记录**一直都在** ✓（归档从不删记录 ✓），所以恢复之后历史是完整的 ✓ ——
    这和 `remove_habit()`（连记录一起删 ✗）是两件事 ✓。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE habits SET active = 1, updated_at = ? WHERE id = ? AND active = 0",
            (now, habit_id),
        )
    if cursor.rowcount == 0:
        raise NotFoundError("习惯不存在，或者它本来就在养成中。")
    return get_habit_today(habit_id)


def remove_habit(habit_id: int) -> dict:
    """**彻底删除**一个习惯，连同它**全部打卡记录**。

    ## 和 `archive_habit()` 的区别（用户 2026-10-05 要求分开 ✓）

    | | 习惯行 | 打卡记录 |
    |---|---|---|
    | **归档** ✓ | 还在（`active = 0`）✓ | **全留着** ✓ |
    | **彻底删除** ✓ | 没了 ✗ | **一起删掉** ✗ |

    ⚠️ `habit_logs.habit_id` 有外键、**没有 `ON DELETE CASCADE`** ✓ ——
    所以必须先删记录、再删习惯 ✓，否则外键会**直接拒绝**这次删除 ✗。

    删完之后 ✓：今天的习惯列表、回顾里的习惯统计都会跟着变 ✓（那是应该的 ✓）。
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM habits WHERE id = ?", (habit_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("习惯不存在。")

        logs = conn.execute(
            "SELECT COUNT(*) AS n FROM habit_logs WHERE habit_id = ?",
            (habit_id,),
        ).fetchone()["n"]

        # ⚠️ 顺序不能反：先记录，后习惯 ✓
        conn.execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))

    return {"deleted_logs": logs}
