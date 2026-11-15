"""S8 · B 类基线预警。

- **只有 2 项能触发**（``late_night`` / ``deadline_stall``）——
  而且这条规则写在表的 CHECK 约束上，不是写在注释里
- 必须**连续 3 天以上偏离常态**
- 单日熬夜不报；样本太少不报（宁可漏报，不要误报）
- 说出口的第一句是「我注意到……怎么了？」
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.config import APP_TIMEZONE
from app.core.exceptions import NotFoundError
from app.core.time_utils import to_utc_iso
from app.database.connection import get_connection
from app.workflows import baseline


def _local(day: str, hhmm: str) -> datetime:
    naive = datetime.fromisoformat(f"{day}T{hhmm}")
    return naive.replace(tzinfo=ZoneInfo(APP_TIMEZONE)).astimezone(timezone.utc)


NOW = _local("2026-06-20", "22:00")
TODAY = date(2026, 6, 20)


def _day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def _user_message_at(moment: datetime) -> None:
    with get_connection() as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, model, metadata_json, created_at
            ) VALUES (?, 'user', '在', NULL, NULL, ?)
            """,
            (conversation_id, to_utc_iso(moment)),
        )


def _baseline_days(hhmm: str = "21:00") -> None:
    """造出"常态"：基线窗口里的每一天都在 hhmm 说一句话。"""
    for offset in range(
        baseline.BASELINE_EXCLUDE_RECENT_DAYS,
        baseline.BASELINE_WINDOW_DAYS,
    ):
        _user_message_at(_local(_day(offset), hhmm))


class _Stub:
    available = True
    last_usage = None

    def __init__(self, text: str = "我注意到你最近睡得晚，怎么了？"):
        self.text = text

    def chat(self, messages, temperature=None):
        return self.text


class _Unavailable:
    available = False
    last_usage = None

    def chat(self, messages, temperature=None):  # pragma: no cover
        raise AssertionError("不可用的 provider 不该被调用")


# ---------- 作息：late_night ----------


def test_no_alert_without_enough_baseline():
    """样本太少就不说——这是防误报的第一道闸。"""
    _user_message_at(_local(_day(1), "01:00"))

    assert baseline.baseline_hour(NOW) is None
    assert baseline.detect_late_night(NOW) is None


def test_single_late_night_is_not_enough():
    """单日熬夜不报：那不是"偏离"，那只是某一天。"""
    _baseline_days()
    _user_message_at(_local(_day(1), "01:30"))

    assert baseline.detect_late_night(NOW) is None


def test_two_late_nights_are_not_enough():
    _baseline_days()
    for offset in (1, 2):
        _user_message_at(_local(_day(offset), "01:00"))

    assert baseline.detect_late_night(NOW) is None


def test_three_consecutive_late_nights_trigger_the_alert():
    _baseline_days()
    for offset in (1, 2, 3):
        _user_message_at(_local(_day(offset), "01:00"))

    finding = baseline.detect_late_night(NOW)

    assert finding["kind"] == baseline.KIND_LATE_NIGHT
    assert finding["baseline_hour"] == 21.0
    assert len(finding["recent"]) == 3
    # 凌晨 1 点算"前一天深夜"，所以是 25 点而不是 1 点
    assert all(item["hour"] == 25.0 for item in finding["recent"])


def test_a_missing_day_breaks_the_streak():
    """空一天是"沉默"，不是"晚睡"——那是机制一的活，别两套机制抢一件事。"""
    _baseline_days()
    for offset in (1, 3):
        _user_message_at(_local(_day(offset), "01:00"))

    assert baseline.detect_late_night(NOW) is None


def test_a_small_drift_is_not_a_drift():
    _baseline_days("22:00")
    for offset in (1, 2, 3):
        _user_message_at(_local(_day(offset), "22:30"))

    assert baseline.detect_late_night(NOW) is None


def test_early_morning_counts_as_late_not_early():
    """凌晨 1 点必须被当成"很晚"，而不是"很早"——这是个很容易写反的地方。"""
    _baseline_days("22:00")
    for offset in (1, 2, 3):
        _user_message_at(_local(_day(offset), "02:00"))

    assert baseline.detect_late_night(NOW)["kind"] == baseline.KIND_LATE_NIGHT


# ---------- DDL：deadline_stall ----------


def _insert_deadline(due: datetime, title: str = "交报告", parent_id=None) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO deadlines (title, due_at, parent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, to_utc_iso(due), parent_id, to_utc_iso(NOW), to_utc_iso(NOW)),
        )
        return cursor.lastrowid


def _completed_pomodoro(deadline_id: int, ended_at: datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pomodoro_sessions (
                started_at, ended_at, planned_minutes, status,
                deadline_id, created_at, updated_at
            ) VALUES (?, ?, 25, 'completed', ?, ?, ?)
            """,
            (
                to_utc_iso(ended_at - timedelta(minutes=25)),
                to_utc_iso(ended_at),
                deadline_id,
                to_utc_iso(NOW),
                to_utc_iso(NOW),
            ),
        )


def test_deadline_due_soon_without_progress_triggers_the_alert():
    _insert_deadline(NOW + timedelta(hours=24))

    finding = baseline.detect_deadline_stall(NOW)

    assert finding["kind"] == baseline.KIND_DEADLINE_STALL
    assert finding["title"] == "交报告"
    assert "due_at_local" in finding


def test_deadline_with_recent_progress_is_not_a_stall():
    deadline_id = _insert_deadline(NOW + timedelta(hours=24))
    _completed_pomodoro(deadline_id, NOW - timedelta(days=1))

    assert baseline.detect_deadline_stall(NOW) is None


def test_deadline_due_far_away_is_not_urgent():
    _insert_deadline(NOW + timedelta(days=10))

    assert baseline.detect_deadline_stall(NOW) is None


def test_completed_deadline_is_ignored():
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO deadlines (
                title, due_at, completion_status, completed_at, created_at, updated_at
            ) VALUES ('交报告', ?, 'completed', ?, ?, ?)
            """,
            (
                to_utc_iso(NOW + timedelta(hours=24)),
                to_utc_iso(NOW),
                to_utc_iso(NOW),
                to_utc_iso(NOW),
            ),
        )

    assert baseline.detect_deadline_stall(NOW) is None


def test_progress_on_a_subtask_counts():
    parent = _insert_deadline(NOW + timedelta(hours=24), "交报告")
    child = _insert_deadline(NOW + timedelta(hours=12), "写初稿", parent_id=parent)
    _completed_pomodoro(child, NOW - timedelta(days=1))

    assert baseline.detect_deadline_stall(NOW) is None


# ---------- 记账：不重复提醒 / 说出口 / 处理掉 ----------


def test_scan_records_once_and_does_not_repeat():
    _insert_deadline(NOW + timedelta(hours=24))

    first = baseline.scan(NOW)
    second = baseline.scan(NOW + timedelta(hours=1))

    assert len(first) == 1
    assert second == []  # 同一段偏离不重复记
    assert len(baseline.list_alerts()) == 1


def test_dismiss_allows_detecting_it_again():
    _insert_deadline(NOW + timedelta(hours=24))
    alert = baseline.scan(NOW)[0]

    baseline.dismiss_alert(alert["id"], NOW)

    assert baseline.list_alerts() == []
    assert len(baseline.list_alerts(include_dismissed=True)) == 1
    assert len(baseline.scan(NOW + timedelta(minutes=5))) == 1


def test_voice_uses_the_noticing_opening(monkeypatch):
    """完成判据：第一句是「我注意到……怎么了？」"""
    monkeypatch.setattr(baseline, "_provider", _Unavailable())
    _insert_deadline(NOW + timedelta(hours=24))
    alert = baseline.scan(NOW)[0]

    voiced = baseline.voice_alert(alert["id"], NOW)

    assert voiced["voiced"] is True
    assert voiced["content"].startswith("我注意到")
    assert voiced["content"].endswith("怎么了？")


def test_voice_is_idempotent(monkeypatch):
    monkeypatch.setattr(baseline, "_provider", _Unavailable())
    _insert_deadline(NOW + timedelta(hours=24))
    alert = baseline.scan(NOW)[0]

    first = baseline.voice_alert(alert["id"], NOW)
    second = baseline.voice_alert(alert["id"], NOW + timedelta(hours=3))

    assert first["content"] == second["content"]
    assert first["voiced_at"] == second["voiced_at"]


def test_voiced_text_is_sanitized(monkeypatch):
    """禁止项对基线预警同样生效。"""
    monkeypatch.setattr(baseline, "_provider", _Stub("你最近状态下滑了"))
    _insert_deadline(NOW + timedelta(hours=24))
    alert = baseline.scan(NOW)[0]

    voiced = baseline.voice_alert(alert["id"], NOW)

    assert "状态下滑" not in voiced["content"]
    assert voiced["content"].startswith("我注意到")


def test_only_two_kinds_can_trigger():
    """「只有 2 项能触发」是数据库约束，不是文档约定。"""
    import sqlite3

    with get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO baseline_alerts (
                    kind, detail_json, detected_at, created_at
                ) VALUES ('third_thing', '{}', ?, ?)
                """,
                (to_utc_iso(NOW), to_utc_iso(NOW)),
            )


def test_unknown_alert_raises():
    with pytest.raises(NotFoundError):
        baseline.voice_alert(999)
