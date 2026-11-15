"""设计裁决 R2①③：她主动找你时，必须和平时说话**用同一套东西**。

① **上下文** —— 主动消息必须走 `plan_for_request()`（也就是 `get_context_blocks()`）。
   裁决原话："如果主动联系自己拼一个短 prompt，她会'在主动找你的时候显得特别空'
   —— 而那时恰恰是你最需要她像个人的时刻。"
② **可分辨** —— 主动消息要写进 `messages` 并带 `source` / `mechanism` 标记，
   否则以后分不清"她主动说的"和"回复你说的"。

（这里也用显式 stub —— 不依赖"反正没配 Key 会走兜底"那个坑。）
"""

import pytest

from app.scheduler import clock
from app.services import chat_service, context_exclusion_service
from app.workflows import proactive


class _CaptureProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "在忙什么？"):
        self.text = text
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        return self.text


@pytest.fixture
def provider(monkeypatch):
    fake = _CaptureProvider()
    monkeypatch.setattr(proactive, "_provider", fake)
    return fake


def _send():
    return proactive._send(proactive.KIND_CARE, "noon", 0, clock.now_utc())


# ---------- ① 上下文 ----------


def test_proactive_uses_the_shared_context_blocks(client, provider):
    _send()

    system_prompt = provider.seen[-1][0]["content"]

    # ⚠️ 断言规则**内容**，不是标题 —— 标题会随口吻重写而变（那是正当的）。
    assert "你欠我" in system_prompt  # rules 块里的硬禁止项
    assert "【当前真实生活状态】" in system_prompt  # today:meta 块
    assert "行动搭档" in system_prompt  # persona 块


def test_the_shape_matches_a_chat_request(client, provider):
    """system + 历史 + 最后一条 user —— 和聊天请求同一个形状。"""
    _send()

    messages = provider.seen[-1]

    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    # proactive.md 里注入了 kind，所以写作指令在最后一条上
    assert "care" in messages[-1]["content"]


def test_an_excluded_block_is_hidden_from_proactive_too(client, provider):
    """S2 面板的排除开关，对她主动找你时**同样生效**。

    这是"走同一套上下文"最直接的好处：隐私开关不需要为主动联系再实现一遍。
    """
    context_exclusion_service.set_excluded("today:schedule", True)

    _send()

    assert "【今日日程】" not in provider.seen[-1][0]["content"]


def test_proactive_does_not_spend_a_once_exclusion(client, provider):
    """`exclude_once` 的语义是"下次**聊天**不发"，她主动说话不该把它花掉。"""
    context_exclusion_service.set_once("today:schedule", True)

    _send()

    assert "today:schedule" in context_exclusion_service.list_once_ids()


# ---------- ② 可分辨 ----------


def test_proactive_message_lands_in_the_chat_history(client, provider):
    """她主动说的话必须出现在聊天记录里 —— 否则"她找过你"等于没发生。"""
    _send()

    messages = client.get("/api/v1/chat/messages?limit=10").json()

    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "在忙什么？"


def test_proactive_message_carries_a_mechanism_marker(client, provider):
    """没有这个标记，以后就分不清"她主动说的"和"回复你说的"。"""
    _send()

    metadata = client.get("/api/v1/chat/messages?limit=10").json()[-1]["metadata"]

    assert metadata["source"] == "proactive"
    assert metadata["mechanism"] == proactive.KIND_CARE


def test_a_normal_reply_is_not_marked_as_proactive(client, provider, monkeypatch):
    """反过来也要成立：普通回复不带这个标记，否则标记本身就没意义了。"""
    monkeypatch.setattr(chat_service, "_provider", provider)

    chat_service.send_chat("在吗")

    metadata = client.get("/api/v1/chat/messages?limit=10").json()[-1]["metadata"] or {}

    assert metadata.get("source") != "proactive"
