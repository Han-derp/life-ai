from fastapi import APIRouter, Query

from app.ai.context import assembler
from app.core.exceptions import DomainValidationError
from app.schemas.context import (
    ContextBlockView,
    ContextExcludeRequest,
    ContextPreviewResponse,
)
from app.services import chat_service, context_exclusion_service
from app.services.context_service import get_today_context

router = APIRouter(prefix="/context", tags=["context"])


@router.get("/today")
def get_context_today(local_date: str | None = Query(default=None)):
    return get_today_context(local_date)


def _block_view(block, *, excluded: bool, dropped: bool) -> ContextBlockView:
    return ContextBlockView(
        id=block.id,
        kind=block.kind,
        label=block.label,
        content=block.content,
        tokens=block.tokens,
        source=block.source,
        sensitivity=block.sensitivity,
        state=block.state,
        excluded=excluded,
        dropped=dropped,
    )


def _cache_hit_tokens() -> int | None:
    """上一次真实请求的缓存命中 token（来自 provider usage）。"""
    usage = chat_service.last_usage()
    if not usage:
        return None
    value = usage.get("prompt_cache_hit_tokens")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _build_preview() -> ContextPreviewResponse:
    plan = chat_service.build_context_plan()

    views: list[ContextBlockView] = []
    excluded_labels: list[str] = []

    for block in plan.system_blocks:
        excluded = block.is_excluded
        if excluded:
            excluded_labels.append(block.label)
        views.append(
            _block_view(
                block,
                excluded=excluded,
                dropped=not excluded and block.id not in plan.kept_system_ids,
            )
        )

    history = plan.history_block
    history_excluded = history.is_excluded
    if history_excluded:
        excluded_labels.append(history.label)
    views.append(
        _block_view(
            history,
            excluded=history_excluded,
            dropped=not history_excluded and plan.history_dropped,
        )
    )

    return ContextPreviewResponse(
        blocks=views,
        total_tokens=plan.total_tokens,
        budget=plan.budget,
        over_budget=plan.over_budget,
        excluded_labels=excluded_labels,
        boundary_notice=assembler.boundary_notice([*plan.system_blocks, history]),
        cache_hit_tokens=_cache_hit_tokens(),
    )


@router.get("/preview", response_model=ContextPreviewResponse)
def get_context_preview():
    """「这次会发送什么」：本次将发送的全部块 + token 占用 + 合计（S2）。"""
    return _build_preview()


@router.post("/exclude", response_model=ContextPreviewResponse)
def post_context_exclude(data: ContextExcludeRequest):
    """设置某块的发送状态（S2）。

    - ``exclude_always``：落库，可随时恢复（"我关过什么"要可查）
    - ``exclude_once``：只对下一次真实请求生效（进程内，不落库）
    - ``include``：恢复发送

    返回更新后的预览，前端不必再单独请求一次。
    """
    valid_ids = {block.id for block in assembler.get_context_blocks()}
    valid_ids.add(assembler.HISTORY_BLOCK_ID)
    if data.block_id not in valid_ids:
        raise DomainValidationError("未知的上下文块。")

    if data.state == "exclude_always":
        context_exclusion_service.set_once(data.block_id, False)
        context_exclusion_service.set_excluded(data.block_id, True)
    elif data.state == "exclude_once":
        context_exclusion_service.set_excluded(data.block_id, False)
        context_exclusion_service.set_once(data.block_id, True)
    else:
        context_exclusion_service.set_excluded(data.block_id, False)
        context_exclusion_service.set_once(data.block_id, False)

    return _build_preview()
