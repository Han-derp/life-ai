"""删除 DDL = **真的从库里删掉**（用户 2026-10-05 明确要求）。

⚠️ 和「取消」是**两件事** ✗：

| | 行还在吗 | 意思是 |
|---|---|---|
| **取消** ✓ | **在** ✓（只改 `completion_status`） | "我决定不做了，但这事发生过" ✓ |
| **删除** ✓ | **不在** ✓ | "当我没记过这件事" ✓ |
"""

import pytest

from app.core.exceptions import NotFoundError
from app.schemas.deadline import DeadlineCreate
from app.services.deadline_service import (
    cancel_deadline,
    create_deadline,
    get_deadline,
    list_deadlines,
    remove_deadline,
)

DUE = "2026-06-03T18:00"


def test_delete_really_removes_the_row(client):
    item = create_deadline(DeadlineCreate(title="临时", due_at_local=DUE))

    remove_deadline(item["id"])

    with pytest.raises(NotFoundError):
        get_deadline(item["id"])


def test_delete_also_removes_its_subtasks(client):
    """★ 拆解出来的里程碑会**一起删** ✓ —— 删父留子是孤儿数据 ✗。"""
    parent = create_deadline(DeadlineCreate(title="大作业", due_at_local=DUE))
    create_deadline(
        DeadlineCreate(title="第一步", due_at_local=DUE, parent_id=parent["id"])
    )
    create_deadline(
        DeadlineCreate(title="第二步", due_at_local=DUE, parent_id=parent["id"])
    )

    result = remove_deadline(parent["id"])

    assert result["deleted_children"] == 2
    assert list_deadlines() == []


def test_delete_does_not_touch_other_deadlines(client):
    keep = create_deadline(DeadlineCreate(title="留着", due_at_local=DUE))
    doomed = create_deadline(DeadlineCreate(title="删掉", due_at_local=DUE))

    remove_deadline(doomed["id"])

    assert [item["id"] for item in list_deadlines()] == [keep["id"]]


def test_cancel_and_delete_are_different(client):
    """⚠️ **这条是这次改动的核心** ✓。"""
    cancelled = create_deadline(DeadlineCreate(title="取消的", due_at_local=DUE))
    deleted = create_deadline(DeadlineCreate(title="删除的", due_at_local=DUE))

    cancel_deadline(cancelled["id"])
    remove_deadline(deleted["id"])

    ids = {item["id"] for item in list_deadlines()}
    assert cancelled["id"] in ids  # 取消的**还在** ✓（只是状态变了 ✓）
    assert deleted["id"] not in ids  # 删除的**不在** ✓


def test_deleting_a_missing_one_raises(client):
    with pytest.raises(NotFoundError):
        remove_deadline(999_999)


def test_the_api_has_a_delete_endpoint(client):
    item = create_deadline(DeadlineCreate(title="走接口删", due_at_local=DUE))

    response = client.delete(f"/api/v1/deadlines/{item['id']}")

    assert response.status_code == 200
    assert response.json()["deleted_children"] == 0
