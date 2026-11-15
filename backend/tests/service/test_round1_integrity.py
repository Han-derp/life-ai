"""第一轮「完整性补丁」的回归测试 ✓（2026-10-05 整合时补的 ✓）。

这个文件来自外部补丁包的 `tests/service/test_round1_integrity.py` ✓，
整合时我**只加了一条**和一句说明 ✓（下面会标出来 ✓）。

## 它锁的是什么

1. **模型的确认信息不许丢** ✗ —— `assumptions` / `needs_confirmation`
   从模型出来 → 入库 → 读回来，必须一模一样 ✓
2. **计划状态跟着步骤走** ✓ —— 步骤在跑 → 计划 `active`；
   步骤全做完 → 计划 `completed` ✓
"""

from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.services import action_plan_service, action_step_service


def test_plan_persists_confirmation_metadata(client):
    """★ 模型给的"我假设了什么"要能读回来 ✓（`MIGRATION_016` ✓）。"""
    plan = action_plan_service.create_plan(
        ActionPlanCreate(
            title="考试复习",
            steps=[ActionStep(title="整理范围", minutes=15)],
            assumptions=["今晚有 30 分钟"],
            needs_confirmation=["是否把第二步安排到明天"],
        )
    )

    refreshed = action_plan_service.get_plan(plan["id"])

    assert refreshed["assumptions"] == ["今晚有 30 分钟"]
    assert refreshed["needs_confirmation"] == ["是否把第二步安排到明天"]


def test_plan_state_follows_step_state(client):
    """★ 步骤在跑 → 计划 `active` ✓；全部完成 → `completed` ✓。"""
    plan = action_plan_service.create_plan(
        ActionPlanCreate(title="状态测试", steps=[ActionStep(title="一步", minutes=10)])
    )
    step = plan["executable_steps"][0]

    action_plan_service.confirm_plan(plan["id"])
    action_step_service.start_step(plan["id"], step["id"])
    assert action_plan_service.get_plan(plan["id"])["status"] == "active"

    action_step_service.complete_step(plan["id"], step["id"])
    assert action_plan_service.get_plan(plan["id"])["status"] == "completed"


# ↓ 整合时**新加**的一条 ✓（补丁包里没有 ✓）——
# 为的是把"全部结束但**有跳过的** → adjusted"这个分支也锁住 ✓。
def test_a_skipped_step_makes_the_plan_adjusted(client):
    """★ 有一步被跳过 → 计划是 `adjusted`（"做过、但调整了"）✓，不是 `completed` ✗。"""
    plan = action_plan_service.create_plan(
        ActionPlanCreate(
            title="两个步骤",
            steps=[
                ActionStep(title="一步", minutes=10),
                ActionStep(title="二步", minutes=10),
            ],
        )
    )
    first, second = plan["executable_steps"]

    action_plan_service.confirm_plan(plan["id"])
    action_step_service.complete_step(plan["id"], first["id"])
    action_step_service.skip_step(plan["id"], second["id"], "今天没精力")

    assert action_plan_service.get_plan(plan["id"])["status"] == "adjusted"
