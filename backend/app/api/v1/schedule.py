from fastapi import APIRouter, Query

from app.schemas.schedule import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from app.services.schedule_service import (
    cancel_schedule,
    complete_schedule,
    create_schedule,
    list_schedule_for_date,
    list_schedules,
    update_schedule,
)

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("/today", response_model=list[ScheduleResponse])
def get_today_schedule(local_date: str | None = Query(default=None)):
    return list_schedule_for_date(local_date)


@router.get("", response_model=list[ScheduleResponse])
def get_schedules(status: str | None = Query(default=None)):
    """**全部**日程（给「查看全部」那个列表页用）。

    `status` 可省略 ✓（= 全部 ✓），或 `pending` / `completed` / `cancelled` ✓。
    """
    return list_schedules(status)


@router.post("", response_model=ScheduleResponse, status_code=201)
def post_schedule(data: ScheduleCreate):
    return create_schedule(data)


@router.patch("/{event_id}", response_model=ScheduleResponse)
def patch_schedule(event_id: int, data: ScheduleUpdate):
    return update_schedule(event_id, data)


@router.post("/{event_id}/complete", response_model=ScheduleResponse)
def post_complete_schedule(event_id: int):
    return complete_schedule(event_id)


@router.post("/{event_id}/cancel", response_model=ScheduleResponse)
def post_cancel_schedule(event_id: int):
    return cancel_schedule(event_id)
