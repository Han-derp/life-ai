from math import ceil

from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.core.time_utils import parse_utc, to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.pomodoro import PomodoroStart
from app.services import action_step_service


def _target_label(conn, row) -> str:
    # ⚠️ 顺序有讲究 ✓：行动步骤 → 日程 → DDL → 临时标签 ✓
    #（一个番茄只能绑一个 ✓，所以顺序只是兜底 ✓）
    if "action_step_id" in row.keys() and row["action_step_id"] is not None:
        step = conn.execute(
            "SELECT title FROM action_plan_steps WHERE id = ?", (row["action_step_id"],)
        ).fetchone()
        return step["title"] if step else "已删除的步骤"
    if row["schedule_event_id"] is not None:
        item = conn.execute("SELECT title FROM schedule_events WHERE id = ?", (row["schedule_event_id"],)).fetchone()
        return item["title"] if item else "已删除日程"
    if row["deadline_id"] is not None:
        item = conn.execute("SELECT title FROM deadlines WHERE id = ?", (row["deadline_id"],)).fetchone()
        return item["title"] if item else "已删除DDL"
    return row["custom_label"] or "专注"


def _row_to_dict(conn, row) -> dict:
    focus, wall = _durations(row, utc_now())
    return {
        "paused": row["paused_at"] is not None,
        "focus_seconds": focus,
        "elapsed_seconds": wall,
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "planned_minutes": row["planned_minutes"],
        "actual_minutes": row["actual_minutes"],
        "status": row["status"],
        "schedule_event_id": row["schedule_event_id"],
        "deadline_id": row["deadline_id"],
        "action_step_id": row["action_step_id"] if "action_step_id" in row.keys() else None,
        "custom_label": row["custom_label"],
        "target_label": _target_label(conn, row),
        "note": row["note"],
    }


def start_pomodoro(data: PomodoroStart) -> dict:
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        running = conn.execute(
            "SELECT id FROM pomodoro_sessions WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if running is not None:
            raise ConflictError("已有正在进行的番茄，请先结束或取消。")

        if data.schedule_event_id is not None:
            exists = conn.execute("SELECT id FROM schedule_events WHERE id = ?", (data.schedule_event_id,)).fetchone()
            if exists is None:
                raise NotFoundError("绑定的日程不存在。")
        if data.deadline_id is not None:
            exists = conn.execute("SELECT id FROM deadlines WHERE id = ?", (data.deadline_id,)).fetchone()
            if exists is None:
                raise NotFoundError("绑定的 DDL 不存在。")
        if data.action_step_id is not None:
            exists = conn.execute(
                "SELECT id FROM action_plan_steps WHERE id = ?", (data.action_step_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError("绑定的行动步骤不存在。")

        cursor = conn.execute(
            """
            INSERT INTO pomodoro_sessions (
                started_at, ended_at, planned_minutes, actual_minutes,
                status, schedule_event_id, deadline_id, action_step_id,
                custom_label, note, created_at, updated_at
            ) VALUES (?, NULL, ?, NULL, 'running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                data.planned_minutes,
                data.schedule_event_id,
                data.deadline_id,
                data.action_step_id,
                data.custom_label.strip() if data.custom_label else None,
                data.note,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM pomodoro_sessions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_dict(conn, row)


def get_current_pomodoro() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pomodoro_sessions WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(conn, row) if row else None


def _durations(row, now):
    end = parse_utc(row["ended_at"]) if row["ended_at"] else now
    wall = max(0, int((end - parse_utc(row["started_at"])).total_seconds()))
    paused = row["paused_seconds"]
    if row["paused_at"]:
        paused += max(0, int((end - parse_utc(row["paused_at"])).total_seconds()))
    if row["ended_at"] and row["focus_seconds"] is not None:
        return row["focus_seconds"], row["elapsed_seconds"]
    return max(0, wall - paused), wall


def _load(conn, session_id):
    row = conn.execute("SELECT * FROM pomodoro_sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise NotFoundError("番茄不存在。")
    return row


def _set_paused(session_id, pause):
    now = utc_now()
    stamp = to_utc_iso(now)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _load(conn, session_id)
        if row["status"] != "running":
            raise DomainValidationError("已经结束的番茄不能暂停或恢复。")
        if bool(row["paused_at"]) == pause:
            return _row_to_dict(conn, row)
        if pause:
            conn.execute("UPDATE pomodoro_sessions SET paused_at=?, updated_at=? WHERE id=?", (stamp, stamp, session_id))
            conn.execute("UPDATE action_plan_steps SET interruption_count=interruption_count+1 WHERE id=?", (row["action_step_id"],))
        else:
            seconds = max(0, int((now - parse_utc(row["paused_at"])).total_seconds()))
            conn.execute("UPDATE pomodoro_sessions SET paused_seconds=paused_seconds+?, paused_at=NULL, updated_at=? WHERE id=?", (seconds, stamp, session_id))
        return _row_to_dict(conn, _load(conn, session_id))


def pause_pomodoro(session_id):
    return _set_paused(session_id, True)


def resume_pomodoro(session_id):
    return _set_paused(session_id, False)


def _close(session_id, status):
    now = utc_now()
    stamp = to_utc_iso(now)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _load(conn, session_id)
        if row["status"] == status:
            return _row_to_dict(conn, row)
        if row["status"] != "running":
            raise DomainValidationError("番茄已结束，不能更改结束方式。")
        focus, wall = _durations(row, now)
        conn.execute("UPDATE pomodoro_sessions SET ended_at=?, actual_minutes=?, focus_seconds=?, elapsed_seconds=?, status=?, updated_at=? WHERE id=?",
                     (stamp, ceil(focus/60), focus, wall, status, stamp, session_id))
        conn.execute("""INSERT INTO timeline_events
            (title,start_at,end_at,source_type,source_id,note,created_at,updated_at)
            VALUES (?,?,?,'pomodoro',?,?,?,?)""",
            (_target_label(conn,row),row["started_at"],stamp,session_id,
             f"计入专注 {focus} 秒；经过 {wall} 秒；{status}",stamp,stamp))
        # Persist session, timeline and step totals atomically; retries cannot add twice.
        conn.execute("""UPDATE action_plan_steps SET actual_seconds=actual_seconds+?,
            actual_minutes=CAST((actual_seconds+?+59)/60 AS INTEGER),
            pomodoro_count=pomodoro_count+1,updated_at=? WHERE id=?""",
            (focus,focus,stamp,row["action_step_id"]))
        return _row_to_dict(conn, _load(conn,session_id))


def finish_pomodoro(session_id):
    return _close(session_id, "completed")


def cancel_pomodoro(session_id):
    return _close(session_id, "cancelled")


def list_pomodoros_for_range(start_utc: str, end_utc: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pomodoro_sessions
            WHERE started_at >= ? AND started_at < ?
            ORDER BY started_at ASC, id ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        return [_row_to_dict(conn, row) for row in rows]
