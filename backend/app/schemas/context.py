"""上下文面板的 API contract（DEVELOPMENT_PLAN v2.0 · S2）。"""

from typing import Literal

from pydantic import BaseModel, Field


class ContextBlockView(BaseModel):
    """面板里的一块。

    ``content`` 原文一并返回——"看得见这次发了什么"就是这个功能的全部意义，
    面板要能展开看原文，所以不能只给 token 数。
    """

    id: str
    kind: str
    label: str
    content: str
    tokens: int
    source: str = ""
    sensitivity: str = "normal"
    state: str = "include"
    excluded: bool = False  # 用户设为不发送（灰掉但保留显示）
    dropped: bool = False  # 超预算被整块丢弃


class ContextPreviewResponse(BaseModel):
    blocks: list[ContextBlockView]
    total_tokens: int  # 实际会发送的 token 合计（含最近聊天）
    budget: int
    over_budget: bool
    excluded_labels: list[str] = Field(default_factory=list)
    boundary_notice: str | None = None
    # 上一次真实请求的缓存命中（来自 provider 的 usage）；还没调用过则为 null
    cache_hit_tokens: int | None = None


class ContextExcludeRequest(BaseModel):
    block_id: str = Field(min_length=1, max_length=120)
    state: Literal["include", "exclude_once", "exclude_always"]
