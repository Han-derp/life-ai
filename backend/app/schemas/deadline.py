from pydantic import BaseModel, Field


class DeadlineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    due_at_local: str
    parent_id: int | None = None
    priority: int = Field(default=0, ge=0, le=5)


class DeadlineUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    due_at_local: str | None = None
    priority: int | None = Field(default=None, ge=0, le=5)


class DeadlineResponse(BaseModel):
    id: int
    title: str
    description: str | None
    due_at: str
    due_at_local: str
    parent_id: int | None
    completion_status: str
    time_status: str
    priority: int


class DeadlineBreakdownSubtask(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due_at_local: str | None = None
    priority: int = Field(default=0, ge=0, le=5)


class DeadlineBreakdownAIResult(BaseModel):
    subtasks: list[DeadlineBreakdownSubtask] = Field(default_factory=list, max_length=5)


class DeadlineBreakdownResponse(BaseModel):
    deadline_id: int
    subtasks: list[DeadlineBreakdownSubtask]
    ai_available: bool


class DeadlineBreakdownApply(BaseModel):
    subtasks: list[DeadlineBreakdownSubtask] = Field(min_length=1, max_length=5)
