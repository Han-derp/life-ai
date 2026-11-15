"""S7 承诺（关系记忆 LR）。

覆盖 DEVELOPMENT_PLAN v2.0 · S7 完成判据里"承诺可验证"的部分：

- 承诺**结构化**，所以能被程序核对（MEMORY_DESIGN §4.5-A）
- 改口**保留旧版**
- 她的回应 + 你的原话都留着
"""

import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.commitment import (
    CommitmentCreate,
    CommitmentFromChatRequest,
    CommitmentReviseRequest,
)
from app.services import commitment_service

QUIET = {"quiet_hours": {"start": "23:00", "end": "08:00"}}


def _promise(content: str = "行。那你也别十一点后还醒着。", **kwargs) -> dict:
    return commitment_service.create_commitment(
        CommitmentCreate(content=content, **kwargs)
    )


# ---------- 基本 ----------


def test_create_commitment_keeps_both_sides_of_the_promise():
    commitment = _promise(source_text="以后十一点以后别找我", structured=QUIET)

    assert commitment["content"] == "行。那你也别十一点后还醒着。"  # 她的回应
    assert commitment["source_text"] == "以后十一点以后别找我"  # 你的原话
    assert commitment["structured"] == {
        "quiet_hours": {"start": "23:00", "end": "08:00"}
    }
    assert commitment["window_text"] == "23:00 – 08:00 不主动找你"
    assert commitment["created_via"] == "manual"
    assert commitment["in_effect"] is True
    assert commitment["is_permanent"] is True
    assert commitment["superseded_by"] is None


def test_window_text_for_open_ended_window():
    commitment = _promise(structured={"quiet_hours": {"start": "23:00", "end": None}})

    assert commitment["window_text"] == "23:00 之后不主动找你"


def test_unknown_structured_key_is_rejected():
    with pytest.raises(DomainValidationError):
        _promise(structured={"something_else": {}})


def test_quiet_hours_requires_start():
    with pytest.raises(DomainValidationError):
        _promise(structured={"quiet_hours": {}})


def test_unknown_commitment_raises():
    with pytest.raises(NotFoundError):
        commitment_service.get_commitment(999)


# ---------- 核对：承诺可验证 ----------


def test_no_violation_when_she_stays_quiet():
    commitment = _promise(structured=QUIET)

    # 当地 22:00，窗口（23:00–08:00）之外
    result = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T14:00:00Z"],
    )

    assert result["checkable"] is True
    assert result["checked"] == 1
    assert result["violations"] == []


def test_violation_inside_the_quiet_window():
    commitment = _promise(structured=QUIET)

    # 当地 23:30 → UTC 15:30
    result = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T15:30:00Z"],
    )

    assert len(result["violations"]) == 1
    assert result["violations"][0]["moment"] == "2026-06-01T15:30:00Z"
    assert result["violations"][0]["moment_local"].startswith("2026-06-01T23:30")


def test_window_wraps_midnight():
    commitment = _promise(structured=QUIET)

    # 当地次日 02:00 —— 仍然在 23:00–08:00 里
    result = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T18:00:00Z"],
    )

    assert len(result["violations"]) == 1


def test_window_end_is_exclusive():
    commitment = _promise(structured=QUIET)

    # 当地正好 08:00 —— 窗口已经结束
    result = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-02T00:00:00Z"],
    )

    assert result["violations"] == []


def test_open_ended_window_covers_the_rest_of_the_day():
    commitment = _promise(structured={"quiet_hours": {"start": "23:00", "end": None}})

    inside = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T15:30:00Z"],
    )
    outside = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T10:00:00Z"],
    )

    assert len(inside["violations"]) == 1
    assert outside["violations"] == []


def test_unstructured_commitment_is_not_checkable():
    commitment = _promise()

    result = commitment_service.violations_for(
        commitment["id"],
        ["2026-06-01T15:30:00Z"],
    )

    assert result["checkable"] is False
    assert result["violations"] == []


def test_bad_moment_is_rejected():
    commitment = _promise(structured=QUIET)

    with pytest.raises(DomainValidationError):
        commitment_service.violations_for(commitment["id"], ["not-a-time"])


# ---------- 改口保留旧版 ----------


def test_revise_creates_a_new_version_and_keeps_the_old_one():
    first = _promise(source_text="以后十一点以后别找我", structured=QUIET)

    second = commitment_service.revise_commitment(
        first["id"],
        CommitmentReviseRequest(content="行。那十二点。"),
    )

    assert second["content"] == "行。那十二点。"
    assert second["created_via"] == "manual"  # 你亲手改过的
    assert second["superseded_by"] is None

    old = commitment_service.get_commitment(first["id"])
    assert old["status"] == "changed"
    assert old["superseded_by"] == second["id"]
    assert old["content"] == "行。那你也别十一点后还醒着。"  # 旧版留着

    assert [item["id"] for item in commitment_service.list_commitments()] == [
        second["id"],
        first["id"],
    ]
    # "她改过口"的那一版不算"当前生效"
    assert [
        item["id"]
        for item in commitment_service.list_commitments(include_inactive=False)
    ] == [second["id"]]


def test_superseded_commitment_cannot_be_revised_again():
    first = _promise()
    commitment_service.revise_commitment(
        first["id"],
        CommitmentReviseRequest(content="新"),
    )

    with pytest.raises(DomainValidationError):
        commitment_service.revise_commitment(
            first["id"],
            CommitmentReviseRequest(content="更新"),
        )


def test_revoke_marks_it_revoked():
    commitment = _promise()

    revoked = commitment_service.revoke_commitment(commitment["id"])

    assert revoked["status"] == "revoked"
    assert revoked["in_effect"] is False


# ---------- 从聊天来 ----------


def test_chat_commitment_requires_your_words():
    with pytest.raises(DomainValidationError):
        commitment_service.apply_chat_commitment(
            CommitmentFromChatRequest(content="行。", source_text="   ")
        )


def test_chat_commitment_is_recorded_with_its_source():
    result = commitment_service.apply_chat_commitment(
        CommitmentFromChatRequest(
            content="行。那你也别十一点后还醒着。",
            source_text="以后十一点以后别找我",
            structured=QUIET,
        )
    )

    assert result["created"] is True
    assert result["commitment"]["created_via"] == "chat"
    assert result["commitment"]["source_text"] == "以后十一点以后别找我"


def test_manual_commitment_wins_over_the_same_chat_commitment():
    manual = _promise()

    result = commitment_service.apply_chat_commitment(
        CommitmentFromChatRequest(
            content=manual["content"],
            source_text="以后十一点以后别找我",
        )
    )

    assert result["created"] is False
    assert result["overridden_by_manual"] is True
    assert result["commitment"]["id"] == manual["id"]
    assert len(commitment_service.list_commitments()) == 1
