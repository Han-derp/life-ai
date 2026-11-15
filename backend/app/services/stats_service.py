from app.services.execution_stats_service import get_execution_stats
from datetime import date, timedelta

from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError
from app.core.time_utils import local_day_range_utc, local_today, to_utc_iso, utc_now
from app.database.connection import get_connection


def _week_bounds(local_date: str | None) -> tuple[date, date]:
    """Return the Monday..Sunday (inclusive) local calendar week containing local_date."""
    day = date.fromisoformat(local_date) if local_date else date.fromisoformat(local_today(APP_TIMEZONE))
    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _schedule_stats(conn, start_utc: str, end_utc: str) -> dict:
    rows = conn.execute(
        """
        SELECT completion_status, COUNT(*) as n
        FROM schedule_events
        WHERE start_at >= ? AND start_at < ?
        GROUP BY completion_status
        """,
        (start_utc, end_utc),
    ).fetchall()
    counts = {"pending": 0, "completed": 0, "cancelled": 0}
    for row in rows:
        counts[row["completion_status"]] = row["n"]
    return {
        "completed": counts["completed"],
        "pending": counts["pending"],
        "cancelled": counts["cancelled"],
        "total": sum(counts.values()),
    }


def _pomodoro_stats(conn, start_utc: str, end_utc: str) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) as n, COALESCE(SUM(actual_minutes), 0) as minutes
        FROM pomodoro_sessions
        WHERE status = 'completed' AND started_at >= ? AND started_at < ?
        """,
        (start_utc, end_utc),
    ).fetchone()
    return {"count": row["n"], "minutes": row["minutes"]}


def _deadline_stats(conn, start_utc: str, end_utc: str, now_utc: str) -> dict:
    completed = conn.execute(
        """
        SELECT COUNT(*) as n FROM deadlines
        WHERE completion_status = 'completed' AND completed_at >= ? AND completed_at < ?
        """,
        (start_utc, end_utc),
    ).fetchone()["n"]
    overdue_pending = conn.execute(
        """
        SELECT COUNT(*) as n FROM deadlines
        WHERE completion_status = 'pending' AND due_at >= ? AND due_at < ? AND due_at < ?
        """,
        (start_utc, end_utc, now_utc),
    ).fetchone()["n"]
    created = conn.execute(
        """
        SELECT COUNT(*) as n FROM deadlines
        WHERE created_at >= ? AND created_at < ?
          -- ⚠️ **取消掉的不算** ✓（2026-10-05 用户实测：
          -- "把新建的没逾期的 DDL 删掉后，统计还是所有的" ✗）。
          --
          -- DDL **从来不是真删除** —— 界面上的「取消」只把 completion_status
          -- 改成 'cancelled' ✓，行还留在库里 ✓（历史要留得住 ✓）。
          -- 所以这里必须显式排除，否则"新建 3 个、取消 2 个"仍然显示 3 ✗。
          --
          -- ⚠️ 语义上这是**刻意的选择**：它表示"**现在还留着的**本周新建" ✓，
          -- 而不是"你一共建过几个" ✗（`completed` 那条仍然是历史事实 ✓）。
          AND completion_status != 'cancelled'
        """,
        (start_utc, end_utc),
    ).fetchone()["n"]
    return {"completed": completed, "overdue_pending": overdue_pending, "created": created}


def _habit_stats(conn, week_start: date, week_end: date) -> list[dict]:
    habits = conn.execute("SELECT * FROM habits WHERE active = 1 ORDER BY id ASC").fetchall()
    result = []
    for habit in habits:
        logs = conn.execute(
            """
            SELECT * FROM habit_logs
            WHERE habit_id = ? AND local_date >= ? AND local_date <= ?
            """,
            (habit["id"], week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
        days_logged = 0
        days_met = 0
        for log in logs:
            tracking_type = habit["tracking_type"]
            if tracking_type == "boolean":
                if log["value_bool"] is not None:
                    days_logged += 1
                    if log["value_bool"]:
                        days_met += 1
                continue

            if log["value_number"] is None:
                continue
            days_logged += 1
            target = habit["target_value"]
            operator = habit["target_operator"]
            if target is None or operator is None:
                continue
            value = log["value_number"]
            met = {
                ">=": value >= target,
                "<=": value <= target,
                "=": value == target,
                ">": value > target,
                "<": value < target,
            }.get(operator)
            if met:
                days_met += 1

        result.append(
            {
                "habit_id": habit["id"],
                "name": habit["name"],
                "tracking_type": habit["tracking_type"],
                "days_logged": days_logged,
                "days_met": days_met,
            }
        )
    return result


def get_weekly_stats(local_date: str | None = None) -> dict:
    """Read-only weekly aggregation over the Mon..Sun local calendar week containing local_date."""
    if local_date is not None:
        try:
            date.fromisoformat(local_date)
        except ValueError as exc:
            raise DomainValidationError("日期格式无效，应为 YYYY-MM-DD。") from exc

    week_start, week_end = _week_bounds(local_date)
    start_utc, _ = local_day_range_utc(week_start.isoformat(), APP_TIMEZONE)
    _, end_utc = local_day_range_utc(week_end.isoformat(), APP_TIMEZONE)
    now_utc = to_utc_iso(utc_now())

    with get_connection() as conn:
        schedule = _schedule_stats(conn, start_utc, end_utc)
        pomodoro = _pomodoro_stats(conn, start_utc, end_utc)
        deadlines = _deadline_stats(conn, start_utc, end_utc, now_utc)
        habits = _habit_stats(conn, week_start, week_end)

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "timezone": APP_TIMEZONE,
        "execution": get_execution_stats(start_utc, end_utc),
        "schedule": schedule,
        "pomodoro": pomodoro,
        "habits": habits,
        "deadlines": deadlines,
    }
