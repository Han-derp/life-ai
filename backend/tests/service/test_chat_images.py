"""聊天带图。

**背景**：`MODEL_USAGE_SPEC` §3 原本写着「图片一律先转成文字，再进入**对话**流程」，
但那条理由（"VL 强项是识别不是判断，会编"）出自 `LOCAL_MODEL_PLAN` §3.1 的
**本地 7B** 语境；R4 已经推翻该前提（云端 `deepseek-flash` 原生支持视觉）。
而且同一份 §3 的第 177–178 行就在教"图片只能放在 user 消息里""可加 detail: low"
—— 自相矛盾。**云端模型本身能看图，聊天没有理由绕 OCR。**

（R4 那条"管道"讲的是**落库**：拍课表 → 转文字 → 你确认 → L1 → 抽取。
那是录数据，和"聊天时发张图给他看"是两件事。）

这个文件守的是聊天这一侧：图片落盘、进请求、能被追问、坏数据不炸。
"""

import base64
from pathlib import Path

import pytest

from app.ai.context import assembler
from app.core.exceptions import DomainValidationError, NotFoundError
from app.services import chat_service, image_service
from app.services.image_service import IMAGES_DIR

#: 1×1 的透明 PNG（够真实：base64 解码要过 validate=True）
PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class _CaptureProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "我看到了。"):
        self.text = text
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        return self.text

    # 模拟云端（DeepSeek）：认图片 → OpenAI 的 block 数组。
    # 真实实现见 `DeepSeekProvider.build_image_message`。
    # ⚠️ 这里必须实现它：provider 是鸭子类型的，没有这个方法就会被当成
    # "认不得图片"，图片会被丢掉，下面那些断言全会红 ✗。
    def build_image_message(self, text, data_url):
        if not data_url:
            return None
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": "low"},
                },
            ],
        }


@pytest.fixture
def provider(monkeypatch):
    fake = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", fake)
    return fake


def _image_parts(messages: list[dict]) -> list[dict]:
    """把请求里所有图片部件挑出来。"""
    parts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts += [p for p in content if p.get("type") == "image_url"]
    return parts


# ---------- 落盘 ----------


def test_the_image_is_saved_and_linked(client, provider):
    result = chat_service.send_chat("这是什么？", image=PNG)

    image = result["user_message"]["metadata"]["image"]
    assert image["mime"] == "image/png"
    assert (IMAGES_DIR / image["path"]).is_file()


def test_the_saved_file_is_the_actual_bytes(client, provider):
    result = chat_service.send_chat("这是什么？", image=PNG)

    path = IMAGES_DIR / result["user_message"]["metadata"]["image"]["path"]
    assert path.read_bytes() == base64.b64decode(PNG.split(",", 1)[1])


# ---------- 进请求 ----------


def test_the_request_carries_the_image(client, provider):
    chat_service.send_chat("这是什么？", image=PNG)

    parts = _image_parts(provider.seen[-1])
    assert len(parts) == 1
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_the_image_sits_in_a_user_message(client, provider):
    """⚠️ 图片只能放在 user 消息里 —— 放 system / assistant 会被 API 拒绝（400）。"""
    chat_service.send_chat("这是什么？", image=PNG)

    for message in provider.seen[-1]:
        if isinstance(message.get("content"), list):
            assert message["role"] == "user"


def test_the_low_detail_hint_is_sent(client, provider):
    """低清图更便宜更快，而且设计明确要求带上它。"""
    chat_service.send_chat("这是什么？", image=PNG)

    assert _image_parts(provider.seen[-1])[0]["image_url"]["detail"] == "low"


def test_the_text_still_goes_along(client, provider):
    chat_service.send_chat("这是什么？", image=PNG)

    message = provider.seen[-1][-1]
    text = next(p for p in message["content"] if p["type"] == "text")
    assert "这是什么？" in text["text"]


def test_an_image_only_message_is_allowed(client, provider):
    """只发一张图、一个字不打，是自然的用法。"""
    result = chat_service.send_chat("", image=PNG)

    assert _image_parts(provider.seen[-1])
    assert result["user_message"]["content"] == ""


def test_neither_text_nor_image_is_rejected(client, provider):
    with pytest.raises(DomainValidationError):
        chat_service.send_chat("   ")


# ---------- 追问要还能看见图 ----------


def test_a_follow_up_carries_the_previous_image(client, provider):
    """★ 没有这条，"那第二行写的什么？"就答不出来 —— 功能等于半残。"""
    chat_service.send_chat("这是什么？", image=PNG)
    chat_service.send_chat("那第二行写的什么？")

    sent = provider.seen[-1]
    history = sent[1:-1]  # 去掉 system 和当前这条
    assert any(isinstance(m.get("content"), list) for m in history)


def test_only_the_last_two_images_are_reattached(client, provider):
    """更早的图只留文字占位 —— 每张 base64 之后几十上百 KB，全带上会让请求爆掉。"""
    for index in range(3):
        chat_service.send_chat(f"第 {index} 张图", image=PNG)
    chat_service.send_chat("刚才那几张说了什么？")

    assert len(_image_parts(provider.seen[-1])) == chat_service.MAX_HISTORY_IMAGES


def test_a_missing_image_file_degrades_to_text(client, provider):
    """文件被清理过时，请求不该崩 —— 降级成纯文本就行。"""
    result = chat_service.send_chat("这是什么？", image=PNG)
    path = IMAGES_DIR / result["user_message"]["metadata"]["image"]["path"]
    path.unlink()

    chat_service.send_chat("再看一眼")

    assert _image_parts(provider.seen[-1]) == []


# ---------- 坏数据不要炸 ----------


def test_a_bad_mime_is_rejected(client):
    with pytest.raises(DomainValidationError):
        image_service.save_data_url("data:application/pdf;base64,aGk=")


def test_a_broken_payload_is_rejected(client):
    with pytest.raises(DomainValidationError):
        image_service.save_data_url("data:image/png;base64,这不是 base64")


def test_an_oversized_image_is_rejected(client, monkeypatch):
    # 那张 1×1 的 PNG 有 67 字节，所以上限要压到它以下才触发得到
    monkeypatch.setattr(image_service, "MAX_IMAGE_BYTES", 10)

    with pytest.raises(DomainValidationError):
        image_service.save_data_url(PNG)


def test_a_non_data_url_is_rejected(client):
    with pytest.raises(DomainValidationError):
        image_service.save_data_url("https://example.com/a.png")


# ---------- 上下文面板的账不能算错 ----------


def test_the_token_estimate_handles_an_image(client, provider):
    """★ `str(数组)` 会得到几千个字符的假数字，面板的预算就变成撒谎 ✗。"""
    chat_service.send_chat("这是什么？", image=PNG)
    captured = provider.seen[-1][-1]

    tokens = assembler._history_tokens([captured])

    # 固定成本 + 那点文字；绝不该因为 base64 而爆掉
    assert tokens < assembler.IMAGE_TOKEN_ESTIMATE + 200


def test_the_panel_shows_a_placeholder_for_images(client, provider):
    """图既上不了面板，也不该上（它只在真实请求里发给模型）。"""
    chat_service.send_chat("这是什么？", image=PNG)
    history = chat_service.recent_history()

    rendered = assembler._render_history(history)

    assert "（一张图片）" in rendered
    assert "base64" not in rendered


# ---------- API ----------


def test_the_api_accepts_an_image(client, provider):
    body = client.post(
        "/api/v1/chat",
        json={"content": "这是什么？", "image": PNG},
    ).json()

    assert body["user_message"]["metadata"]["image"]["mime"] == "image/png"
    assert body["assistant_message"]["content"] == "我看到了。"


def test_the_api_rejects_an_empty_turn(client):
    response = client.post("/api/v1/chat", json={"content": "  "})

    assert response.status_code == 400
    assert "发张图" in response.json()["detail"]


def test_the_image_can_be_fetched_back(client, provider):
    """前端要能显示它 —— `/images/<文件>` 是本地静态目录。"""
    body = client.post(
        "/api/v1/chat",
        json={"content": "这是什么？", "image": PNG},
    ).json()
    name = body["user_message"]["metadata"]["image"]["path"]

    response = client.get(f"/images/{name}")

    assert response.status_code == 200
    assert response.content == Path(IMAGES_DIR / name).read_bytes()


# ---------- 删消息时，图片文件也要跟着走 ----------


def _send_image(provider) -> tuple[int, Path]:
    body = chat_service.send_chat("这是什么？", image=PNG)
    message = body["user_message"]
    return message["id"], Path(IMAGES_DIR) / message["metadata"]["image"]["path"]


def test_deleting_a_message_deletes_its_image_file(client, provider):
    """★ 以前只删数据库行，**文件一直留着** ✗ ——
    攒久了就是一堆谁也认不出的孤儿，而且里面全是你发过的图 ✓。
    """
    message_id, path = _send_image(provider)
    assert path.exists()

    chat_service.delete_message(message_id)

    assert not path.exists()


def test_rolling_back_deletes_the_images_in_that_range(client, provider):
    """回滚一次会删掉**一批**消息 —— 那批里的图也要清 ✓。"""
    first = chat_service.send_chat("先留一条")["user_message"]["id"]
    _, path = _send_image(provider)
    assert path.exists()

    deleted = chat_service.delete_messages_after(first)

    assert deleted >= 1
    assert not path.exists()


def test_deleting_a_text_message_is_unaffected(client, provider):
    """没图的消息照旧 —— 不该因为这次改动出任何岔子 ✓。"""
    message_id = chat_service.send_chat("一句普通的话")["user_message"]["id"]

    chat_service.delete_message(message_id)

    with pytest.raises(NotFoundError):
        chat_service.get_message(message_id)


def test_a_bogus_path_cannot_delete_anything_outside_the_images_dir(
    client, provider, tmp_path
):
    """⚠️ **安全**：`metadata_json` 是可被编辑的数据 ✗。

    万一里面塞了绝对路径或 `../../`，`delete_saved` 也**只能**在
    images 目录里动手 ✓ —— 外面那个文件必须原封不动 ✓。
    """
    from app.services import image_service

    outside = tmp_path / "不该被删.txt"
    outside.write_text("原封不动", encoding="utf-8")

    # 绝对路径
    assert image_service.delete_saved({"path": str(outside)}) is False
    # 相对穿越
    assert image_service.delete_saved({"path": f"../../{outside.name}"}) is False

    assert outside.read_text(encoding="utf-8") == "原封不动"



def test_history_images_are_re_attached_for_follow_up(client, provider):
    """追问图片时，最近的历史图片应继续随上下文发送。"""
    chat_service.send_chat("这是什么？", image=PNG)
    chat_service.send_chat("那第二行写的什么？")

    history = provider.seen[-1][1]
    assert isinstance(history["content"], list)
    assert history["content"][1]["type"] == "image_url"
