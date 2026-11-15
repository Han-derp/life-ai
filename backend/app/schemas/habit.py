from pydantic import BaseModel, Field


class HabitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tracking_type: str
    aggregation_mode: str | None = None
    unit: str | None = Field(default=None, max_length=30)
    target_value: float | None = None
    target_text: str | None = None
    target_operator: str | None = None
    time_anchor: str | None = None


class HabitLogRequest(BaseModel):
    value_bool: bool | None = None
    value_number: float | None = None
    value_text: str | None = None
    note: str | None = None


class HabitTodayResponse(BaseModel):
    id: int
    name: str
    tracking_type: str
    aggregation_mode: str | None
    unit: str | None
    target_value: float | None
    target_text: str | None
    target_operator: str | None
    time_anchor_minute: int | None
    current_bool: bool | None
    current_number: float | None
    current_text: str | None
    met: bool | None
    progress: float | None
    # ⚠️ 「养成中 / 已结束」那个徽标靠它 ✓。
    # **必须在这里声明** ✗ —— 只往 service 的 dict 里塞是不够的 ✓：
    # FastAPI 会**按 response_model 把多余字段悄悄过滤掉** ✗，
    # 于是服务层有、接口上却没有（2026-10-05 真踩过一次 ✓，由新测试当场抓住 ✓）。
    active: bool
