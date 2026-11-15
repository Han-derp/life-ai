from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    start_at_local: str
    end_at_local: str | None = None
    note: str | None = None


class ScheduleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    start_at_local: str | None = None
    end_at_local: str | None = None
    note: str | None = None


class ScheduleResponse(BaseModel):
    id: int
    title: str
    description: str | None
    start_at: str
    end_at: str | None
    start_at_local: str
    end_at_local: str | None
    completion_status: str
    time_status: str
    source_type: str
    note: str | None
