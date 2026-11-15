"""S6 流式输出（service 层）。

对应 DEVELOPMENT_PLAN v2.0 · S6 的三条完成判据：

- 发出消息后 **1 秒内**开始出字 —— 结构上：第一个 delta 之前不写库、不做总结
- **流式内容与最终落库内容一致**
- **流式中断（关页面）不会写坏数据**
"""

import json

import pytest

from app.core.exceptions import ExternalServiceError
from app.llm.base import LLMProvider
from app.services import chat_service


class FakeStreamingProvider:
    available = True
    last_usage = {"prompt_cache_hit_tokens": 1234}

    def __init__(self, chunks=("你", "好", "呀")):
        self.chunks = tuple(chunks)

    def chat(self, messages, temperature=None):
        return "".join(self.chunks)

    def chat_stream(self, messages, temperature=None):
        for chunk in self.chunks:
            yield chunk


class ExplodingProvider:
    available = True
    last_usage = None

    def chat(self, messages, temperature=None):
        raise ExternalServiceError("炸了")

    def chat_stream(self, messages, temperature=None):
        raise ExternalServiceError("炸了")
        yield  # pragma: no cover —— 只为让它成为生成器函数


@pytest.fixture(autouse=True)
def restore_provider():
    original = chat_service._provider
    yield
    chat_service._provider = original


def _events(frames) -> list[dict]:
    events = []
    for frame in frames:
        assert frame.startswith("data: ")
        events.append(json.loads(frame[len("data: ") :].strip()))
    return events


def test_stream_emits_deltas_then_done():
    chat_service._provider = FakeStreamingProvider()

    events = _events(list(chat_service.stream_chat("在吗")))

    assert [event["type"] for event in events] == ["delta", "delta", "delta", "done"]
    assert "".join(event["text"] for event in events[:-1]) == "你好呀"
    assert events[-1]["content"] == "你好呀"
    assert events[-1]["assistant_message_id"] is not None


def test_persisted_reply_matches_the_stream_exactly():
    """完成判据：流式内容与最终落库内容一致。"""
    chat_service._provider = FakeStreamingProvider(chunks=("甲", "乙", "丙"))

    events = _events(list(chat_service.stream_chat("在吗")))
    streamed = "".join(
        event["text"] for event in events if event["type"] == "delta"
    )

    assistant = [
        message
        for message in chat_service.list_messages()
        if message["role"] == "assistant"
    ]

    assert len(assistant) == 1
    assert assistant[0]["content"] == streamed == "甲乙丙"
    assert assistant[0]["metadata"]["generation_status"] == "ok"


def test_exactly_one_assistant_row_per_turn():
    chat_service._provider = FakeStreamingProvider()

    list(chat_service.stream_chat("在吗"))

    assert [m["role"] for m in chat_service.list_messages()] == ["user", "assistant"]


def test_interrupted_stream_keeps_the_partial_and_marks_it():
    """完成判据：流式中断（关页面）不会写坏数据。"""
    chat_service._provider = FakeStreamingProvider(chunks=("半", "句"))

    generator = chat_service.stream_chat("在吗")
    first = next(generator)  # 收到第一个 delta
    assert "半" in first

    generator.close()  # 模拟客户端断开

    assistant = [
        message
        for message in chat_service.list_messages()
        if message["role"] == "assistant"
    ]

    assert len(assistant) == 1
    assert assistant[0]["content"] == "半"
    assert assistant[0]["metadata"]["generation_status"] == "interrupted"


def test_closing_after_the_reply_was_saved_does_not_write_twice():
    """已经在 done 之前落库了，再断开不能重复写一条。"""
    chat_service._provider = FakeStreamingProvider(chunks=("一",))

    generator = chat_service.stream_chat("在吗")
    next(generator)  # delta
    next(generator)  # done（此时 assistant 已落库，生成器停在 done 的 yield 上）

    generator.close()

    assistant = [
        message
        for message in chat_service.list_messages()
        if message["role"] == "assistant"
    ]
    assert len(assistant) == 1


def test_provider_error_is_an_event_not_an_exception():
    chat_service._provider = ExplodingProvider()

    events = _events(list(chat_service.stream_chat("在吗")))

    assert events[-1]["type"] == "error"
    assert "炸了" in events[-1]["detail"]

    user = [
        message
        for message in chat_service.list_messages()
        if message["role"] == "user"
    ][0]
    assert user["metadata"]["generation_status"] == "failed"


def test_empty_reply_writes_nothing():
    chat_service._provider = FakeStreamingProvider(chunks=())

    events = _events(list(chat_service.stream_chat("在吗")))

    assert [event["type"] for event in events] == ["done"]
    assert events[-1]["assistant_message_id"] is None
    assert [m["role"] for m in chat_service.list_messages()] == ["user"]


def test_default_chat_stream_falls_back_to_chat():
    """没覆盖 chat_stream 的 provider 依然可用（ABC 的默认实现）。

    这条是给测试里的 FakeProvider 用的保险：新增抽象方法会直接把它们打挂。
    """

    class PlainProvider(LLMProvider):
        def chat(self, messages, temperature=None):
            return "整段"

    assert list(PlainProvider().chat_stream([])) == ["整段"]
