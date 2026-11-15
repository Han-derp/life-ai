"""把「现在几点」给他 —— 但**不能**因此废掉 KV 缓存。

这条需求被两个约束夹着，而且方向相反：

- **用户要生活管理监督** → 他必须知道现在几点。
  真实对话里他答的是"时间我这边看不到实时时钟……我不能瞎报" ——
  他没错，是设计漏了：没有时间，他说不了"该睡了""别熬到后半夜"。
- **S1 的缓存修复** → `now` **不能**进 system prompt。
  进去一次，它后面的一切（包括整段历史）都会因前缀不匹配而失去缓存，
  实测能差 20–100 倍费用。

解法：把时间附在**最新一条用户消息**后面。那条消息每次请求本来就是新的。

⚠️ 所以这个文件里 **`test_the_note_is_not_in_the_system_prompt` 是护栏**，
它守的是费用；`test_the_note_goes_onto_the_latest_user_message` 守的是功能。
两个都不能少 —— 谁"顺手"把时间挪进 system prompt，这条就会红。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.config import APP_TIMEZONE
from app.core.time_utils import current_time_note
from app.services import chat_service


class _CaptureProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "嗯。"):
        self.text = text
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        return self.text


@pytest.fixture
def provider(monkeypatch):
    fake = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", fake)
    return fake


# ---------- 时间本身 ----------


def test_time_note_has_date_weekday_and_local_time():
    moment = datetime(2026, 10, 3, 15, 42, tzinfo=ZoneInfo(APP_TIMEZONE))

    note = current_time_note(APP_TIMEZONE, moment)

    assert "2026-10-03" in note
    assert "周六" in note
    assert "15:42" in note


def test_time_note_uses_local_time_not_utc():
    """UTC 15:42 在 Asia/Shanghai 是 23:42 —— 说反了就会把他教错。"""
    moment = datetime(2026, 10, 3, 15, 42, tzinfo=ZoneInfo("UTC"))

    note = current_time_note(APP_TIMEZONE, moment)

    assert "23:42" in note
    assert "15:42" not in note


# ---------- 功能：他必须看得到 ----------


def test_the_note_goes_onto_the_latest_user_message(client, provider):
    chat_service.send_chat("现在几点了？")

    last = provider.seen[-1][-1]

    assert last["role"] == "user"
    assert last["content"].startswith("现在几点了？")
    assert "（现在：" in last["content"]


# ---------- 护栏：不能废掉缓存 ----------


def _has_time_note(text: str) -> bool:
    """判定"真正的时间备注"，而不是 `behavior_rules.md` 里那句「（现在：…）」示例。

    ⚠️ 一开始我用 `"（现在：" in text` 判断，结果三条测试全红——
    因为 system prompt 里的行为规则**本身就提到了这个字面形式**。
    所以要往后多看几位：真正的时间备注后面跟的是日期数字。
    """
    marker = "（现在："
    index = text.find(marker)
    while index != -1:
        following = text[index + len(marker) : index + len(marker) + 4]
        if following.isdigit():
            return True
        index = text.find(marker, index + 1)
    return False


def test_the_note_is_not_in_the_system_prompt(client, provider):
    """**缓存护栏。**

    时间一旦进 system prompt，它后面的一切（含整段历史）都会失去 KV 缓存。
    这个测试红掉，就意味着费用可能涨 20–100 倍。
    """
    chat_service.send_chat("现在几点了？")

    system_prompt = provider.seen[-1][0]["content"]

    assert not _has_time_note(system_prompt)


def test_the_saved_message_stays_clean(client, provider):
    """落库的是原文：否则历史里会越堆越多「（现在：…）」。"""
    chat_service.send_chat("现在几点了？")

    saved = client.get("/api/v1/chat/messages?limit=10").json()
    user_messages = [m for m in saved if m["role"] == "user"]

    assert user_messages[-1]["content"] == "现在几点了？"


def test_history_of_earlier_turns_carries_no_stale_time(client, provider):
    """多轮之后，**历史里的旧消息不能带着过去的时间** ——
    否则他会把"昨天 23:00"当成"现在"。"""
    chat_service.send_chat("第一句")
    chat_service.send_chat("第二句")

    messages = provider.seen[-1]
    note_owners = [
        m
        for m in messages
        if m["role"] == "user" and _has_time_note(str(m.get("content", "")))
    ]

    assert len(note_owners) == 1  # 只有最新那条
    assert note_owners[0] is messages[-1]
