"""主动联系的 API contract（DEVELOPMENT_PLAN v2.0 · S8）。"""

from pydantic import BaseModel


class ProactiveContactResponse(BaseModel):
    id: int
    kind: str  # care | silence_3d | silence_7d | essay
    content: str
    slot: str | None  # care 才有：noon / evening
    created_at: str
    delivered_at: str | None
    responded_at: str | None
    delivered: bool


class ProactiveTickResponse(BaseModel):
    """走一次 tick 的结果。``did`` 为 null 表示这次什么都不该做。"""

    did: ProactiveContactResponse | None = None
