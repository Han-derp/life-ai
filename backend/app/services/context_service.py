from datetime import date, timedelta

from app.core.config import APP_TIMEZONE
from app.core.time_utils import (
    local_day_range_utc,
    local_today,
    to_utc_iso,
    utc_now,
)
from app.database.connection import get_connection
from app.services.deadline_service import _row_to_dict as deadline_row_to_dict
from app.services.habit_service import list_habits_today
from app.services.pomodoro_service import list_pomodoros_for_range
from app.services.schedule_service import list_schedule_for_date
from app.services.timeline_service import list_timeline_for_date


def _deadline_context(local_date: str) -> list[dict]:
    today = date.fromisoformat(local_date)
    # Include today + the next 7 local calendar days.
    future_boundary_local = (today + timedelta(days=8)).isoformat()
    future_end, _ = local_day_range_utc(future_boundary_local, APP_TIMEZONE)
    now = to_utc_iso(utc_now())

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM deadlines
            WHERE completion_status = 'pending'
              AND due_at < ?
            ORDER BY
                CASE WHEN due_at < ? THEN 0 ELSE 1 END,
                due_at ASC,
                priority DESC
            """,
            (future_end, now),
        ).fetchall()
    return [deadline_row_to_dict(row) for row in rows]


def _pomodoro_summary(start_utc: str, end_utc: str) -> dict:
    sessions = list_pomodoros_for_range(start_utc, end_utc)
    completed = [item for item in sessions if item["status"] == "completed"]
    groups: dict[str, dict] = {}
    for item in completed:
        label = item["target_label"]
        group = groups.setdefault(label, {"label": label, "count": 0, "minutes": 0})
        group["count"] += 1
        group["minutes"] += item["actual_minutes"] or 0
    return {
        "count": len(completed),
        "minutes": sum(item["actual_minutes"] or 0 for item in completed),
        "groups": list(groups.values()),
        "running": next((item for item in sessions if item["status"] == "running"), None),
    }


def get_today_context(local_date: str | None = None) -> dict:
    day = local_date or local_today(APP_TIMEZONE)
    start_utc, end_utc = local_day_range_utc(day, APP_TIMEZONE)
    return {
        "now": to_utc_iso(utc_now()),
        "timezone": APP_TIMEZONE,
        "local_date": day,
        "schedule": list_schedule_for_date(day),
        "deadlines": _deadline_context(day),
        "habits": list_habits_today(day),
        "pomodoro": _pomodoro_summary(start_utc, end_utc),
        "timeline": list_timeline_for_date(day),
    }
