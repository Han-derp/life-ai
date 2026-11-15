from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime 必须包含时区信息，不能传入 naive datetime。")
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError("UTC 时间字符串必须包含时区。")
    return dt.astimezone(UTC)


def local_now(timezone_name: str) -> datetime:
    return utc_now().astimezone(ZoneInfo(timezone_name))


def current_time_note(timezone_name: str, moment: datetime | None = None) -> str:
    """给「最新一条用户消息」附上的当前时间。

    **为什么不能放进 system prompt**：`now` 一旦进 system prompt，
    它**后面的一切**（包括整段聊天历史）都会因为前缀不匹配而失去 KV 缓存 ——
    实测缓存命中能省 20–100 倍费用（MODEL_USAGE_SPEC §4.4）。
    而「最新那条用户消息」**每次请求本来就是新的**，
    把时间挂在它后面，缓存命中率一点不受影响。

    **为什么必须有它**：这是生活管理的地基。没有时间，他连"现在几点"都答不了
    （真实对话里他确实拒答了），也就说不了"该睡了""别熬到后半夜"。

    只给事实，不给判断（不变量 4：事实由数据来，不由推断来）——
    "现在很晚了"要他自己看时间得出，不替他下结论。
    """
    now = moment or utc_now()
    local = now.astimezone(ZoneInfo(timezone_name))
    weekday = "一二三四五六日"[local.weekday()]
    return f"（现在：{local:%Y-%m-%d} 周{weekday} {local:%H:%M}）"


def local_today(timezone_name: str) -> str:
    return local_now(timezone_name).date().isoformat()


def local_date_of(utc_iso: str, timezone_name: str) -> str:
    return parse_utc(utc_iso).astimezone(ZoneInfo(timezone_name)).date().isoformat()


def local_day_range_utc(local_date: str, timezone_name: str) -> tuple[str, str]:
    day = date.fromisoformat(local_date)
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return to_utc_iso(start_local), to_utc_iso(end_local)


def local_datetime_to_utc_iso(value: str, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return to_utc_iso(dt)


def utc_to_local_iso(utc_iso: str, timezone_name: str) -> str:
    return parse_utc(utc_iso).astimezone(ZoneInfo(timezone_name)).isoformat(timespec="minutes")


def minutes_from_midnight(value: str | time) -> int:
    parsed = time.fromisoformat(value) if isinstance(value, str) else value
    return parsed.hour * 60 + parsed.minute


def normalize_time_minutes(value: str | time, anchor_minute: int) -> int:
    if not 0 <= anchor_minute < 1440:
        raise ValueError("anchor_minute 必须在 0～1439 之间。")
    value_minutes = minutes_from_midnight(value)
    if value_minutes >= anchor_minute:
        return value_minutes - anchor_minute
    return value_minutes + 1440 - anchor_minute


def local_time_from_normalized(normalized_minutes: int, anchor_minute: int) -> str:
    raw = (anchor_minute + int(normalized_minutes)) % 1440
    return f"{raw // 60:02d}:{raw % 60:02d}"


def local_expiry_to_utc(value: str | None, timezone_name: str) -> str | None:
    """把"期限"归一化成 UTC ISO。

    三种写法：

    - ``"2026-06-01"``       —— **当日有效**（到次日 00:00 当地时间为止）
    - ``"2026-06-01T23:30"`` —— 到那一刻为止
    - ``None`` / 空串        —— 永久（返回 ``None``）

    日期写法是专门为"我今天不想被打扰"准备的：说"今天"就该是今天，不能变成永久。

    **规则（her_rules）和承诺（commitments）共用这一个函数**——
    两者的"期限"语义必须一模一样，各写一份迟早会漂移。
    解析失败抛 ``ValueError``，由调用方翻译成领域错误。
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    if len(raw) == 10:
        day = date.fromisoformat(raw)
        next_day = (day + timedelta(days=1)).isoformat()
        start_of_next_day, _ = local_day_range_utc(next_day, timezone_name)
        return start_of_next_day

    return local_datetime_to_utc_iso(raw, timezone_name)
