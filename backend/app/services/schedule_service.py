from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import (
    local_datetime_to_utc_iso,
    local_day_range_utc,
    local_today,
    parse_utc,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.services.status_utils import event_time_status


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "start_at_local": utc_to_local_iso(row["start_at"], APP_TIMEZONE),
        "end_at_local": utc_to_local_iso(row["end_at"], APP_TIMEZONE) if row["end_at"] else None,
        "completion_status": row["completion_status"],
        "time_status": event_time_status(row["start_at"], row["end_at"], row["completion_status"]),
        "source_type": row["source_type"],
        "note": row["note"],
    }


def _convert_times(start_local: str, end_local: str | None) -> tuple[str, str | None]:
    try:
        start = local_datetime_to_utc_iso(start_local, APP_TIMEZONE)
        end = local_datetime_to_utc_iso(end_local, APP_TIMEZONE) if end_local else None
    except ValueError as exc:
        raise DomainValidationError("日程时间格式无效。") from exc
    if end is not None and parse_utc(end) < parse_utc(start):
        raise DomainValidationError("结束时间不能早于开始时间。")
    return start, end


def create_schedule(data: ScheduleCreate) -> dict:
    start, end = _convert_times(data.start_at_local, data.end_at_local)
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO schedule_events (
                title, description, start_at, end_at, completion_status,
                source_type, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 'manual', ?, ?, ?)
            """,
            (
                data.title.strip(),
                data.description.strip() if data.description else None,
                start,
                end,
                data.note.strip() if data.note else None,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM schedule_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def list_schedule_for_date(local_date: str | None = None) -> list[dict]:
    day = local_date or local_today(APP_TIMEZONE)
    start, end = local_day_range_utc(day, APP_TIMEZONE)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM schedule_events
            WHERE
                (end_at IS NULL AND start_at >= ? AND start_at < ?)
                OR
                (end_at IS NOT NULL AND start_at < ? AND end_at > ?)
            ORDER BY start_at ASC, id ASC
            """,
            (start, end, end, start),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_schedules(status: str | None = None) -> list[dict]:
    """**全部**日程（不限定某一天）。

    ⚠️ 和 `list_schedule_for_date()` 回答的是**两个问题** ✗：

    | 函数 | 问题 |
    |---|---|
    | `list_schedule_for_date()` ✓ | "**今天**有什么" ✓ |
    | `list_schedules()` ✓ | "我一共记过哪些" ✓（翻看 / 全部那一类用 ✓） |

    排序是 `start_at DESC` ✓ —— 它主要给**翻看**用，最近的在最上面 ✓。
    （「未完成」那一类由前端改成升序 ✓：待办该从最急的看起 ✓。）
    """
    query = "SELECT * FROM schedule_events"
    params: tuple = ()
    if status:
        if status not in {"pending", "completed", "cancelled"}:
            raise DomainValidationError("无效的日程状态。")
        query += " WHERE completion_status = ?"
        params = (status,)
    query += " ORDER BY start_at DESC, id DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_schedule(event_id: int, data: ScheduleUpdate) -> dict:
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM schedule_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError("日程不存在。")
        if row["completion_status"] != "pending":
            raise DomainValidationError("只有 pending 日程可以编辑。")

        title = data.title.strip() if data.title is not None else row["title"]
        description = data.description if data.description is not None else row["description"]
        note = data.note if data.note is not None else row["note"]
        start = row["start_at"]
        end = row["end_at"]
        if data.start_at_local is not None or data.end_at_local is not None:
            start_local = data.start_at_local or utc_to_local_iso(start, APP_TIMEZONE)
            end_local = data.end_at_local if data.end_at_local is not None else (
                utc_to_local_iso(end, APP_TIMEZONE) if end else None
            )
            start, end = _convert_times(start_local, end_local)

        conn.execute(
            """
            UPDATE schedule_events
            SET title = ?, description = ?, start_at = ?, end_at = ?, note = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, description, start, end, note, now, event_id),
        )
        updated = conn.execute("SELECT * FROM schedule_events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_dict(updated)


def _set_status(event_id: int, status: str) -> dict:
    now = to_utc_iso(utc_now())
    field = "completed_at" if status == "completed" else "cancelled_at"
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM schedule_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError("日程不存在。")
        if row["completion_status"] != "pending":
            raise DomainValidationError("只有 pending 日程可以变更状态。")
        conn.execute(
            f"""
            UPDATE schedule_events
            SET completion_status = ?, {field} = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, now, event_id),
        )
        updated = conn.execute("SELECT * FROM schedule_events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_dict(updated)


def complete_schedule(event_id: int) -> dict:
    return _set_status(event_id, "completed")


def cancel_schedule(event_id: int) -> dict:
    return _set_status(event_id, "cancelled")
