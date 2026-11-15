"""S6 流式 API（`POST /api/v1/chat/stream`）。"""

import json

from app.core.exceptions import ExternalServiceError
from app.services import chat_service


class FakeStreamingProvider:
    available = True
    last_usage = {"prompt_cache_hit_tokens": 99}

    def chat(self, messages, temperature=None):
        return "你好呀"

    def chat_stream(self, messages, temperature=None):
        for chunk in ("你", "好", "呀"):
            yield chunk


class ExplodingProvider:
    available = True
    last_usage = None

    def chat(self, messages, temperature=None):
        return "不该走这里"

    def chat_stream(self, messages, temperature=None):
        raise ExternalServiceError("没配 Key")
        yield  # pragma: no cover


def _events(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_endpoint_returns_sse(client, monkeypatch):
    monkeypatch.setattr(chat_service, "_provider", FakeStreamingProvider())

    response = client.post("/api/v1/chat/stream", json={"content": "在吗"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response.text)
    assert [event["type"] for event in events] == ["delta", "delta", "delta", "done"]
    assert events[-1]["content"] == "你好呀"


def test_streamed_content_is_what_gets_persisted(client, monkeypatch):
    monkeypatch.setattr(chat_service, "_provider", FakeStreamingProvider())

    response = client.post("/api/v1/chat/stream", json={"content": "在吗"})
    streamed = "".join(
        event["text"] for event in _events(response.text) if event["type"] == "delta"
    )

    messages = client.get("/api/v1/chat/messages").json()
    assistant = [message for message in messages if message["role"] == "assistant"]

    assert len(assistant) == 1
    assert assistant[0]["content"] == streamed == "你好呀"


def test_non_streaming_endpoint_still_works(client, monkeypatch):
    """流式是新增的一条路；一次性的那条不能坏（它是对照组，也是回退路）。"""
    monkeypatch.setattr(chat_service, "_provider", FakeStreamingProvider())

    response = client.post("/api/v1/chat", json={"content": "在吗"})

    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "你好呀"


def test_stream_reports_provider_errors_as_events(client, monkeypatch):
    monkeypatch.setattr(chat_service, "_provider", ExplodingProvider())

    response = client.post("/api/v1/chat/stream", json={"content": "在吗"})

    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1]["type"] == "error"
    assert "没配 Key" in events[-1]["detail"]


def test_stream_reports_an_empty_turn(client):
    """空回合（没字也没图）→ 从**流里**回一条明确的错误。

    以前靠 `ChatRequest.content` 的 `min_length=1` 拦（422）。
    现在 content 允许为空（可以只发一张图），所以改由 service 判定 ——
    流式链路的错误一律从流里出来，状态码那时已经改不了了。
    """
    response = client.post("/api/v1/chat/stream", json={"content": ""})

    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1]["type"] == "error"
    assert "发张图" in events[-1]["detail"]
