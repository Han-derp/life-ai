from fastapi import APIRouter, Query

from app.schemas.timeline import TimelineCreate, TimelineResponse
from app.services.timeline_service import create_timeline_event, list_timeline_for_date

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("/today", response_model=list[TimelineResponse])
def get_today_timeline(local_date: str | None = Query(default=None)):
    return list_timeline_for_date(local_date)


@router.post("", response_model=TimelineResponse, status_code=201)
def post_timeline(data: TimelineCreate):
    return create_timeline_event(data)
