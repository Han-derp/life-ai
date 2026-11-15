from fastapi import APIRouter, Query

from app.schemas.deadline import (
    DeadlineBreakdownApply,
    DeadlineBreakdownResponse,
    DeadlineCreate,
    DeadlineResponse,
    DeadlineUpdate,
)
from app.services.deadline_service import (
    apply_deadline_breakdown,
    cancel_deadline,
    complete_deadline,
    create_deadline,
    get_deadline,
    list_deadlines,
    remove_deadline,
    suggest_deadline_breakdown,
    update_deadline,
)

router = APIRouter(prefix="/deadlines", tags=["deadlines"])


@router.get("", response_model=list[DeadlineResponse])
def get_deadlines(status: str | None = Query(default=None)):
    return list_deadlines(status)


@router.get("/{deadline_id}", response_model=DeadlineResponse)
def get_deadline_route(deadline_id: int):
    return get_deadline(deadline_id)


@router.post("", response_model=DeadlineResponse, status_code=201)
def post_deadline(data: DeadlineCreate):
    return create_deadline(data)


@router.patch("/{deadline_id}", response_model=DeadlineResponse)
def patch_deadline(deadline_id: int, data: DeadlineUpdate):
    return update_deadline(deadline_id, data)


@router.post("/{deadline_id}/complete", response_model=DeadlineResponse)
def post_complete_deadline(deadline_id: int):
    return complete_deadline(deadline_id)


@router.delete("/{deadline_id}")
def delete_deadline(deadline_id: int):
    """**真的从库里删掉**它（连同它的子任务）。

    ⚠️ 和 `/cancel` 是两件事（用户 2026-10-05 明确要求分开 ✓）：

    - **取消** = 改 `completion_status` ✓，行留着 ✓（"我决定不做了，但这事发生过" ✓）
    - **删除** = 行消失 ✓（"当我没记过这件事" ✓）

    返回 `{"deleted_children": n}` ✓ —— 界面用它告诉用户"顺带删了几个子任务" ✓。
    """
    return remove_deadline(deadline_id)


@router.post("/{deadline_id}/cancel", response_model=DeadlineResponse)
def post_cancel_deadline(deadline_id: int):
    return cancel_deadline(deadline_id)


@router.post("/{deadline_id}/breakdown/suggest", response_model=DeadlineBreakdownResponse)
def post_suggest_breakdown(deadline_id: int):
    return suggest_deadline_breakdown(deadline_id)


@router.post("/{deadline_id}/breakdown/apply", response_model=list[DeadlineResponse], status_code=201)
def post_apply_breakdown(deadline_id: int, data: DeadlineBreakdownApply):
    return apply_deadline_breakdown(deadline_id, data)

