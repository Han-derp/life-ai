import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.deadline import DeadlineBreakdownApply, DeadlineBreakdownSubtask, DeadlineCreate
from app.services import deadline_service
from app.services.deadline_service import (
    apply_deadline_breakdown,
    create_deadline,
    suggest_deadline_breakdown,
)


class FakeProvider:
    available = True

    def __init__(self, response: str | None = None):
        self._response = response or (
            '{"subtasks":[{"title":"搭建大纲","due_at_local":"2026-12-05T20:00","priority":2},'
            '{"title":"完成初稿","due_at_local":"2026-12-20T20:00","priority":1}]}'
        )

    def chat(self, messages, temperature=None):
        return self._response


class UnavailableProvider:
    available = False

    def chat(self, messages, temperature=None):
        raise AssertionError("chat() must not be called when unavailable")


@pytest.fixture(autouse=True)
def restore_provider():
    original = deadline_service._provider
    yield
    deadline_service._provider = original


def _make_parent(**kwargs) -> dict:
    return create_deadline(
        DeadlineCreate(title="写毕业论文", due_at_local="2026-12-31T20:00", **kwargs)
    )


def test_suggest_returns_ai_unavailable_when_no_provider():
    deadline_service._provider = UnavailableProvider()
    parent = _make_parent()

    result = suggest_deadline_breakdown(parent["id"])

    assert result["ai_available"] is False
    assert result["subtasks"] == []


def test_suggest_parses_valid_ai_json():
    deadline_service._provider = FakeProvider()
    parent = _make_parent()

    result = suggest_deadline_breakdown(parent["id"])

    assert result["ai_available"] is True
    assert len(result["subtasks"]) == 2
    assert result["subtasks"][0]["title"] == "搭建大纲"


def test_suggest_falls_back_when_ai_returns_malformed_json_twice():
    deadline_service._provider = FakeProvider(response="not json at all")
    parent = _make_parent()

    result = suggest_deadline_breakdown(parent["id"])

    assert result["ai_available"] is False
    assert result["subtasks"] == []


def test_suggest_rejects_breakdown_of_a_child_deadline():
    deadline_service._provider = FakeProvider()
    parent = _make_parent()
    child = create_deadline(
        DeadlineCreate(title="子任务", due_at_local="2026-12-10T20:00", parent_id=parent["id"])
    )

    with pytest.raises(DomainValidationError):
        suggest_deadline_breakdown(child["id"])


def test_apply_creates_child_deadlines_in_one_call():
    parent = _make_parent()
    payload = DeadlineBreakdownApply(
        subtasks=[
            DeadlineBreakdownSubtask(title="搭建大纲", due_at_local="2026-12-05T20:00", priority=2),
            DeadlineBreakdownSubtask(title="完成初稿", due_at_local=None, priority=1),
        ]
    )

    children = apply_deadline_breakdown(parent["id"], payload)

    assert len(children) == 2
    assert all(child["parent_id"] == parent["id"] for child in children)
    assert children[0]["title"] == "搭建大纲"
    # due_at_local=None falls back to the parent's own due date.
    assert children[1]["due_at"] == parent["due_at"]


def test_apply_rejects_missing_parent():
    payload = DeadlineBreakdownApply(
        subtasks=[DeadlineBreakdownSubtask(title="子任务", due_at_local=None, priority=0)]
    )
    with pytest.raises(NotFoundError):
        apply_deadline_breakdown(999, payload)


def test_apply_rejects_second_level_breakdown():
    parent = _make_parent()
    child = create_deadline(
        DeadlineCreate(title="子任务", due_at_local="2026-12-10T20:00", parent_id=parent["id"])
    )
    payload = DeadlineBreakdownApply(
        subtasks=[DeadlineBreakdownSubtask(title="孙任务", due_at_local=None, priority=0)]
    )
    with pytest.raises(DomainValidationError):
        apply_deadline_breakdown(child["id"], payload)


def test_apply_rejects_invalid_subtask_due_at():
    parent = _make_parent()
    payload = DeadlineBreakdownApply(
        subtasks=[DeadlineBreakdownSubtask(title="坏时间", due_at_local="not-a-date", priority=0)]
    )
    with pytest.raises(DomainValidationError):
        apply_deadline_breakdown(parent["id"], payload)
