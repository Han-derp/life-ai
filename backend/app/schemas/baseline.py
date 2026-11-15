"""B 类基线预警的 API contract（DEVELOPMENT_PLAN v2.0 · S8）。"""

from pydantic import BaseModel


class BaselineAlertResponse(BaseModel):
    id: int
    kind: str  # late_night | deadline_stall —— 只有这两种能触发
    detail: dict
    content: str | None  # 说出口时才生成
    detected_at: str
    voiced_at: str | None
    dismissed_at: str | None
    voiced: bool
