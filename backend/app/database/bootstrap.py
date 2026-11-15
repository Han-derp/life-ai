from app.core.config import APP_TIMEZONE, DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE, PERSONA
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.database.migrations import migrate


def initialize_defaults() -> None:
    now = to_utc_iso(utc_now())
    defaults = {
        "timezone": APP_TIMEZONE,
        "model_name": DEEPSEEK_MODEL,
        "temperature": str(DEEPSEEK_TEMPERATURE),
        "daily_review_time": "22:30",
        # S3：当前启用人格。ON CONFLICT DO NOTHING，不会覆盖用户在应用内的切换。
        "persona": PERSONA,
    }
    with get_connection() as conn:
        for key, value in defaults.items():
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, value, now),
            )

        conversation = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if conversation is None:
            conn.execute(
                """
                INSERT INTO conversations (title, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                ("和你的对话", now, now),
            )


def bootstrap_database() -> None:
    migrate()
    initialize_defaults()
