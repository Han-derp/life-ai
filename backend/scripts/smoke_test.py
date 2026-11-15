from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.core.time_utils import (
    local_date_of,
    local_day_range_utc,
    normalize_time_minutes,
    to_utc_iso,
    utc_now,
)
from app.database.connection import (
    DB_PATH,
    get_connection,
)


EXPECTED_TABLES = {
    "schema_migrations",
    "app_settings",
    "schedule_events",
    "deadlines",
    "habits",
    "habit_logs",
    "pomodoro_sessions",
    "timeline_events",
    "conversations",
    "messages",
    "daily_reviews",
    "context_exclusions",
    # S4 记忆库
    "raw_entries",
    "observations",
    "impressions",
    "impression_evidence",
    "tags",
    "observation_tags",
    "relationship",
    "commitments",
    # S7 规则
    "her_rules",
    # S8 主动联系
    "proactive_contacts",
    # S8 B 类基线预警
    "baseline_alerts",
    "tag_suggestions",
    "action_plans",
    "action_plan_steps",
}

# 每次新增 migration 都要同步这里（S8 起 7；012 起 12；…；016 起 16；017 起 17；018 起 18；019 起 19）。
EXPECTED_SCHEMA_VERSION = 19


def check_database():
    with get_connection() as conn:
        foreign_keys = conn.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]

        journal_mode = conn.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]

        tables = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }

        missing = EXPECTED_TABLES - tables

        assert foreign_keys == 1, (
            "foreign_keys 没有开启"
        )

        assert journal_mode.lower() == "wal", (
            f"journal_mode 不是 WAL，而是 {journal_mode}"
        )

        assert not missing, (
            f"缺少数据表：{missing}\n"
            "这通常不是迁移坏了，而是**这个库还没被迁移过**：\n"
            "migrate() 是在应用启动时跑的（bootstrap_database），"
            "所以先启动一次服务；或者直接执行：\n"
            "  python -c \"from app.database.migrations import migrate; migrate()\"\n"
            "（本脚本只做检查、故意不自动迁移——否则它会把要检查的东西自己修掉。）"
        )

        version = conn.execute(
            """
            SELECT MAX(version)
            FROM schema_migrations
            """
        ).fetchone()[0]

        assert version == EXPECTED_SCHEMA_VERSION, (
            f"migration version 应为 {EXPECTED_SCHEMA_VERSION}，实际为 {version}"
        )

        timezone_row = conn.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = 'timezone'
            """
        ).fetchone()

        assert timezone_row is not None, (
            "app_settings 中没有 timezone"
        )

        assert timezone_row["value"] == APP_TIMEZONE, (
            "数据库 timezone 与 .env 不一致"
        )

        conversation_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM conversations
            """
        ).fetchone()[0]

        assert conversation_count == 1, (
            f"默认会话应该只有1个，实际有 {conversation_count}"
        )


def check_time_utils():
    ZoneInfo(APP_TIMEZONE)

    now = utc_now()
    now_iso = to_utc_iso(now)

    assert now_iso.endswith("Z"), (
        f"UTC ISO 应以 Z 结尾：{now_iso}"
    )

    local_date = local_date_of(
        now_iso,
        APP_TIMEZONE,
    )

    start_utc, end_utc = local_day_range_utc(
        local_date,
        APP_TIMEZONE,
    )

    assert start_utc < end_utc

    print(
        f"  当前 UTC：{now_iso}"
    )
    print(
        f"  当前当地日期：{local_date}"
    )
    print(
        f"  今日 UTC 范围：{start_utc} → {end_utc}"
    )

    # 睡觉习惯：
    # 以18:00为一天睡眠时间线的起点
    anchor = 18 * 60

    bedtime_2330 = normalize_time_minutes(
        "23:30",
        anchor,
    )

    bedtime_0030 = normalize_time_minutes(
        "00:30",
        anchor,
    )

    assert bedtime_2330 == 330, (
        f"23:30 应归一化为330，实际为 {bedtime_2330}"
    )

    assert bedtime_0030 == 390, (
        f"00:30 应归一化为390，实际为 {bedtime_0030}"
    )

    assert bedtime_0030 > bedtime_2330


def check_indexes():
    expected_indexes = {
        "idx_schedule_start_at",
        "idx_schedule_status",
        "idx_deadlines_due_at",
        "idx_deadlines_parent",
        "idx_deadlines_status",
        "idx_habit_logs_date",
        "idx_habit_logs_habit",
        "idx_pomodoro_started",
        "idx_pomodoro_deadline",
        "idx_pomodoro_schedule",
        "idx_timeline_start",
        "idx_messages_conversation_created",
        "idx_daily_reviews_date",
        "idx_timeline_source_unique",
    }

    with get_connection() as conn:
        indexes = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }

    missing = expected_indexes - indexes

    assert not missing, (
        f"缺少索引：{missing}"
    )


def main():
    print(f"数据库：{DB_PATH}")
    print()

    check_database()
    print("✓ 数据库结构")

    check_indexes()
    print("✓ 数据库索引")

    check_time_utils()
    print("✓ 时间 / 时区")

    print()
    print("阶段一 Smoke Test 全部通过。")


if __name__ == "__main__":
    main()