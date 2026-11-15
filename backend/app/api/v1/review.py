from fastapi import APIRouter, Query

from app.schemas.review import DailyReviewConfirm, DailyReviewResponse
from app.services.review_service import confirm_review, generate_review, get_review

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/today", response_model=DailyReviewResponse | None)
def get_today_review(local_date: str | None = Query(default=None)):
    return get_review(local_date)


@router.post("/today/generate", response_model=DailyReviewResponse)
def post_generate_review(local_date: str | None = Query(default=None)):
    return generate_review(local_date)


@router.post("/today/confirm", response_model=DailyReviewResponse)
def post_confirm_review(data: DailyReviewConfirm, local_date: str | None = Query(default=None)):
    return confirm_review(data, local_date)
