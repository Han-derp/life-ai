from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError
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
from app.schemas.timeline import TimelineCreate


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "start_at_local": utc_to_local_iso(row["start_at"], APP_TIMEZONE),
        "end_at_local": utc_to_local_iso(row["end_at"], APP_TIMEZONE) if row["end_at"] else None,
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "note": row["note"],
    }


def create_timeline_event(data: TimelineCreate) -> dict:
    try:
        start = local_datetime_to_utc_iso(data.start_at_local, APP_TIMEZONE)
        end = local_datetime_to_utc_iso(data.end_at_local, APP_TIMEZONE) if data.end_at_local else None
    except ValueError as exc:
        raise DomainValidationError("时间轴时间格式无效。") from exc
    if end and parse_utc(end) < parse_utc(start):
        raise DomainValidationError("结束时间不能早于开始时间。")

    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO timeline_events (
                title, start_at, end_at, source_type, source_id,
                note, created_at, updated_at
            ) VALUES (?, ?, ?, 'manual', NULL, ?, ?, ?)
            """,
            (data.title.strip(), start, end, data.note, now, now),
        )
        row = conn.execute("SELECT * FROM timeline_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def list_timeline_for_date(local_date: str | None = None) -> list[dict]:
    day = local_date or local_today(APP_TIMEZONE)
    start, end = local_day_range_utc(day, APP_TIMEZONE)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM timeline_events
            WHERE
                (end_at IS NULL AND start_at >= ? AND start_at < ?)
                OR
                (end_at IS NOT NULL AND start_at < ? AND end_at > ?)
            ORDER BY start_at ASC, id ASC
            """,
            (start, end, end, start),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]
