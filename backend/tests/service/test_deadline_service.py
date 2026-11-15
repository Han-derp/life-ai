import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.deadline import DeadlineCreate, DeadlineUpdate
from app.services.deadline_service import (
    cancel_deadline,
    complete_deadline,
    create_deadline,
    get_deadline,
    list_deadlines,
    update_deadline,
)


def _make(title: str = "写完 S1 测试", due_at_local: str = "2026-12-01T20:00", **kwargs) -> dict:
    return create_deadline(DeadlineCreate(title=title, due_at_local=due_at_local, **kwargs))


def test_create_deadline_defaults_to_pending():
    deadline = _make()
    assert deadline["completion_status"] == "pending"
    assert deadline["parent_id"] is None
    assert deadline["due_at"].endswith("Z")


def test_create_deadline_rejects_invalid_due_at():
    with pytest.raises(DomainValidationError):
        create_deadline(DeadlineCreate(title="坏时间", due_at_local="not-a-date"))


def test_create_deadline_with_valid_parent_sets_milestone():
    parent = _make(title="父任务")
    child = _make(title="子任务", parent_id=parent["id"])
    assert child["parent_id"] == parent["id"]


def test_create_deadline_rejects_missing_parent():
    with pytest.raises(DomainValidationError):
        _make(title="孤儿任务", parent_id=999)


def test_create_deadline_rejects_two_level_milestone():
    grandparent = _make(title="祖先任务")
    parent = _make(title="父任务", parent_id=grandparent["id"])
    with pytest.raises(DomainValidationError):
        _make(title="孙任务", parent_id=parent["id"])


def test_get_deadline_not_found_raises():
    with pytest.raises(NotFoundError):
        get_deadline(999)


def test_list_deadlines_filters_by_status():
    a = _make(title="A")
    complete_deadline(a["id"])
    _make(title="B")

    pending_only = list_deadlines("pending")
    assert all(item["completion_status"] == "pending" for item in pending_only)
    assert any(item["title"] == "B" for item in pending_only)
    assert all(item["title"] != "A" for item in pending_only)


def test_list_deadlines_rejects_invalid_status():
    with pytest.raises(DomainValidationError):
        list_deadlines("not-a-status")


def test_update_deadline_only_allowed_when_pending():
    deadline = _make()
    complete_deadline(deadline["id"])
    with pytest.raises(DomainValidationError):
        update_deadline(deadline["id"], DeadlineUpdate(title="改标题"))


def test_update_deadline_changes_fields():
    deadline = _make()
    updated = update_deadline(deadline["id"], DeadlineUpdate(title="新标题", priority=3))
    assert updated["title"] == "新标题"
    assert updated["priority"] == 3


def test_complete_deadline_sets_status_once():
    deadline = _make()
    completed = complete_deadline(deadline["id"])
    assert completed["completion_status"] == "completed"
    with pytest.raises(DomainValidationError):
        complete_deadline(deadline["id"])


def test_cancel_deadline_sets_status():
    deadline = _make()
    cancelled = cancel_deadline(deadline["id"])
    assert cancelled["completion_status"] == "cancelled"
