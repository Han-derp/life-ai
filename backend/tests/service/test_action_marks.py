"""「重要」和「困难」是**两个独立维度** ✓（2026-10-05 第三轮 ✓）。

## 为什么必须分开（用户原话）

> **重要是用户的价值判断，困难是执行负担，不能混成一个优先级。**

所以这里锁三件事：

1. 两个字段**互相独立** ✓ —— 改一个不影响另一个 ✓
2. **`0` 和 `None` 不一样** ✓ —— "明确不重要" vs "还没标记" ✓
3. 已有的 `priority`（模型给的粗排）**不参与**这两个 ✓
"""

import pytest
from pydantic import ValidationError

from app.schemas.action_plan import ActionPlanCreate, ActionStep, StepAdjustRequest
from app.services import action_plan_service, action_step_service


def _plan_with_one_step(client) -> tuple[int, int]:
    plan = action_plan_service.create_plan(
        ActionPlanCreate(
            title="把报告开个头",
            steps=[ActionStep(title="整理数据", minutes=20, priority="high")],
        )
    )
    return plan["id"], plan["executable_steps"][0]["id"]


def test_both_marks_are_unset_by_default(client):
    plan_id, step_id = _plan_with_one_step(client)

    step = action_step_service.list_steps(plan_id)[0]

    assert step["importance"] is None
    assert step["difficulty"] is None


def test_importance_and_difficulty_are_independent(client):
    """★★ **这条最重要** ✗ —— "很重要但很容易"必须能表达出来 ✓。"""
    plan_id, step_id = _plan_with_one_step(client)

    action_step_service.adjust_step(plan_id, step_id, importance=3)
    only_important = action_step_service.list_steps(plan_id)[0]
    assert only_important["importance"] == 3
    assert only_important["difficulty"] is None  # 没动它 ✓

    action_step_service.adjust_step(plan_id, step_id, difficulty=1)
    both = action_step_service.list_steps(plan_id)[0]
    assert both["importance"] == 3  # 也没动它 ✓
    assert both["difficulty"] == 1


def test_zero_is_a_real_value_not_missing(client):
    """★ `0` = "明确标记为不重要/不难" ✓，和 `None`（还没标记）**不是一回事** ✗。"""
    plan_id, step_id = _plan_with_one_step(client)

    action_step_service.adjust_step(plan_id, step_id, importance=0, difficulty=0)

    step = action_step_service.list_steps(plan_id)[0]
    assert step["importance"] == 0
    assert step["difficulty"] == 0


def test_the_marks_do_not_touch_the_model_priority(client):
    """★ `priority` 是**模型给的粗排** ✓ —— 用户标重要/困难**不该改它** ✗。"""
    plan_id, step_id = _plan_with_one_step(client)

    action_step_service.adjust_step(plan_id, step_id, importance=2, difficulty=3)

    assert action_step_service.list_steps(plan_id)[0]["priority"] == "high"


def test_the_range_is_enforced(client):
    """★ 只有 0–3 四档 ✓（多一档就是在暗示"合并维度" ✗）。"""
    with pytest.raises(ValidationError):
        StepAdjustRequest(importance=4)
    with pytest.raises(ValidationError):
        StepAdjustRequest(difficulty=-1)


def test_the_http_layer_carries_both(client):
    plan_id, step_id = _plan_with_one_step(client)

    body = client.post(
        f"/api/v1/action-plans/{plan_id}/steps/{step_id}/adjust",
        json={"importance": 3, "difficulty": 0},
    ).json()

    assert body["importance"] == 3
    assert body["difficulty"] == 0
