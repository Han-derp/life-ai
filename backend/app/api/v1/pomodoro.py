from fastapi import APIRouter

from app.schemas.pomodoro import PomodoroResponse, PomodoroStart
from app.services.pomodoro_service import (
    cancel_pomodoro,
    finish_pomodoro,
    get_current_pomodoro,
    start_pomodoro,
)

router = APIRouter(prefix="/pomodoro", tags=["pomodoro"])


@router.get("/current", response_model=PomodoroResponse | None)
def get_current():
    return get_current_pomodoro()


@router.post("/start", response_model=PomodoroResponse, status_code=201)
def post_start(data: PomodoroStart):
    return start_pomodoro(data)


@router.post("/{session_id}/finish", response_model=PomodoroResponse)
def post_finish(session_id: int):
    return finish_pomodoro(session_id)


@router.post("/{session_id}/cancel", response_model=PomodoroResponse)
def post_cancel(session_id: int):
    return cancel_pomodoro(session_id)


@router.post("/{session_id}/pause", response_model=PomodoroResponse)
def post_pause(session_id: int):
    from app.services.pomodoro_service import pause_pomodoro
    return pause_pomodoro(session_id)


@router.post("/{session_id}/resume", response_model=PomodoroResponse)
def post_resume(session_id: int):
    from app.services.pomodoro_service import resume_pomodoro
    return resume_pomodoro(session_id)
