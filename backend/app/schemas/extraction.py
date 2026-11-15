"""自动记忆抽取的 API contract。"""

from pydantic import BaseModel, Field


class ExtractionResultResponse(BaseModel):
    checked_messages: int
    created_observations: int
    # 因为"核不到出处"被丢掉的条数。>0 说明模型在改写引用。
    dropped_without_source: int
    model: str | None = None
    suggested_tags: int = 0


class ExtractionPendingResponse(BaseModel):
    pending_messages: int


class ExtractionBatchRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
