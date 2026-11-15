"""印象归纳的 API contract。"""

from pydantic import BaseModel, Field


class ImpressionGenerateRequest(BaseModel):
    """手动归纳。

    - ``observation_ids`` 非空 → 用**这几条**归纳（你自己挑的"同类"）
    - 留空 → 走自动路径：同标签下 ≥3 条**还没被用过**的观察
    """

    observation_ids: list[int] = Field(default_factory=list, max_length=50)
    min_count: int = Field(default=3, ge=2, le=20)


class ImpressionCandidateResponse(BaseModel):
    tag_id: int
    tag_name: str
    observation_ids: list[int]


class ImpressionRunResponse(BaseModel):
    generated: int
    #: 自动路径下"为什么没做"要能看见 —— 否则用户只会觉得坏了
    candidates: list[ImpressionCandidateResponse] = Field(default_factory=list)
    skipped_reason: str | None = None
    #: 新建 / 补同方向证据 / 记反例 各多少（设计裁决 R6 的三条路）
    detail: str | None = None
