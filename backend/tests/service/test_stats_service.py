from datetime import date, timedelta

import pytest

from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError
from app.core.time_utils import local_datetime_to_utc_iso, local_today
from app.schemas.deadline import DeadlineCreate
from app.schemas.habit import HabitCreate, HabitLogRequest
from app.schemas.pomodoro import PomodoroStart
from app.schemas.schedule import ScheduleCreate
from app.services.deadline_service import (
    cancel_deadline,
    complete_deadline,
    create_deadline,
)
from app.services.habit_service import create_habit, log_habit
from app.services.pomodoro_service import finish_pomodoro, start_pomodoro
from app.services.schedule_service import cancel_schedule, complete_schedule, create_schedule
from app.services.stats_service import get_weekly_stats


# Monday of a fixed reference week, used across tests so week_start/week_end are deterministic.
_MONDAY = date(2026, 6, 1)
assert _MONDAY.weekday() == 0


def _local(day_offset: int, hhmm: str) -> str:
    day = _MONDAY + timedelta(days=day_offset)
    return f"{day.isoformat()}T{hhmm}"


def test_week_bounds_are_monday_to_sunday():
    stats = get_weekly_stats(_local(2, "09:00")[:10])
    assert stats["week_start"] == "2026-06-01"
    assert stats["week_end"] == "2026-06-07"


def test_schedule_counts_by_status_within_week():
    e1 = create_schedule(ScheduleCreate(title="A", start_at_local=_local(0, "09:00"), end_at_local=_local(0, "10:00")))
    e2 = create_schedule(ScheduleCreate(title="B", start_at_local=_local(1, "09:00"), end_at_local=_local(1, "10:00")))
    create_schedule(ScheduleCreate(title="C", start_at_local=_local(3, "09:00"), end_at_local=_local(3, "10:00")))
    complete_schedule(e1["id"])
    cancel_schedule(e2["id"])

    stats = get_weekly_stats("2026-06-03")
    assert stats["schedule"] == {"completed": 1, "pending": 1, "cancelled": 1, "total": 3}


def test_schedule_excludes_events_outside_week():
    create_schedule(ScheduleCreate(title="OutsideNext", start_at_local="2026-06-08T09:00", end_at_local="2026-06-08T10:00"))
    stats = get_weekly_stats("2026-06-03")
    assert stats["schedule"]["total"] == 0


def test_pomodoro_sums_completed_minutes_only():
    running = start_pomodoro(PomodoroStart(planned_minutes=25, custom_label="写代码"))
    finish_pomodoro(running["id"])
    stats = get_weekly_stats(local_today(APP_TIMEZONE))
    assert stats["pomodoro"]["count"] == 1
    assert stats["pomodoro"]["minutes"] >= 0


def test_deadline_completed_and_overdue_counts():
    today = local_today(APP_TIMEZONE)
    d1 = create_deadline(DeadlineCreate(title="报告", due_at_local=f"{today}T18:00"))
    complete_deadline(d1["id"])
    create_deadline(DeadlineCreate(title="过期任务", due_at_local="2000-01-01T00:00"))

    stats = get_weekly_stats(today)
    assert stats["deadlines"]["completed"] == 1
    assert stats["deadlines"]["created"] == 2  # both deadlines were created "now", within this week


def test_habit_boolean_days_met():
    habit = create_habit(HabitCreate(name="喝水", tracking_type="boolean"))
    log_habit(habit["id"], HabitLogRequest(value_bool=True), local_date="2026-06-01")
    log_habit(habit["id"], HabitLogRequest(value_bool=False), local_date="2026-06-02")

    stats = get_weekly_stats("2026-06-03")
    entry = next(item for item in stats["habits"] if item["habit_id"] == habit["id"])
    assert entry["days_logged"] == 2
    assert entry["days_met"] == 1


def test_habit_numeric_target_met():
    habit = create_habit(
        HabitCreate(name="喝水量", tracking_type="numeric", target_value=2000, target_operator=">=")
    )
    log_habit(habit["id"], HabitLogRequest(value_number=2500), local_date="2026-06-01")
    log_habit(habit["id"], HabitLogRequest(value_number=500), local_date="2026-06-02")

    stats = get_weekly_stats("2026-06-03")
    entry = next(item for item in stats["habits"] if item["habit_id"] == habit["id"])
    assert entry["days_logged"] == 2
    assert entry["days_met"] == 1


def test_invalid_local_date_raises():
    with pytest.raises(DomainValidationError):
        get_weekly_stats("not-a-date")


# ---------- 取消掉的不算"新建" ----------


def _backdate(deadline_id: int, day_offset: int = 2) -> None:
    """把 `created_at` 挪进那个固定的测试周里。

    ⚠️ `create_deadline()` 写的是**真实的 now** ✓，而这个文件用的是 2026-06
    那一周 ✓ —— 不挪的话"本周新建"永远是 0 ✗（我第一版就是这么红的 ✓）。
    创建时间没有接口能指定 ✓，所以只能直接改库 ✓。
    """
    from app.database.connection import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE deadlines SET created_at = ? WHERE id = ?",
            (
                local_datetime_to_utc_iso(_local(day_offset, "09:00"), APP_TIMEZONE),
                deadline_id,
            ),
        )


def test_a_cancelled_deadline_is_not_counted_as_created():
    """★ 用户实测（2026-10-05）：

    > "我把新建的没有逾期的 DDL 删除后，统计还是统计的所有的" ✗

    根因：DDL 的「取消」只把 `completion_status` 改成 `'cancelled'` ✓，
    行还留在库里 ✓。所以"本周新建"必须**显式排除**它 ✓。

    ⚠️ 但用户真正要的是"**删除就该真的删掉**" ✓ —— 那个已经单独做了 ✓
    （见 `tests/service/test_deadline_delete.py` ✓）。
    """
    first = create_deadline(DeadlineCreate(title="A", due_at_local=_local(2, "18:00")))
    second = create_deadline(DeadlineCreate(title="B", due_at_local=_local(3, "18:00")))
    doomed = create_deadline(
        DeadlineCreate(title="C", due_at_local=_local(4, "18:00"))
    )
    for item in (first, second, doomed):
        _backdate(item["id"])

    assert get_weekly_stats("2026-06-03")["deadlines"]["created"] == 3

    cancel_deadline(doomed["id"])

    assert get_weekly_stats("2026-06-03")["deadlines"]["created"] == 2


def test_a_deleted_deadline_disappears_from_the_count_too():
    """★ 真删除之后，数字**自然**就掉了 ✓（行都没了 ✓）。"""
    from app.services.deadline_service import remove_deadline

    keep = create_deadline(DeadlineCreate(title="A", due_at_local=_local(2, "18:00")))
    doomed = create_deadline(DeadlineCreate(title="B", due_at_local=_local(3, "18:00")))
    for item in (keep, doomed):
        _backdate(item["id"])

    assert get_weekly_stats("2026-06-03")["deadlines"]["created"] == 2

    remove_deadline(doomed["id"])

    assert get_weekly_stats("2026-06-03")["deadlines"]["created"] == 1
