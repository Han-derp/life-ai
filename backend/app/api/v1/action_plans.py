from fastapi import APIRouter, Query

from app.schemas.action_plan import (
    ActionPlanCreate,
    ActionPlanDraftRequest,
    ActionPlanDraftResponse,
    ActionPlanResponse,
    ActionPlanStepOut,
    StepAdjustRequest,
    StepMaterializeRequest,
    StepSkipRequest,
)
from app.services import action_plan_service, action_step_service
from app.services.action_plan_ai_service import generate_draft

router = APIRouter(prefix="/action-plans", tags=["action-plans"])


# ---------- 计划 ----------


@router.get("", response_model=list[ActionPlanResponse])
def get_action_plans(status: str | None = Query(default=None)):
    """列计划 ✓（`?status=draft` 只看草稿 ✓）。"""
    return action_plan_service.list_plans(status)


@router.post("", response_model=ActionPlanResponse, status_code=201)
def post_action_plan(data: ActionPlanCreate):
    """存一份计划 ✓ —— 状态从 `draft` 开始 ✓，**同时物化出可执行步骤** ✓。"""
    return action_plan_service.create_plan(data)


@router.post("/draft", response_model=ActionPlanDraftResponse)
def post_action_plan_draft(data: ActionPlanDraftRequest):
    """让 AI 出一份**草稿** ✓ —— **不入库** ✗。

    ⚠️ 返回里的 `source` 会告诉界面：这份是 AI 生成的 ✓，还是保底模板 ✗。
    """
    return generate_draft(data.message)


@router.get("/{plan_id}", response_model=ActionPlanResponse)
def get_action_plan(plan_id: int):
    """一份计划的**完整**状态 ✓ —— 含 `executable_steps` ✓。"""
    return action_plan_service.get_plan(plan_id)


@router.post("/{plan_id}/confirm", response_model=ActionPlanResponse)
def post_confirm_action_plan(plan_id: int):
    """确认一份计划 ✓ —— 只能确认 `draft` / `adjusted` ✓。"""
    return action_plan_service.confirm_plan(plan_id)


# ---------- 步骤（③ 落地 / ⑤ 执行）----------


@router.post("/{plan_id}/steps/{step_id}/start", response_model=ActionPlanStepOut)
def post_start_step(plan_id: int, step_id: int):
    """开始一步 ✓ —— `pending` → `active` ✓。"""
    return action_step_service.start_step(plan_id, step_id)


@router.post("/{plan_id}/steps/{step_id}/pause", response_model=ActionPlanStepOut)
def post_pause_step(plan_id: int, step_id: int):
    """暂停 ✓ —— 回 `pending` ✓ + **中断次数 +1** ✓。"""
    return action_step_service.pause_step(plan_id, step_id)


@router.post("/{plan_id}/steps/{step_id}/complete", response_model=ActionPlanStepOut)
def post_complete_step(plan_id: int, step_id: int):
    """完成 ✓ —— 幂等 ✓，并**顺手完成它落地的 DDL / 日程** ✓。"""
    return action_step_service.complete_step(plan_id, step_id)


@router.post("/{plan_id}/steps/{step_id}/skip", response_model=ActionPlanStepOut)
def post_skip_step(
    plan_id: int, step_id: int, data: StepSkipRequest | None = None
):
    """跳过 ✓ —— 可以带原因 ✓（⑥ 要统计它 ✓）。"""
    return action_step_service.skip_step(
        plan_id, step_id, data.reason if data is not None else None
    )


@router.post("/{plan_id}/steps/{step_id}/adjust", response_model=ActionPlanStepOut)
def post_adjust_step(plan_id: int, step_id: int, data: StepAdjustRequest):
    """调整 ✓ —— 只改传进来的字段 ✓。"""
    return action_step_service.adjust_step(
        plan_id,
        step_id,
        title=data.title,
        minutes=data.minutes,
        priority=data.priority,
        importance=data.importance,
        difficulty=data.difficulty,
    )


@router.post("/{plan_id}/steps/{step_id}/materialize", response_model=ActionPlanStepOut)
def post_materialize_step(
    plan_id: int, step_id: int, data: StepMaterializeRequest | None = None
):
    """把一步**落地**成 DDL / 日程 ✓，或者**什么都不做** ✓（默认）。

    ⚠️ **不会自动全塞进日历** ✗ —— `kind` 必须明确指定 ✓。
    """
    payload = data or StepMaterializeRequest()
    return action_step_service.materialize_step(
        plan_id,
        step_id,
        kind=payload.kind,
        due_at=payload.due_at,
        start_at=payload.start_at,
        end_at=payload.end_at,
        materialize_key=payload.materialize_key,
    )
