"""B 类基线预警（DEVELOPMENT_PLAN v2.0 · S8）。

她**只在两件事上**可以越过"只读"来关心你（MEMORY_DESIGN §5.1-B）：

1. ``late_night``      作息明显变晚
2. ``deadline_stall``  DDL 即将逾期、而且看不到进展

为什么只有两项：预警是她**主动开口说"我注意到……"**。
一旦多了，关心就变成监控。所以"只有 2 项"这条规则直接写在表的 CHECK 约束上——
想加第三种，你得改那张表，而改的时候你会想起这条规矩。

触发条件同样是硬的：**连续 3 天以上偏离常态**。
单独一天熬夜不会被说，因为那不是"偏离"，那只是某一天。

⚠️ **不采集手机数据**（§6.3-B）。作息完全从**消息时间戳**推出来。
这意味着它是"你在这里出现的时刻"而不是"你睡着的时刻"，
所以阈值刻意放得宽（默认 1.5 小时）——宁可漏报，不要误报。
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import (
    local_day_range_utc,
    parse_utc,
    to_utc_iso,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.llm import factory
from app.scheduler import clock
from app.workflows import tone

KIND_LATE_NIGHT = "late_night"
KIND_DEADLINE_STALL = "deadline_stall"

# 用过去两周里的"较早那些天"算常态。最近几天是要判断的对象，不能进基线——
# 这个偏移**必须严格大于** LATE_NIGHT_STREAK_DAYS，否则"最近三天"会和基线窗口
# 重叠，等于拿要被判断的数据当尺子。
BASELINE_WINDOW_DAYS = 14
BASELINE_EXCLUDE_RECENT_DAYS = 3
MIN_BASELINE_DAYS = 3

LATE_NIGHT_DRIFT_HOURS = 1.5
LATE_NIGHT_STREAK_DAYS = 3

BASELINE_EXCLUDE_RECENT_DAYS = LATE_NIGHT_STREAK_DAYS + 1

STALL_DUE_HOURS = 48
STALL_PROGRESS_WINDOW_DAYS = 3

# 凌晨这几点算"前一天深夜"——不然 01:00 会被当成"很早"。
NIGHT_OWL_CUTOFF_HOUR = 5

# 「我注意到……怎么了？」也是她在说话，所以走 `chat`。
_provider = factory.current_provider("chat")


# ---------- 作息：从消息时间戳推 ----------


def _daily_last_message_hour(local_date: str) -> float | None:
    """某个当地自然日里，最后一条用户消息是几点（小时，跨午夜按 +24 计）。"""
    start_utc, end_utc = local_day_range_utc(local_date, APP_TIMEZONE)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT created_at FROM messages
            WHERE role = 'user' AND created_at >= ? AND created_at < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (start_utc, end_utc),
        ).fetchone()

    if row is None:
        return None

    local = parse_utc(row["created_at"]).astimezone(ZoneInfo(APP_TIMEZONE))
    hour = local.hour + local.minute / 60
    return hour + 24 if local.hour < NIGHT_OWL_CUTOFF_HOUR else hour


def baseline_hour(moment: datetime) -> float | None:
    """常态是几点。样本太少就返回 ``None``——宁可不说，也不误报。"""
    today = clock.local_date(moment)
    hours = []
    for offset in range(BASELINE_EXCLUDE_RECENT_DAYS, BASELINE_WINDOW_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        hour = _daily_last_message_hour(day)
        if hour is not None:
            hours.append(hour)

    if len(hours) < MIN_BASELINE_DAYS:
        return None
    return sum(hours) / len(hours)


def detect_late_night(moment: datetime) -> dict | None:
    baseline = baseline_hour(moment)
    if baseline is None:
        return None

    today = clock.local_date(moment)
    recent: list[tuple[str, float | None]] = []
    for offset in range(1, LATE_NIGHT_STREAK_DAYS + 1):
        day = (today - timedelta(days=offset)).isoformat()
        recent.append((day, _daily_last_message_hour(day)))

    # 这三天必须**每一天都真的出现过**：空一天就是"沉默"，不是"晚睡"，
    # 沉默归机制一管，别两套机制抢同一件事。
    if any(hour is None for _, hour in recent):
        return None

    threshold = baseline + LATE_NIGHT_DRIFT_HOURS
    if not all(hour >= threshold for _, hour in recent if hour is not None):
        return None

    return {
        "kind": KIND_LATE_NIGHT,
        "baseline_hour": round(baseline, 2),
        "threshold_hour": round(threshold, 2),
        "recent": [{"local_date": day, "hour": round(hour, 2)} for day, hour in recent],
    }


# ---------- DDL：快到点了而且没动静 ----------


def detect_deadline_stall(moment: datetime) -> dict | None:
    now_iso = to_utc_iso(moment)
    horizon = to_utc_iso(moment + timedelta(hours=STALL_DUE_HOURS))
    progress_since = to_utc_iso(moment - timedelta(days=STALL_PROGRESS_WINDOW_DAYS))

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM deadlines
            WHERE completion_status = 'pending'
              AND due_at >= ?
              AND due_at < ?
            ORDER BY due_at ASC
            """,
            (now_iso, horizon),
        ).fetchall()

        for row in rows:
            # "有进展" = 这段时间里有完成的番茄绑在它（或它的子任务）身上
            progress = conn.execute(
                """
                SELECT id FROM pomodoro_sessions
                WHERE status = 'completed'
                  AND ended_at >= ?
                  AND (
                      deadline_id = ?
                      OR deadline_id IN (
                          SELECT id FROM deadlines WHERE parent_id = ?
                      )
                  )
                LIMIT 1
                """,
                (progress_since, row["id"], row["id"]),
            ).fetchone()
            if progress is not None:
                continue

            return {
                "kind": KIND_DEADLINE_STALL,
                "deadline_id": row["id"],
                "title": row["title"],
                "due_at": row["due_at"],
                "due_at_local": utc_to_local_iso(row["due_at"], APP_TIMEZONE),
            }

    return None


def detect(moment: datetime | None = None) -> list[dict]:
    now = moment or clock.now_utc()
    found = []
    for finding in (detect_late_night(now), detect_deadline_stall(now)):
        if finding is not None:
            found.append(finding)
    return found


# ---------- 存取 ----------


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "detail": json.loads(row["detail_json"]),
        "content": row["content"],
        "detected_at": row["detected_at"],
        "voiced_at": row["voiced_at"],
        "dismissed_at": row["dismissed_at"],
        "voiced": row["voiced_at"] is not None,
    }


def _has_open_alert(kind: str) -> bool:
    """同一段偏离不重复记：没被处理掉就不再记一条。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM baseline_alerts
            WHERE kind = ? AND dismissed_at IS NULL
            LIMIT 1
            """,
            (kind,),
        ).fetchone()
    return row is not None


def scan(moment: datetime | None = None) -> list[dict]:
    """检测并记下来。返回这次新记的几条（可能为空）。"""
    now = moment or clock.now_utc()
    recorded = []

    for finding in detect(now):
        if _has_open_alert(finding["kind"]):
            continue

        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO baseline_alerts (
                    kind, detail_json, content, detected_at,
                    voiced_at, dismissed_at, created_at
                ) VALUES (?, ?, NULL, ?, NULL, NULL, ?)
                """,
                (
                    finding["kind"],
                    json.dumps(finding, ensure_ascii=False),
                    to_utc_iso(now),
                    to_utc_iso(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM baseline_alerts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            recorded.append(_row_to_dict(row))

    return recorded


def list_alerts(include_dismissed: bool = False, limit: int = 50) -> list[dict]:
    query = "SELECT * FROM baseline_alerts"
    if not include_dismissed:
        query += " WHERE dismissed_at IS NULL"
    query += " ORDER BY id DESC LIMIT ?"

    with get_connection() as conn:
        rows = conn.execute(query, (max(1, min(limit, 200)),)).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_alert(alert_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM baseline_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("这条预警不存在。")
    return _row_to_dict(row)


def _generate_content(kind: str, detail: dict, moment: datetime) -> str | None:
    """说出口的那一句。第一句永远是「我注意到……怎么了？」（§5.1-B）。"""
    if not _provider.available:
        return tone.FALLBACK_TEXT.get(kind)

    prompt = load_prompt("proactive.md").format(
        kind=kind,
        silent_days=0,
        local_time=clock.to_local(moment).isoformat(timespec="minutes"),
    )
    payload = json.dumps({"kind": kind, "detail": detail}, ensure_ascii=False)

    try:
        raw = _provider.chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": payload},
            ],
            temperature=1.0,
        )
    except ExternalServiceError:
        return tone.FALLBACK_TEXT.get(kind)

    # 兜底文案存在，所以这里不会返回 None
    return tone.sanitize(raw, kind)


def voice_alert(alert_id: int, moment: datetime | None = None) -> dict:
    """把它说出口。已经说过就不再改（幂等）。"""
    now = moment or clock.now_utc()
    current = get_alert(alert_id)
    if current["voiced"]:
        return current

    content = _generate_content(current["kind"], current["detail"], now)
    if not content:
        raise NotFoundError("这条预警没法生成可说的话。")

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE baseline_alerts
            SET content = ?, voiced_at = ?
            WHERE id = ? AND voiced_at IS NULL
            """,
            (content, to_utc_iso(now), alert_id),
        )
    return get_alert(alert_id)


def dismiss_alert(alert_id: int, moment: datetime | None = None) -> dict:
    """处理掉/忽略掉这条。同一段偏离之后可以重新被检测。"""
    now = moment or clock.now_utc()
    get_alert(alert_id)

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE baseline_alerts
            SET dismissed_at = ?
            WHERE id = ? AND dismissed_at IS NULL
            """,
            (to_utc_iso(now), alert_id),
        )
    return get_alert(alert_id)
