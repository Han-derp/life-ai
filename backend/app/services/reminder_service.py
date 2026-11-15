"""站内提醒 ✓ —— **不存表、不跑定时器** ✗（2026-10-05 第三轮 ✓）。

## 为什么是"算出来的"，不是"存起来的"

"现在该提醒他什么" ✓ 完全可以从**已有数据**算出来：
过期的 DDL ✓ 今天要交的 ✓ 一个跑了太久的步骤 ✓。

存成 `reminders` 表会立刻引入两个新问题 ✗：
**谁**负责把它标成已读 ✓？**谁**去清已经过期的 ✓？——
而算出来的提醒 ✓ **你处理完它自己就没了** ✓，永远和事实一致 ✓。

## 边界（用户 2026-10-05 明确说的 ✓）

- **只做站内提醒** ✓ —— "关闭浏览器之后的通知"是**另一个交付能力** ✗，不在这轮里 ✓
- **不催命** ✓ —— 每条都**带一个动作** ✓（"去做" / "改个时间"），
  而且**最多 5 条** ✓：一次堆十条提醒等于没提醒 ✗
"""

from datetime import timedelta

from app.core import config
from app.core.time_utils import (
    local_day_range_utc,
    local_today,
    parse_utc,
    to_utc_iso,
    utc_now,
)
from app.database.connection import get_connection

#: 一步"开始"超过这么久还没动 → 提醒一下 ✓
#: （**不是你懒** ✓ —— 是它卡住了 ✓，所以措辞不能是指责 ✓）
_STALE_STEP_HOURS = 4

#: 最多几条 ✓（多了等于没提醒 ✗）
_MAX = 5

_URGENCY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _days_late(due_at: str, now) -> int:
    delta = now - parse_utc(due_at)
    return max(0, int(delta.total_seconds() // 86400))


def list_reminders() -> list[dict]:
    """现在该提醒他的事 ✓ —— 按紧急度排 ✓，最多 5 条 ✓。

    ⚠️ 三类来源 ✓，全部是**查出来的** ✓：
    1. **过期**的 DDL ✓（`high`）—— 它已经不是"待办"了，是"堵着" ✓
    2. **今天到期**的 DDL ✓（`medium`）
    3. **跑太久没动**的一步 ✓（`low`）—— 措辞是"要么继续要么暂停" ✓，
       不是"你怎么还没做完" ✗
    """
    now = utc_now()
    timezone_name = config.APP_TIMEZONE
    today = local_today(timezone_name)
    day_start, day_end = local_day_range_utc(today, timezone_name)
    now_iso = to_utc_iso(now)
    stale_cutoff = to_utc_iso(now - timedelta(hours=_STALE_STEP_HOURS))

    reminders: list[dict] = []

    with get_connection() as conn:
        for row in conn.execute(
            """
            SELECT id, title, due_at FROM deadlines
            WHERE completion_status = 'pending' AND due_at < ?
            ORDER BY due_at ASC LIMIT ?
            """,
            (now_iso, _MAX),
        ).fetchall():
            reminders.append(
                {
                    "kind": "deadline_overdue",
                    "urgency": "high",
                    "title": f"「{row['title']}」已经过期 {_days_late(row['due_at'], now)} 天",
                    "action": "改个时间，或者取消它",
                    "ref_id": row["id"],
                }
            )

        for row in conn.execute(
            """
            SELECT id, title, due_at FROM deadlines
            WHERE completion_status = 'pending' AND due_at >= ? AND due_at < ?
            ORDER BY due_at ASC LIMIT ?
            """,
            (day_start, day_end, _MAX),
        ).fetchall():
            reminders.append(
                {
                    "kind": "deadline_today",
                    "urgency": "medium",
                    "title": f"「{row['title']}」今天到期",
                    "action": "现在排一步",
                    "ref_id": row["id"],
                }
            )

        for row in conn.execute(
            """
            SELECT id, plan_id, title FROM action_plan_steps
            WHERE status = 'active' AND started_at IS NOT NULL AND started_at < ?
            ORDER BY started_at ASC LIMIT ?
            """,
            (stale_cutoff, _MAX),
        ).fetchall():
            reminders.append(
                {
                    "kind": "step_stalled",
                    "urgency": "low",
                    "title": f"「{row['title']}」已经开始很久了",
                    "action": "要么继续，要么先暂停",
                    "ref_id": row["plan_id"],
                }
            )

    reminders.sort(key=lambda item: _URGENCY_ORDER.get(item["urgency"], 9))
    return reminders[:_MAX]
