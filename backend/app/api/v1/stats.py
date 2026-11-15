from fastapi import APIRouter, Query

from app.schemas.stats import WeeklyStatsResponse
from app.services.stats_service import get_weekly_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/weekly", response_model=WeeklyStatsResponse)
def get_weekly_stats_route(local_date: str | None = Query(default=None)):
    return get_weekly_stats(local_date)
