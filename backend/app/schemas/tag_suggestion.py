"""标签建议的 API contract（设计裁决 R8）。"""

from pydantic import BaseModel


class TagSuggestionResponse(BaseModel):
    id: int
    name: str
    dimension: str
    reason: str | None = None
    source_observation_id: int | None = None
    #: 被建议了几次 —— 按它降序排，"她反复注意到的"自己浮出来
    count: int
    status: str
    created_at: str
    updated_at: str


class TagSuggestionDecisionResponse(BaseModel):
    name: str
    status: str | None = None
    tag: dict | None = None
