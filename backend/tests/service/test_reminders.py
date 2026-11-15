"""站内提醒 ✓（2026-10-05 第三轮 ✓）。

## 锁的是什么

- **算出来的** ✓，不是存表 ✓ → 处理完**自己消失** ✓（不需要"已读"✗）
- **三类来源** ✓：过期 ✓ / 今天到期 ✓ / 跑太久的步骤 ✓
- **最多 5 条** ✓，按紧急度排 ✓（"一次堆十条提醒等于没提醒" ✓）
- ⚠️ **措辞不指责** ✓ —— 步骤跑太久那条说的是"要么继续要么暂停" ✓，
  不是"你怎么还没做完" ✗
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.core import config
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.schemas.deadline import DeadlineCreate
from app.services import (
    action_plan_service,
    action_step_service,
    deadline_service,
    reminder_service,
)


def _past_local(minutes_ago: int) -> str:
    """N 分钟前的**本地**时间字符串 ✓（按**应用的**时区 ✓，不是机器的 ✗）。

    ⚠️ 这里踩过一次 ✓：`astimezone()` 用的是**操作系统**的时区 ✓，
    而 `create_deadline` 用的是 `APP_TIMEZONE` ✓ —— 两者不一致时，
    "30 分钟前"会被算成"8 小时后" ✗，测试就假红了 ✓。
    """
    moment = utc_now() - timedelta(minutes=minutes_ago)
    return moment.astimezone(ZoneInfo(config.APP_TIMEZONE)).strftime("%Y-%m-%dT%H:%M")


def test_no_reminders_on_a_fresh_database(client):
    assert reminder_service.list_reminders() == []


def test_an_overdue_deadline_is_high_urgency(client):
    deadline_service.create_deadline(
        DeadlineCreate(title="交实验报告", due_at_local=_past_local(60 * 30))
    )

    reminders = reminder_service.list_reminders()

    assert len(reminders) == 1
    assert reminders[0]["kind"] == "deadline_overdue"
    assert reminders[0]["urgency"] == "high"
    assert "过期" in reminders[0]["title"]
    # ⚠️ 每条都必须**带一个动作** ✓ —— 只说"你过期了"等于骂人 ✗
    assert reminders[0]["action"]


def test_a_deadline_due_today_is_medium_urgency(client):
    """★ 今天到期 ✓ —— 用一个"今天 23:59"的本地时间 ✓。"""
    today = utc_now().astimezone(ZoneInfo(config.APP_TIMEZONE)).strftime("%Y-%m-%d")
    deadline_service.create_deadline(
        DeadlineCreate(title="今天要交", due_at_local=f"{today}T23:59")
    )

    reminders = reminder_service.list_reminders()

    assert [item["kind"] for item in reminders] == ["deadline_today"]
    assert reminders[0]["urgency"] == "medium"


def test_a_completed_deadline_is_not_reminded(client):
    deadline = deadline_service.create_deadline(
        DeadlineCreate(title="已经做完的", due_at_local=_past_local(120))
    )
    deadline_service.complete_deadline(deadline["id"])

    assert reminder_service.list_reminders() == []


def test_a_step_stuck_in_active_is_reminded_gently(client):
    """★ 措辞不能是指责 ✓（"你怎么还没做完" ✗）。"""
    plan = action_plan_service.create_plan(
        ActionPlanCreate(title="写报告", steps=[ActionStep(title="整理数据", minutes=20)])
    )
    step = plan["executable_steps"][0]
    action_step_service.start_step(plan["id"], step["id"])

    # 把开始时间挪到 6 小时前 ✓（超过 4 小时的门槛 ✓）
    with get_connection() as conn:
        conn.execute(
            "UPDATE action_plan_steps SET started_at = ? WHERE id = ?",
            (to_utc_iso(utc_now() - timedelta(hours=6)), step["id"]),
        )

    reminders = reminder_service.list_reminders()

    assert [item["kind"] for item in reminders] == ["step_stalled"]
    assert "要么继续" in reminders[0]["action"]
    assert "懒" not in reminders[0]["title"]
    assert "还没" not in reminders[0]["title"]


def test_at_most_five_and_sorted_by_urgency(client):
    for index in range(7):
        deadline_service.create_deadline(
            DeadlineCreate(
                title=f"过期 {index}", due_at_local=_past_local(60 * (index + 1))
            )
        )

    reminders = reminder_service.list_reminders()

    assert len(reminders) == 5
    urgencies = [item["urgency"] for item in reminders]
    assert urgencies == sorted(urgencies, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x])


def test_the_http_layer_returns_a_list(client):
    deadline_service.create_deadline(
        DeadlineCreate(title="过期的事", due_at_local=_past_local(60))
    )

    body = client.get("/api/v1/reminders").json()

    assert isinstance(body, list)
    assert body[0]["kind"] == "deadline_overdue"
