"""⑤ 番茄钟 ↔ 行动步骤 ✓ —— 这个环必须闭合 ✓。

## 为什么这组测试重要

用户 2026-10-05 说得很直接：
**"一定要绑定到具体步骤，不能只记录一个孤立的番茄钟，
否则后面的计划偏差分析没有意义。"**

所以这里锁三件事：

1. 番茄钟能**绑到步骤** ✓（`pomodoro_sessions.action_step_id` ✓）
2. 结束番茄 → **实际耗时写回步骤** ✓
3. 写回失败**不许让"结束番茄"失败** ✗（计时数据优先 ✓）
"""

from datetime import timedelta

import pytest

from app.core.exceptions import NotFoundError
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.schemas.pomodoro import PomodoroStart
from app.services import action_plan_service, action_step_service, pomodoro_service


def _plan(client) -> dict:
    return action_plan_service.create_plan(
        ActionPlanCreate(
            title="写报告",
            steps=[
                ActionStep(title="整理实验数据", minutes=25, priority="high"),
                ActionStep(title="写主体", minutes=50, priority="high"),
            ],
        )
    )


def _backdated_session(step_id: int, minutes: int) -> int:
    """直接塞一条**半小时前开始**的 running 番茄 ✓。

    ⚠️ 为什么不用 `start_pomodoro()` ✓：`finish` 算的是"真实经过的时间" ✓，
    刚开就结束 → 0 分钟 ✗，测不出写回 ✓。
    """
    started = to_utc_iso(utc_now() - timedelta(minutes=minutes))
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pomodoro_sessions (
                started_at, ended_at, planned_minutes, actual_minutes,
                status, schedule_event_id, deadline_id, action_step_id,
                custom_label, note, created_at, updated_at
            ) VALUES (?, NULL, ?, NULL, 'running', NULL, NULL, ?, NULL, NULL, ?, ?)
            """,
            (started, minutes, step_id, started, started),
        )
        return cursor.lastrowid


def test_a_pomodoro_can_be_bound_to_a_step(client):
    plan = _plan(client)
    step = plan["executable_steps"][0]

    session = pomodoro_service.start_pomodoro(
        PomodoroStart(planned_minutes=25, action_step_id=step["id"])
    )

    assert session["action_step_id"] == step["id"]
    assert session["target_label"] == "整理实验数据"  # 标签也用步骤名 ✓


def test_binding_a_missing_step_is_rejected(client):
    with pytest.raises(NotFoundError):
        pomodoro_service.start_pomodoro(
            PomodoroStart(planned_minutes=25, action_step_id=9999)
        )


def test_finishing_a_pomodoro_writes_the_time_back(client):
    """★★ **这条是 ⑤ 的全部意义** ✓ —— 实际耗时回到步骤上 ✓。"""
    plan = _plan(client)
    step = plan["executable_steps"][0]
    session_id = _backdated_session(step["id"], minutes=20)

    pomodoro_service.finish_pomodoro(session_id)

    refreshed = action_step_service.list_steps(plan["id"])
    assert refreshed[0]["actual_minutes"] >= 19  # 20 分钟左右（ceil 可能多 1）
    assert refreshed[0]["pomodoro_count"] == 1


def test_two_pomodoros_accumulate(client):
    """★ 一步可以跑好几个番茄 ✓ —— 实际耗时是它们的**和** ✓。"""
    plan = _plan(client)
    step = plan["executable_steps"][0]

    pomodoro_service.finish_pomodoro(_backdated_session(step["id"], minutes=10))
    pomodoro_service.finish_pomodoro(_backdated_session(step["id"], minutes=15))

    refreshed = action_step_service.list_steps(plan["id"])
    assert refreshed[0]["pomodoro_count"] == 2
    assert refreshed[0]["actual_minutes"] >= 24


def test_a_broken_step_link_does_not_break_finishing(client):
    """★ 步骤被删了 ✗ → 番茄**照样能结束** ✓（计时数据优先 ✓）。"""
    plan = _plan(client)
    step = plan["executable_steps"][0]
    session_id = _backdated_session(step["id"], minutes=5)

    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM action_plan_steps WHERE id = ?", (step["id"],))

    finished = pomodoro_service.finish_pomodoro(session_id)

    assert finished["status"] == "completed"


def test_a_pomodoro_needs_exactly_one_target(client):
    """★ 一个番茄只能绑一个目标 ✓ —— 步骤 + 标签同时给 → 直接拒 ✗。"""
    plan = _plan(client)
    step = plan["executable_steps"][0]

    with pytest.raises(ValueError):
        PomodoroStart(
            planned_minutes=25,
            action_step_id=step["id"],
            custom_label="顺便写点别的",
        )
