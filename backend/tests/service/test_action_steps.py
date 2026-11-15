"""「行动步骤」—— ③ 落地 + ⑤ 执行 ✓。

## 这组测试锁的是什么

- **不默认全塞进日历** ✗（用户 2026-10-05 明确要求 ✓）
- **状态机** ✓：`pending → active → completed / skipped` ✓
- **暂停算一次中断** ✓（⑥⑦ 的原料 ✓）
- **完成是幂等的** ✓（双击不该炸 ✗）
- **跨计划不能操作** ✓（步骤 A 不能借计划 B 的手改 ✓）
"""

import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.services import action_plan_service, action_step_service


def _plan(**overrides) -> ActionPlanCreate:
    data = {
        "title": "把报告开个头",
        "minimum_plan": "只整理数据",
        "steps": [
            ActionStep(title="整理实验数据", minutes=25, priority="high"),
            ActionStep(title="写三个小标题", minutes=10, priority="medium"),
        ],
    }
    data.update(overrides)
    return ActionPlanCreate(**data)


def _created(client, **overrides) -> dict:
    return action_plan_service.create_plan(_plan(**overrides))


def test_creating_a_plan_materialises_its_steps(client):
    """★ 存计划 = **同时**生成可执行步骤 ✓（不用再调一个接口 ✓）。"""
    plan = _created(client)

    assert [s["title"] for s in plan["executable_steps"]] == [
        "整理实验数据",
        "写三个小标题",
    ]
    assert all(s["status"] == "pending" for s in plan["executable_steps"])
    assert plan["executable_steps"][0]["estimated_minutes"] == 25


def test_start_then_pause_counts_an_interruption(client):
    """★ 暂停 = 回 `pending` ✓ + **中断次数 +1** ✓。"""
    plan = _created(client)
    step = plan["executable_steps"][0]

    started = action_step_service.start_step(plan["id"], step["id"])
    assert started["status"] == "active"
    assert started["started_at"]

    paused = action_step_service.pause_step(plan["id"], step["id"])
    assert paused["status"] == "pending"
    assert paused["interruption_count"] == 1


def test_pausing_something_not_running_is_rejected(client):
    plan = _created(client)

    with pytest.raises(DomainValidationError):
        action_step_service.pause_step(plan["id"], plan["executable_steps"][0]["id"])


def test_complete_is_idempotent(client):
    """★ 双击 / 重试都不该炸 ✗。"""
    plan = _created(client)
    step = plan["executable_steps"][0]

    first = action_step_service.complete_step(plan["id"], step["id"])
    second = action_step_service.complete_step(plan["id"], step["id"])

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["completed_at"] == first["completed_at"]


def test_a_completed_step_cannot_be_started_or_skipped(client):
    plan = _created(client)
    step = plan["executable_steps"][0]
    action_step_service.complete_step(plan["id"], step["id"])

    with pytest.raises(DomainValidationError):
        action_step_service.start_step(plan["id"], step["id"])
    with pytest.raises(DomainValidationError):
        action_step_service.skip_step(plan["id"], step["id"])


def test_skip_keeps_the_reason(client):
    plan = _created(client)
    step = plan["executable_steps"][1]

    skipped = action_step_service.skip_step(plan["id"], step["id"], "今天没精力")

    assert skipped["status"] == "skipped"
    assert skipped["skip_reason"] == "今天没精力"


def test_adjust_only_touches_what_you_pass(client):
    plan = _created(client)
    step = plan["executable_steps"][0]

    adjusted = action_step_service.adjust_step(plan["id"], step["id"], minutes=40)

    assert adjusted["minutes"] == 40
    assert adjusted["estimated_minutes"] == 40
    assert adjusted["title"] == "整理实验数据"  # 没传就不改 ✓
    assert adjusted["priority"] == "high"


def test_a_step_cannot_be_touched_through_another_plan(client):
    """★ 跨计划操作 → 404 ✓（不能借别的计划的手改这一步 ✓）。"""
    plan_a = _created(client)
    plan_b = _created(client, title="另一件事")

    with pytest.raises(NotFoundError):
        action_step_service.start_step(plan_b["id"], plan_a["executable_steps"][0]["id"])


# ---------- ③ 落地：默认什么都不建 ----------


def test_materialize_defaults_to_nothing(client):
    """★★ **这条最重要** ✗ —— 用户明确说"不要默认全塞进日历" ✓。"""
    plan = _created(client)
    step = plan["executable_steps"][0]

    result = action_step_service.materialize_step(plan["id"], step["id"])

    assert result["deadline_id"] is None
    assert result["schedule_id"] is None


def test_materialize_as_a_deadline(client):
    plan = _created(client)
    step = plan["executable_steps"][0]

    result = action_step_service.materialize_step(
        plan["id"], step["id"], kind="deadline", due_at="2026-10-07T23:00"
    )

    assert result["deadline_id"]
    assert result["schedule_id"] is None


def test_materialize_as_a_schedule(client):
    plan = _created(client)
    step = plan["executable_steps"][1]

    result = action_step_service.materialize_step(
        plan["id"],
        step["id"],
        kind="schedule",
        start_at="2026-10-06T20:00",
        end_at="2026-10-06T20:10",
    )

    assert result["schedule_id"]
    assert result["deadline_id"] is None


def test_materializing_twice_is_rejected(client):
    plan = _created(client)
    step = plan["executable_steps"][0]
    action_step_service.materialize_step(
        plan["id"], step["id"], kind="deadline", due_at="2026-10-07T23:00"
    )

    with pytest.raises(DomainValidationError):
        action_step_service.materialize_step(
            plan["id"], step["id"], kind="deadline", due_at="2026-10-08T23:00"
        )


def test_completing_a_step_completes_its_deadline(client):
    """★ 落地之后**同步状态** ✓ —— 步骤完成 → 对应 DDL 也完成 ✓。"""
    from app.services import deadline_service

    plan = _created(client)
    step = plan["executable_steps"][0]
    landed = action_step_service.materialize_step(
        plan["id"], step["id"], kind="deadline", due_at="2026-10-07T23:00"
    )

    action_step_service.complete_step(plan["id"], step["id"])

    assert deadline_service.get_deadline(landed["deadline_id"])["completion_status"] == "completed"


# ---------- ⑤ 番茄钟写回 ----------


def test_the_pomodoro_accumulates_not_overwrites(client):
    """★ 一步可以跑好几个番茄钟 ✓ —— 实际耗时是**和** ✓。"""
    plan = _created(client)
    step = plan["executable_steps"][0]

    action_step_service.record_pomodoro(step["id"], 25)
    after = action_step_service.record_pomodoro(step["id"], 15)

    assert after["actual_minutes"] == 40
    assert after["pomodoro_count"] == 2


# ---------- HTTP 层 ----------


def test_the_step_endpoints_are_wired(client):
    plan = _created(client)
    step = plan["executable_steps"][0]
    base = f"/api/v1/action-plans/{plan['id']}/steps/{step['id']}"

    assert client.post(f"{base}/start").json()["status"] == "active"
    assert client.post(f"{base}/pause").json()["status"] == "pending"
    assert client.post(f"{base}/adjust", json={"minutes": 30}).json()["minutes"] == 30
    assert client.post(f"{base}/skip", json={"reason": "改天"}).json()["status"] == "skipped"


def test_get_one_plan_includes_the_executable_steps(client):
    plan = _created(client)

    body = client.get(f"/api/v1/action-plans/{plan['id']}").json()

    assert len(body["executable_steps"]) == 2
    assert body["executable_steps"][0]["status"] == "pending"
