from fastapi import APIRouter

from app.services import reminder_service

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("")
def get_reminders():
    """现在该提醒你的事 ✓ —— **算出来的** ✓（不存表、不跑定时器 ✓）。"""
    return reminder_service.list_reminders()
