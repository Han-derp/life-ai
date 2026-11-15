"""历史消息要带**绝对本地时间**。

用户实测：她说"昨天聊的那个"时，模型翻到旧消息**不知道哪条是昨天** ✗
（原来只有**当前这一句**末尾挂着「（现在：…）」✓）。

⚠️ 必须是**绝对**时间 —— 相对时间每天变 ✓ → 同一批历史渲染出不同字节 ✓
→ KV 缓存前缀全废 ✓ → 变慢变贵 ✓。
"""

from app.core.config import APP_TIMEZONE
from app.core.time_utils import utc_to_local_iso
from app.services import chat_service

CREATED = "2026-10-04T15:20:00+00:00"


def _local(created: str) -> str:
    local = utc_to_local_iso(created, APP_TIMEZONE)
    return local[5:16].replace("T", " ")


def test_a_history_message_carries_its_own_local_time(client):
    """★ 每条历史消息都该带着**它自己**的时间 ✓。"""
    chat_service._save_message("user", "我下个月要考试")
    chat_service._save_message("user", "在吗")

    users = [
        item for item in chat_service.recent_history() if item["role"] == "user"
    ]

    assert len(users) == 2
    assert all(item["content"].startswith("[") for item in users)


def test_the_stamp_is_absolute_not_relative(client):
    """★ 相对时间会**毁掉 KV 缓存前缀** ✗（它每天都不一样 ✓）。"""
    stamp = chat_service._stamp(CREATED, "你好")

    assert stamp == f"[{_local(CREATED)}] 你好"
    assert "昨天" not in stamp
    assert "天前" not in stamp


def test_the_stamp_is_byte_stable(client):
    """同一条消息渲染两次必须**逐字节一样** —— 这是不破坏缓存的前提 ✓。"""
    assert chat_service._stamp(CREATED, "你好") == chat_service._stamp(CREATED, "你好")


def test_an_image_message_is_left_untouched(client):
    """带图的消息 content 是**数组** —— 没地方放前缀 ✓，原样返回 ✓。"""
    payload = [{"type": "text", "text": "这是什么？"}]

    assert chat_service._stamp(CREATED, payload) is payload


def test_a_missing_timestamp_does_not_crash(client):
    """时间缺失/异常时**退回原文** ✓ —— 不该让整次请求崩掉 ✓。"""
    assert chat_service._stamp(None, "你好") == "你好"
    assert chat_service._stamp("不是时间", "你好") == "你好"
