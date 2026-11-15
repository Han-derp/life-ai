"""Shared daily/weekly execution measures. Session effort is attributed to end date.
Completed-step deviation uses lifetime recorded effort, not the reporting-period effort.
"""
from app.database.connection import get_connection


def get_execution_stats(start_utc: str, end_utc: str) -> dict:
    with get_connection() as conn:
        sessions = conn.execute("""SELECT COUNT(*) AS n,
            COALESCE(SUM(COALESCE(focus_seconds,actual_minutes*60,0)),0) AS seconds
            FROM pomodoro_sessions WHERE status IN ('completed','cancelled')
            AND ended_at>=? AND ended_at<?""", (start_utc,end_utc)).fetchone()
        steps = conn.execute("""SELECT * FROM action_plan_steps
            WHERE status='completed' AND completed_at>=? AND completed_at<?""",
            (start_utc,end_utc)).fetchall()
        eligible = [x for x in steps if x['confirmed_minutes'] is not None and x['pomodoro_count']>0]
        expected = sum(x['confirmed_minutes']*60 for x in eligible)
        actual = sum(x['actual_seconds'] for x in eligible)
    return {
        'recorded_sessions':sessions['n'], 'recorded_focus_seconds':sessions['seconds'],
        'completed_steps':len(steps), 'comparable_steps':len(eligible),
        'unmeasured_steps':len(steps)-len(eligible),
        'confirmed_estimate_seconds':expected, 'completed_actual_seconds':actual,
        'deviation_seconds':actual-expected if eligible else None,
        'attribution':'session_end_date; deviation_for_completed_steps_uses_lifetime_effort',
    }
