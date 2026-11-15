"""图片内容进记忆（S10 剩下的那半）。

设计里的管道：**图片 → 文字 → L1 → 正常抽取** ✓。

"图能被看见"和"图能被记住"是两件事 —— 没有这一步，他看一眼就忘，
而照片 / 截图 / 课表这类消息对记忆库等于不存在 ✗。
"""

import json
import logging

import pytest

from app.core.exceptions import ExternalServiceError
from app.database.connection import get_connection
from app.llm import factory
from app.services import chat_service
from app.workflows import extraction

PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

OCR = "周三 14:00 高数期中，教三 201"


def _metadata(message_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    return json.loads(row["metadata_json"] or "{}")


class _Vision:
    name = "deepseek"
    available = True
    last_usage = None

    def __init__(self, text: str = OCR, raises: bool = False):
        self.text = text
        self.raises = raises
        self.calls = 0

    def chat(self, messages, temperature=None, json_mode=False):
        self.calls += 1
        if self.raises:
            raise ExternalServiceError("视觉挂了")
        self.messages = messages
        return self.text

    # ⚠️ **必须有这个方法。** provider 是鸭子类型的（设计裁决 Q5 的注释里写了
    # 为什么不做成抽象方法），没有它就会被当成"认不得图片"，
    # `_describe_image()` 直接返回 None —— **第一版我漏了，四条测试全红** ✗，
    # 而且日志里早就写着原因（`vision provider … 认不得图片`）。
    def build_image_message(self, text, data_url):
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }


class _Extract:
    name = "openai-compatible"
    model = "test-extract-model"
    available = True
    last_usage = None

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {"observations": [], "suggested_tags": []}
        self.prompts: list[str] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.prompts.append(messages[-1]["content"])
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.fixture
def fake_llm(monkeypatch):
    """装上假的视觉 / 抽取模型。返回 ``(vision, extract)``。"""

    def install(vision_text: str = OCR, payload: dict | None = None, raises=False):
        vision = _Vision(vision_text, raises=raises)
        extract = _Extract(payload)

        monkeypatch.setattr(
            factory,
            "get_provider",
            lambda task: vision if task == "vision" else extract,
        )
        return vision, extract

    return install


@pytest.fixture
def image_message(client, monkeypatch):
    """发一条带图的消息（聊天模型换掉，别真去打 API）。"""

    class _Chat:
        name = "deepseek"
        available = True
        last_usage = None

        def chat(self, messages, temperature=None, json_mode=False):
            return "嗯。"

        def chat_stream(self, messages, temperature=None, json_mode=False):
            yield "嗯。"

    monkeypatch.setattr(chat_service, "_provider", _Chat())
    result = chat_service.send_chat("课表出来了", image=PNG)
    return result["user_message"]["id"]


def test_the_image_is_read_into_text_and_kept_on_the_message(
    fake_llm, image_message
):
    """★ 这一步就是"被看见"→"被记住"的那一下。"""
    vision, _ = fake_llm()

    result = extraction.extract_pending()

    assert result["described_images"] == 1
    assert _metadata(image_message)["image_text"] == OCR
    assert vision.calls == 1


def test_the_image_is_read_only_once(fake_llm, image_message):
    """★ 视觉调用不便宜，重抽不该重新读图。"""
    vision, _ = fake_llm()
    extraction.extract_pending()

    # 标回"还没抽取"，模拟重抽 / 编辑后重抽 ✓
    extraction.unmark_extracted(image_message)
    extraction.extract_pending()

    assert vision.calls == 1  # ← 第二次没再读图 ✓


def test_the_extractor_sees_the_image_content(fake_llm, image_message):
    """★ 图里的内容要作为**这条消息的一部分**进提示词。"""
    _, extract = fake_llm()

    extraction.extract_pending()

    assert OCR in extract.prompts[-1]


def test_a_fact_from_the_image_is_kept_with_the_image_text_as_source(
    fake_llm, image_message
):
    """★ 图里的内容**可以当出处** —— 那是我们自己读出来、存下来的文字 ✓。"""
    fake_llm(
        payload={
            "observations": [
                {
                    "content": "高数期中考在周三 14:00，教三 201",
                    "source_text": "周三 14:00 高数期中，教三 201",
                    "confidence": 0.9,
                    "tag_ids": [],
                }
            ],
            "suggested_tags": [],
        }
    )

    result = extraction.extract_pending()

    assert result["created_observations"] == 1


def test_a_quote_that_is_neither_in_the_text_nor_in_the_image_is_dropped(
    fake_llm, image_message
):
    """不变量 5 照旧：核不到出处就丢掉 —— 图这条路也不放宽 ✓。"""
    fake_llm(
        payload={
            "observations": [
                {
                    "content": "他考了满分",
                    "source_text": "我这次考了满分",
                    "confidence": 0.9,
                    "tag_ids": [],
                }
            ],
            "suggested_tags": [],
        }
    )

    result = extraction.extract_pending()

    assert result["created_observations"] == 0
    assert result["dropped_without_source"] == 1


def test_reading_the_image_failing_does_not_break_the_batch(
    fake_llm, image_message
):
    """★ 一张图读不出来，不该让整批记忆抽取挂掉。

    `image_text` 会是空的 → 调度器下一个 tick 会再试 ✓。
    """
    fake_llm(raises=True)

    result = extraction.extract_pending()

    assert result["described_images"] == 0
    assert result["checked_messages"] == 1  # 批次照常跑完 ✓
    assert "image_text" not in _metadata(image_message)


def test_a_message_without_an_image_is_untouched(fake_llm, client, monkeypatch):
    """没有图的消息走老路 —— 一个字都不变 ✓。"""

    class _Chat:
        name = "deepseek"
        available = True
        last_usage = None

        def chat(self, messages, temperature=None, json_mode=False):
            return "嗯。"

        def chat_stream(self, messages, temperature=None, json_mode=False):
            yield "嗯。"

    monkeypatch.setattr(chat_service, "_provider", _Chat())
    result = chat_service.send_chat("我下个月要考试")
    message_id = result["user_message"]["id"]

    vision, _ = fake_llm()
    extraction.extract_pending()

    assert vision.calls == 0  # 没图就不该调视觉 ✓
    assert "image_text" not in _metadata(message_id)


def test_a_provider_that_cannot_read_images_is_loud_about_it(
    fake_llm, image_message, monkeypatch, caplog
):
    """★ **不许静默跳过。**

    第一版我的假 provider 没有 `build_image_message`，四条测试**静默地失败在
    断言上** ✗ —— 而日志里其实早就写着原因。
    所以这条测试盯的不是行为，是"**原因要看得见**"。
    """

    class _Blind:
        name = "blind"
        available = True
        last_usage = None

        def chat(self, messages, temperature=None, json_mode=False):
            raise AssertionError("认不得图片的 provider 不该被调用")

    _, extract = fake_llm()
    monkeypatch.setattr(
        factory,
        "get_provider",
        lambda task: _Blind() if task == "vision" else extract,
    )

    with caplog.at_level(logging.WARNING, logger="app.workflows.extraction"):
        result = extraction.extract_pending()

    assert result["described_images"] == 0
    assert "认不得图片" in caplog.text
