from pydantic import BaseModel, Field


class TomorrowFocus(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    deadline_id: int | None = None


class DailyReviewAIResult(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    affirmation: str = Field(min_length=1, max_length=600)
    main_issue: str = Field(min_length=1, max_length=600)
    tomorrow_focus: list[TomorrowFocus] = Field(default_factory=list, max_length=3)


class DailyReviewConfirm(BaseModel):
    tomorrow_focus: list[TomorrowFocus] = Field(default_factory=list, max_length=3)


class DailyReviewResponse(BaseModel):
    id: int
    local_date: str
    stats: dict
    ai_summary: str | None
    affirmation: str | None
    main_issue: str | None
    tomorrow_focus: list[TomorrowFocus]
    status: str
    confirmed_at: str | None
    ai_available: bool = True
