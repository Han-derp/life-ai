"""S2 上下文面板 API（「这次会发送什么」）。"""

import pytest

from app.services import chat_service, context_exclusion_service


@pytest.fixture(autouse=True)
def reset_once_exclusions():
    """exclude_once 是模块级状态，测试之间必须隔离。"""
    context_exclusion_service.clear_once_ids()
    yield
    context_exclusion_service.clear_once_ids()

EXPECTED_BLOCK_IDS = [
    "persona",
    "rules",
    "today:meta",
    "today:schedule",
    "today:deadlines",
    "today:habits",
    "today:pomodoro",
    "today:timeline",
    "history",
]


def test_preview_lists_all_blocks_with_tokens(client):
    response = client.get("/api/v1/context/preview")

    assert response.status_code == 200
    body = response.json()

    assert [b["id"] for b in body["blocks"]] == EXPECTED_BLOCK_IDS
    assert body["total_tokens"] > 0
    assert body["budget"] > 0
    assert body["over_budget"] is False
    assert body["excluded_labels"] == []
    # 每块都能看到原文——"看得见"是这个功能的全部意义
    assert all(b["content"] for b in body["blocks"])


def test_exclude_always_persists_and_can_be_restored(client):
    response = client.post(
        "/api/v1/context/exclude",
        json={"block_id": "today:timeline", "state": "exclude_always"},
    )
    assert response.status_code == 200

    body = response.json()
    block = next(b for b in body["blocks"] if b["id"] == "today:timeline")

    assert block["excluded"] is True
    assert block["state"] == "exclude_always"
    assert "实际时间轴" in body["excluded_labels"]
    assert body["boundary_notice"] is not None
    # 被排除的块保留在面板里（灰掉，不消失）
    assert block["content"]

    restored = client.post(
        "/api/v1/context/exclude",
        json={"block_id": "today:timeline", "state": "include"},
    )
    assert restored.status_code == 200
    assert restored.json()["excluded_labels"] == []


def test_exclude_once_does_not_persist(client):
    response = client.post(
        "/api/v1/context/exclude",
        json={"block_id": "today:habits", "state": "exclude_once"},
    )
    assert response.status_code == 200

    block = next(b for b in response.json()["blocks"] if b["id"] == "today:habits")
    assert block["state"] == "exclude_once"
    assert block["excluded"] is True
    assert context_exclusion_service.list_excluded_ids() == set()  # 不落库


def test_excluded_block_is_not_sent_to_the_model(client, monkeypatch):
    captured = {}

    class CapturingProvider:
        available = True
        last_usage = {"prompt_cache_hit_tokens": 42}

        def chat(self, messages, temperature=None):
            captured["system"] = messages[0]["content"]
            return "好"

    monkeypatch.setattr(chat_service, "_provider", CapturingProvider())

    client.post(
        "/api/v1/context/exclude",
        json={"block_id": "today:timeline", "state": "exclude_always"},
    )
    chat = client.post("/api/v1/chat", json={"content": "在吗"})
    assert chat.status_code == 200

    system = captured["system"]
    assert "【不可见内容】" in system
    assert "实际时间轴" in system  # 只作为"不可见"被提及
    assert "【实际时间轴】" not in system  # 内容本身没有发送


def test_preview_reports_cache_hit_from_last_usage(client, monkeypatch):
    class UsageProvider:
        available = True
        last_usage = {"prompt_cache_hit_tokens": 1520}

        def chat(self, messages, temperature=None):
            return "好"

    monkeypatch.setattr(chat_service, "_provider", UsageProvider())

    body = client.get("/api/v1/context/preview").json()
    assert body["cache_hit_tokens"] == 1520


def test_exclude_rejects_unknown_block(client):
    response = client.post(
        "/api/v1/context/exclude",
        json={"block_id": "memory:999", "state": "exclude_always"},
    )

    assert response.status_code == 400


def test_exclude_rejects_unknown_state(client):
    response = client.post(
        "/api/v1/context/exclude",
        json={"block_id": "persona", "state": "nope"},
    )

    assert response.status_code == 422
