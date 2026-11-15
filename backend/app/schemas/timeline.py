from pydantic import BaseModel, Field


class TimelineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_at_local: str
    end_at_local: str | None = None
    note: str | None = None


class TimelineResponse(BaseModel):
    id: int
    title: str
    start_at: str
    end_at: str | None
    start_at_local: str
    end_at_local: str | None
    source_type: str
    source_id: int | None
    note: str | None
