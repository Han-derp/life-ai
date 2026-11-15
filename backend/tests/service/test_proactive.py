"""S8 主动联系（机制一 + 机制二）。

对应 DEVELOPMENT_PLAN v2.0 · S8 的完成判据。**全部用假时钟**，不真的等三天。

- 模拟沉默 3 天 / 7 天 / 1 个月，各自行为正确
- 机制一**最多 2 次**，第 3 次绝不出现
- 所有文案里搜不到禁止词
- 用户回一句后她立刻退回，不再追问
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.config import APP_TIMEZONE
from app.core.time_utils import to_utc_iso
from app.database.connection import get_connection
from app.workflows import proactive


def _local(day: str, hhmm: str) -> datetime:
    """给定**当地时间**，返回对应的 UTC 时刻。

    测试里坚持写当地时刻，是因为"沉默几天"按当地自然日算——
    直接用 UTC 会在跨日那几小时里骗过自己。
    """
    naive = datetime.fromisoformat(f"{day}T{hhmm}")
    return naive.replace(tzinfo=ZoneInfo(APP_TIMEZONE)).astimezone(timezone.utc)


def _user_message_at(moment: datetime) -> None:
    """直接写一条用户消息（带指定时间），用来伪造沉默。"""
    with get_connection() as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, model, metadata_json, created_at
            ) VALUES (?, 'user', '在吗', NULL, NULL, ?)
            """,
            (conversation_id, to_utc_iso(moment)),
        )


class _FakeProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "我想你了，这段时间过得怎么样。"):
        self.text = text

    def chat(self, messages, temperature=None):
        return self.text


class _UnavailableProvider:
    available = False
    last_usage = None

    def chat(self, messages, temperature=None):  # pragma: no cover
        raise AssertionError("不可用的 provider 不该被调用")


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider())


# ---------- 什么都不该发生的时候 ----------


def test_nothing_happens_without_any_conversation():
    assert proactive.next_action(_local("2026-06-10", "09:00")) is None


def test_two_days_of_silence_is_not_enough():
    _user_message_at(_local("2026-06-01", "21:00"))

    # 当地 09:00：还没到日常惦记的时刻；沉默也只有 2 天
    assert proactive.next_action(_local("2026-06-03", "09:00")) is None


# ---------- 机制一：沉默升级 ----------


def test_silent_three_days_sends_one_light_signal():
    _user_message_at(_local("2026-06-01", "21:00"))

    action = proactive.next_action(_local("2026-06-04", "09:00"))

    assert action["action"] == "send"
    assert action["kind"] == proactive.KIND_SILENCE_SHORT
    assert action["silent_days"] == 3


def test_silent_seven_days_sends_the_longer_message():
    _user_message_at(_local("2026-06-01", "21:00"))
    proactive.tick(_local("2026-06-04", "09:00"))

    action = proactive.next_action(_local("2026-06-08", "09:00"))

    assert action["kind"] == proactive.KIND_SILENCE_LONG
    assert action["silent_days"] == 7


def test_mechanism_one_stops_after_two_times():
    """完成判据：机制一最多 2 次，第 3 次绝不出现。"""
    _user_message_at(_local("2026-06-01", "21:00"))

    first = proactive.tick(_local("2026-06-04", "09:00"))
    second = proactive.tick(_local("2026-06-08", "09:00"))

    assert first["kind"] == proactive.KIND_SILENCE_SHORT
    assert second["kind"] == proactive.KIND_SILENCE_LONG

    # 之后无论过多久，都不该出现第 3 次（30 天之前也不会变成小作文）
    for day in ("2026-06-15", "2026-06-25", "2026-06-29"):
        assert proactive.tick(_local(day, "09:00")) is None

    kinds = [item["kind"] for item in proactive.list_contacts()]
    assert kinds.count(proactive.KIND_SILENCE_SHORT) == 1
    assert kinds.count(proactive.KIND_SILENCE_LONG) == 1


# ---------- 机制二：留存 ----------


def test_essay_is_written_after_a_month_but_not_delivered():
    """完成判据：沉默 1 个月的行为正确——写下来，但**先不给**。"""
    _user_message_at(_local("2026-06-01", "21:00"))

    essay = proactive.tick(_local("2026-07-02", "09:00"))  # 31 天

    assert essay["kind"] == proactive.KIND_ESSAY
    assert essay["delivered"] is False
    assert essay["delivered_at"] is None


def test_pending_essay_is_delivered_when_the_user_comes_back():
    _user_message_at(_local("2026-06-01", "21:00"))
    essay = proactive.tick(_local("2026-07-02", "09:00"))
    assert [item["id"] for item in proactive.pending_contacts()] == [essay["id"]]

    # 你回来了
    _user_message_at(_local("2026-07-02", "10:00"))
    delivered = proactive.tick(_local("2026-07-02", "11:00"))

    assert delivered["id"] == essay["id"]
    assert delivered["delivered"] is True
    assert proactive.pending_contacts() == []


def test_essay_is_not_written_twice_in_the_same_silence():
    _user_message_at(_local("2026-06-01", "21:00"))
    proactive.tick(_local("2026-07-02", "09:00"))

    assert proactive.tick(_local("2026-07-10", "09:00")) is None
    assert len(proactive.list_contacts()) == 1


# ---------- 用户回一句就退回 ----------


def test_replying_resets_the_silence_period():
    """完成判据：用户回一句后她立刻退回，不再追问。"""
    _user_message_at(_local("2026-06-01", "21:00"))
    proactive.tick(_local("2026-06-04", "09:00"))  # 3 天信号

    _user_message_at(_local("2026-06-04", "10:00"))  # 你回了一句

    assert proactive.tick(_local("2026-06-05", "09:00")) is None
    assert proactive.list_contacts()[0]["responded_at"] is not None


def test_a_new_silence_period_gets_two_more_chances():
    """回话是"退回"，不是"用光机会"。"""
    _user_message_at(_local("2026-06-01", "21:00"))
    proactive.tick(_local("2026-06-04", "09:00"))
    _user_message_at(_local("2026-06-04", "10:00"))
    _user_message_at(_local("2026-06-10", "10:00"))

    again = proactive.next_action(_local("2026-06-13", "09:00"))

    assert again["kind"] == proactive.KIND_SILENCE_SHORT


# ---------- 文案安全（硬约束） ----------


def test_forbidden_phrases_are_replaced_with_safe_text():
    assert (
        proactive.sanitize("你都几天没来了", proactive.KIND_SILENCE_SHORT) == "在忙？"
    )
    assert proactive.sanitize("你怎么不见了", proactive.KIND_CARE) == "在忙什么？"
    assert (
        proactive.sanitize("你去哪了", proactive.KIND_SILENCE_LONG) == "不打扰你，我在这。"
    )


def test_unsafe_essay_is_dropped_rather_than_shipped():
    """小作文没有安全文案可退，所以宁可这条不发。"""
    assert proactive.sanitize("你没来多久，我一直在等", proactive.KIND_ESSAY) is None


def test_safe_text_passes_through():
    assert proactive.sanitize("在忙？", proactive.KIND_CARE) == "在忙？"


def test_generated_text_goes_through_the_sanitizer(monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider("你终于想起我了"))
    _user_message_at(_local("2026-06-01", "21:00"))

    sent = proactive.tick(_local("2026-06-04", "09:00"))

    assert sent["content"] == "在忙？"  # 被兜底文案换掉了


def test_every_stored_contact_is_free_of_forbidden_phrases(monkeypatch):
    """完成判据：所有文案里搜不到禁止词。"""
    monkeypatch.setattr(
        proactive,
        "_provider",
        _FakeProvider("早啊，今天想干嘛"),
    )
    _user_message_at(_local("2026-06-01", "21:00"))
    proactive.tick(_local("2026-06-04", "09:00"))
    proactive.tick(_local("2026-06-08", "09:00"))
    proactive.tick(_local("2026-07-02", "09:00"))

    contacts = proactive.list_contacts()
    assert contacts  # 确实产生了记录

    for contact in contacts:
        for phrase in proactive.FORBIDDEN_PHRASES:
            assert phrase not in contact["content"], (phrase, contact["content"])


# ---------- 没有模型时的兜底 ----------


def test_fallback_text_is_used_without_a_provider(monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _UnavailableProvider())
    _user_message_at(_local("2026-06-01", "21:00"))

    sent = proactive.tick(_local("2026-06-04", "09:00"))

    assert sent["content"] == "在忙？"


def test_essay_is_skipped_without_a_provider(monkeypatch):
    """小作文没法用罐头文案顶替——宁可先不写。"""
    monkeypatch.setattr(proactive, "_provider", _UnavailableProvider())
    _user_message_at(_local("2026-06-01", "21:00"))

    assert proactive.tick(_local("2026-07-02", "09:00")) is None
    assert proactive.list_contacts() == []


# ---------- 活跃期的日常惦记 ----------


def test_daily_care_fires_after_the_slot():
    _user_message_at(_local("2026-06-01", "09:00"))

    action = proactive.next_action(_local("2026-06-01", "14:00"))

    assert action["kind"] == proactive.KIND_CARE
    assert action["slot"] == "noon"


def test_daily_care_fires_once_per_slot_per_day():
    _user_message_at(_local("2026-06-01", "09:00"))
    proactive.tick(_local("2026-06-01", "14:00"))

    assert proactive.next_action(_local("2026-06-01", "15:00")) is None


def test_daily_care_downgrades_after_two_unanswered():
    """连续两次没回 → 降频为一天一次（只留晚上那次）。"""
    _user_message_at(_local("2026-06-01", "09:00"))
    proactive.tick(_local("2026-06-01", "14:00"))
    proactive.tick(_local("2026-06-01", "21:00"))

    assert proactive.next_action(_local("2026-06-02", "14:00")) is None
    evening = proactive.next_action(_local("2026-06-02", "21:00"))
    assert evening["kind"] == proactive.KIND_CARE
    assert evening["slot"] == "evening"


def test_deliver_contact_is_idempotent():
    _user_message_at(_local("2026-06-01", "21:00"))
    essay = proactive.tick(_local("2026-07-02", "09:00"))

    first = proactive.deliver_contact(essay["id"], _local("2026-07-02", "08:00"))
    second = proactive.deliver_contact(essay["id"], _local("2026-07-03", "08:00"))

    assert first["delivered_at"] == second["delivered_at"]


def test_deliver_unknown_contact_raises():
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        proactive.deliver_contact(999)


def test_silence_uses_local_calendar_days():
    """用自然日而不是 24 小时：昨晚说过话，今天不该被催。"""
    _user_message_at(_local("2026-06-01", "23:30"))

    # 只过了 9 个多小时，但已经是第二天当地早上
    assert proactive.next_action(_local("2026-06-02", "09:00")) is None
    # 日历上满 3 天才发
    assert proactive.next_action(_local("2026-06-04", "09:00"))["kind"] == (
        proactive.KIND_SILENCE_SHORT
    )
