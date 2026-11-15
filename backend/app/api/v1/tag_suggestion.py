from fastapi import APIRouter

from app.schemas.tag_suggestion import (
    TagSuggestionDecisionResponse,
    TagSuggestionResponse,
)
from app.services import tag_suggestion_service

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/tag-suggestions", response_model=list[TagSuggestionResponse])
def get_tag_suggestions():
    """她**建议**的新标签，按被建议次数降序。

    **她不能自己创建标签**（MEMORY_DESIGN §6.4）—— 只能建议，
    你批准了才进 `tags`。次数高 = 她反复注意到同一件事，那本身值得看一眼。
    """
    return tag_suggestion_service.list_pending()


@router.post(
    "/tag-suggestions/{suggestion_id}/approve",
    response_model=TagSuggestionDecisionResponse,
)
def post_tag_suggestion_approve(suggestion_id: int):
    """批准：写入 `tags`，并**清理这条建议**（避免"已批准但还挂在待办里"）。"""
    return tag_suggestion_service.approve(suggestion_id)


@router.post(
    "/tag-suggestions/{suggestion_id}/reject",
    response_model=TagSuggestionDecisionResponse,
)
def post_tag_suggestion_reject(suggestion_id: int):
    """拒绝：这个名字**以后永远不会再被建议**。

    这是裁决里的硬约束 —— 否则她会反复提你拒绝过的东西，
    那是关系里最烦人的行为之一。
    """
    return tag_suggestion_service.reject(suggestion_id)
