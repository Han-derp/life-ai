from pydantic import BaseModel


class WeeklyScheduleStats(BaseModel):
    completed: int
    pending: int
    cancelled: int
    total: int


class WeeklyPomodoroStats(BaseModel):
    count: int
    minutes: int


class WeeklyHabitStats(BaseModel):
    habit_id: int
    name: str
    tracking_type: str
    days_logged: int
    days_met: int


class WeeklyDeadlineStats(BaseModel):
    completed: int
    overdue_pending: int
    created: int


class WeeklyStatsResponse(BaseModel):
    execution: dict
    week_start: str
    week_end: str
    timezone: str
    schedule: WeeklyScheduleStats
    pomodoro: WeeklyPomodoroStats
    habits: list[WeeklyHabitStats]
    deadlines: WeeklyDeadlineStats
