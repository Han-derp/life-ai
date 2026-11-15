from app.core.time_utils import parse_utc, utc_now


def deadline_time_status(due_at: str, completion_status: str) -> str:
    if completion_status == "completed":
        return "completed"
    if completion_status == "cancelled":
        return "cancelled"
    return "overdue" if parse_utc(due_at) < utc_now() else "upcoming"


def event_time_status(start_at: str, end_at: str | None, completion_status: str) -> str:
    if completion_status == "completed":
        return "completed"
    if completion_status == "cancelled":
        return "cancelled"
    now = utc_now()
    start = parse_utc(start_at)
    if now < start:
        return "upcoming"
    if end_at is not None and now <= parse_utc(end_at):
        return "ongoing"
    return "overdue"
