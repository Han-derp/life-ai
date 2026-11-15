from app.services.execution_stats_service import get_execution_stats
from app.core.time_utils import local_day_range_utc
import json
import re

from pydantic import ValidationError

from app.core.config import APP_TIMEZONE
from app.core.exceptions import ExternalServiceError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import local_today, to_utc_iso, utc_now
from app.database.connection import get_connection
from app.llm import factory
from app.schemas.review import DailyReviewAIResult, DailyReviewConfirm
from app.services.context_service import get_today_context


# Daily Review 走 `review`（云端）：需要判断力，且不是高频操作。
_provider = factory.current_provider("review")


def _review_row_to_dict(row, ai_available: bool | None = None) -> dict:
    focus = json.loads(row["tomorrow_focus_json"] or "[]")
    return {
        "id": row["id"],
        "local_date": row["local_date"],
        "stats": json.loads(row["stats_json"]),
        "ai_summary": row["ai_summary"],
        "affirmation": row["affirmation"],
        "main_issue": row["main_issue"],
        "tomorrow_focus": focus,
        "status": row["status"],
        "confirmed_at": row["confirmed_at"],
        "ai_available": _provider.available if ai_available is None else ai_available,
    }


def _stats_from_context(context: dict) -> dict:
    schedule = context["schedule"]
    habits = context["habits"]
    deadlines = context["deadlines"]
    return {
        "schedule": {
            "completed": sum(1 for x in schedule if x["completion_status"] == "completed"),
            "pending": sum(1 for x in schedule if x["completion_status"] == "pending"),
            "cancelled": sum(1 for x in schedule if x["completion_status"] == "cancelled"),
            "total": len(schedule),
        },
        "pomodoro": {
            "count": context["pomodoro"]["count"],
            "minutes": context["pomodoro"]["minutes"],
        },
        "habits": {
            "tracked": len(habits),
            "met": sum(1 for x in habits if x["met"] is True),
        },
        "deadlines": {
            "overdue": sum(1 for x in deadlines if x["time_status"] == "overdue"),
            "upcoming": sum(1 for x in deadlines if x["time_status"] == "upcoming"),
        },
        "timeline_events": len(context["timeline"]),
    }


def _extract_json(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("模型输出中没有 JSON 对象。")
    return cleaned[start : end + 1]


def _validate_focus_ids(result: DailyReviewAIResult, valid_ids: set[int]) -> DailyReviewAIResult:
    repaired = []
    for item in result.tomorrow_focus[:3]:
        deadline_id = item.deadline_id if item.deadline_id in valid_ids else None
        repaired.append({"text": item.text, "deadline_id": deadline_id})
    return DailyReviewAIResult(
        summary=result.summary,
        affirmation=result.affirmation,
        main_issue=result.main_issue,
        tomorrow_focus=repaired,
    )


def _ask_review_ai(context: dict, stats: dict) -> tuple[DailyReviewAIResult, str]:
    prompt = load_prompt("daily_review.md")
    payload = {
        "stats": stats,
        "today": context,
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = _provider.chat(messages, temperature=0.6)

    for attempt in range(2):
        try:
            parsed = json.loads(_extract_json(raw))
            result = DailyReviewAIResult.model_validate(parsed)
            valid_ids = {item["id"] for item in context["deadlines"]}
            return _validate_focus_ids(result, valid_ids), raw
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if attempt == 1:
                raise
            raw = _provider.chat(
                [
                    {
                        "role": "system",
                        "content": "只修复下面内容为符合给定结构的严格JSON；不要添加新事实。",
                    },
                    {
                        "role": "user",
                        "content": f"校验错误：{exc}\n原输出：\n{raw}\n\n目标结构：\n{prompt}",
                    },
                ],
                temperature=0.2,
            )
    raise RuntimeError("unreachable")


def _fallback_result(context: dict) -> DailyReviewAIResult:
    overdue = [x for x in context["deadlines"] if x["time_status"] == "overdue"]
    upcoming = [x for x in context["deadlines"] if x["time_status"] == "upcoming"]
    candidates = (overdue + upcoming)[:3]
    return DailyReviewAIResult(
        summary="今天的真实数据已经归档。配置 DeepSeek API 后，这里会生成个性化复盘。",
        affirmation="你至少把今天留下了可回看的记录，这比靠印象判断可靠。",
        main_issue="AI 尚未配置，因此这一版只保存程序统计，不做行为推断。",
        tomorrow_focus=[
            {"text": f"推进：{item['title']}", "deadline_id": item["id"]}
            for item in candidates
        ],
    )


def get_review(local_date: str | None = None) -> dict | None:
    day = local_date or local_today(APP_TIMEZONE)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM daily_reviews WHERE local_date = ?", (day,)).fetchone()
    return _review_row_to_dict(row) if row else None


def generate_review(local_date: str | None = None) -> dict:
    day = local_date or local_today(APP_TIMEZONE)
    context = get_today_context(day)
    stats = _stats_from_context(context)
    start, end = local_day_range_utc(day, APP_TIMEZONE)
    stats["execution"] = get_execution_stats(start, end)
    ai_available = _provider.available
    raw = None

    if ai_available:
        try:
            result, raw = _ask_review_ai(context, stats)
        except (ExternalServiceError, ValidationError, ValueError, json.JSONDecodeError):
            # Facts must still be saved even if the model is unavailable or returns malformed JSON.
            result = _fallback_result(context)
            ai_available = False
    else:
        result = _fallback_result(context)

    now = to_utc_iso(utc_now())
    stats_json = json.dumps(stats, ensure_ascii=False)
    focus_json = json.dumps([x.model_dump() for x in result.tomorrow_focus], ensure_ascii=False)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO daily_reviews (
                local_date, stats_json, ai_summary, affirmation, main_issue,
                tomorrow_focus_json, raw_ai_json, status, confirmed_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?)
            ON CONFLICT(local_date) DO UPDATE SET
                stats_json = excluded.stats_json,
                ai_summary = excluded.ai_summary,
                affirmation = excluded.affirmation,
                main_issue = excluded.main_issue,
                tomorrow_focus_json = excluded.tomorrow_focus_json,
                raw_ai_json = excluded.raw_ai_json,
                status = 'draft',
                confirmed_at = NULL,
                updated_at = excluded.updated_at
            """,
            (
                day,
                stats_json,
                result.summary,
                result.affirmation,
                result.main_issue,
                focus_json,
                raw,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM daily_reviews WHERE local_date = ?", (day,)).fetchone()
    return _review_row_to_dict(row, ai_available=ai_available)


def confirm_review(data: DailyReviewConfirm, local_date: str | None = None) -> dict:
    day = local_date or local_today(APP_TIMEZONE)
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        valid_ids = {row["id"] for row in conn.execute("SELECT id FROM deadlines").fetchall()}
    safe_focus = [
        {"text": item.text, "deadline_id": item.deadline_id if item.deadline_id in valid_ids else None}
        for item in data.tomorrow_focus[:3]
    ]
    focus_json = json.dumps(safe_focus, ensure_ascii=False)
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM daily_reviews WHERE local_date = ?", (day,)).fetchone()
    if row is None:
        generate_review(day)

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE daily_reviews
            SET tomorrow_focus_json = ?, status = 'confirmed', confirmed_at = ?, updated_at = ?
            WHERE local_date = ?
            """,
            (focus_json, now, now, day),
        )
        updated = conn.execute("SELECT * FROM daily_reviews WHERE local_date = ?", (day,)).fetchone()
    return _review_row_to_dict(updated)
