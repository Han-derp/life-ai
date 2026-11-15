from fastapi import APIRouter

from app.api.v1 import (
    action_plans,
    chat,
    commitments,
    context,
    deadlines,
    export,
    extraction,
    habits,
    impression,
    memory,
    persona,
    pomodoro,
    proactive,
    reminders,
    review,
    rules,
    schedule,
    settings,
    stats,
    tag_suggestion,
    timeline,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(schedule.router)
api_router.include_router(deadlines.router)
api_router.include_router(habits.router)
api_router.include_router(pomodoro.router)
api_router.include_router(timeline.router)
api_router.include_router(context.router)
api_router.include_router(stats.router)
api_router.include_router(persona.router)
api_router.include_router(memory.router)
api_router.include_router(extraction.router)
api_router.include_router(impression.router)
api_router.include_router(tag_suggestion.router)
api_router.include_router(rules.router)
api_router.include_router(commitments.router)
api_router.include_router(proactive.router)
api_router.include_router(settings.router)
api_router.include_router(export.router)

api_router.include_router(chat.router)
api_router.include_router(action_plans.router)
api_router.include_router(reminders.router)
api_router.include_router(review.router)
