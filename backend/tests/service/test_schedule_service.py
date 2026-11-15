import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.services.schedule_service import (
    cancel_schedule,
    complete_schedule,
    create_schedule,
    list_schedule_for_date,
    update_schedule,
)


def _make(
    title: str = "写周报",
    start_at_local: str = "2026-06-01T09:00",
    end_at_local: str | None = "2026-06-01T10:00",
) -> dict:
    return create_schedule(
        ScheduleCreate(title=title, start_at_local=start_at_local, end_at_local=end_at_local)
    )


def test_create_schedule_defaults():
    event = _make()
    assert event["completion_status"] == "pending"
    assert event["source_type"] == "manual"
    assert event["start_at"].endswith("Z")


def test_create_schedule_rejects_end_before_start():
    with pytest.raises(DomainValidationError):
        _make(start_at_local="2026-06-01T10:00", end_at_local="2026-06-01T09:00")


def test_create_schedule_allows_open_ended_event():
    event = _make(end_at_local=None)
    assert event["end_at"] is None
    assert event["end_at_local"] is None


def test_list_schedule_for_date_includes_overlapping_events():
    _make(start_at_local="2026-06-01T23:00", end_at_local="2026-06-02T01:00")
    same_day = list_schedule_for_date("2026-06-01")
    next_day = list_schedule_for_date("2026-06-02")
    assert len(same_day) == 1
    assert len(next_day) == 1


def test_list_schedule_for_date_excludes_other_days():
    _make(start_at_local="2026-06-01T09:00", end_at_local="2026-06-01T10:00")
    other_day = list_schedule_for_date("2026-06-05")
    assert other_day == []


def test_update_schedule_only_when_pending():
    event = _make()
    complete_schedule(event["id"])
    with pytest.raises(DomainValidationError):
        update_schedule(event["id"], ScheduleUpdate(title="改名"))


def test_update_schedule_changes_time():
    event = _make()
    updated = update_schedule(
        event["id"],
        ScheduleUpdate(start_at_local="2026-06-01T11:00", end_at_local="2026-06-01T12:00"),
    )
    assert updated["start_at_local"].startswith("2026-06-01T11:00")


def test_update_schedule_not_found():
    with pytest.raises(NotFoundError):
        update_schedule(999, ScheduleUpdate(title="改名"))


def test_complete_then_cancel_is_rejected():
    event = _make()
    complete_schedule(event["id"])
    with pytest.raises(DomainValidationError):
        cancel_schedule(event["id"])


def test_cancel_schedule_sets_status():
    event = _make()
    cancelled = cancel_schedule(event["id"])
    assert cancelled["completion_status"] == "cancelled"
