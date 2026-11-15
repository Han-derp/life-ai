from pydantic import BaseModel, Field, model_validator


class PomodoroStart(BaseModel):
    planned_minutes: int = Field(default=25, ge=1, le=180)
    schedule_event_id: int | None = None
    deadline_id: int | None = None
    #: 绑定到**行动步骤** ✓（2026-10-05 加，⑤）——
    #: ⚠️ **不再新写一套计时逻辑** ✗：番茄钟原本就有 ✓，这里只是多一个目标 ✓。
    action_step_id: int | None = None
    custom_label: str | None = Field(default=None, max_length=200)
    note: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        targets = [
            self.schedule_event_id is not None,
            self.deadline_id is not None,
            self.action_step_id is not None,
            bool(self.custom_label and self.custom_label.strip()),
        ]
        if sum(targets) != 1:
            raise ValueError("番茄必须且只能绑定一个日程、DDL、行动步骤或临时标签。")
        return self


class PomodoroResponse(BaseModel):
    paused: bool = False
    focus_seconds: int = 0
    elapsed_seconds: int = 0
    id: int
    started_at: str
    ended_at: str | None
    planned_minutes: int
    actual_minutes: int | None
    status: str
    schedule_event_id: int | None
    deadline_id: int | None
    action_step_id: int | None = None
    custom_label: str | None
    target_label: str
    note: str | None
