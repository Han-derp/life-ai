from datetime import datetime, timezone

import pytest

from app.core.time_utils import (
    local_date_of,
    local_datetime_to_utc_iso,
    local_day_range_utc,
    local_time_from_normalized,
    local_today,
    minutes_from_midnight,
    normalize_time_minutes,
    parse_utc,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)


TZ = "Asia/Shanghai"


def test_utc_now_is_aware():
    now = utc_now()
    assert now.tzinfo is not None


def test_to_utc_iso_requires_aware_datetime():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 1, 1))


def test_to_utc_iso_formats_with_trailing_z():
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert to_utc_iso(dt) == "2026-01-01T12:00:00Z"


def test_parse_utc_accepts_z_suffix():
    dt = parse_utc("2026-01-01T12:00:00Z")
    assert dt.tzinfo is not None
    assert dt.hour == 12


def test_parse_utc_requires_timezone():
    with pytest.raises(ValueError):
        parse_utc("2026-01-01T12:00:00")


def test_local_datetime_to_utc_iso_shanghai_offset():
    # Asia/Shanghai is UTC+8, no DST.
    utc_iso = local_datetime_to_utc_iso("2026-01-01T20:00", TZ)
    assert utc_iso == "2026-01-01T12:00:00Z"


def test_local_day_range_utc_spans_24_hours():
    start, end = local_day_range_utc("2026-01-01", TZ)
    delta = parse_utc(end) - parse_utc(start)
    assert delta.total_seconds() == 24 * 3600


def test_local_date_of_roundtrip():
    utc_iso = local_datetime_to_utc_iso("2026-01-01T00:30", TZ)
    assert local_date_of(utc_iso, TZ) == "2026-01-01"


def test_local_today_matches_zoneinfo_date():
    day = local_today(TZ)
    assert len(day) == 10
    assert day.count("-") == 2


def test_utc_to_local_iso_converts_offset():
    local_iso = utc_to_local_iso("2026-01-01T12:00:00Z", TZ)
    assert local_iso.startswith("2026-01-01T20:00")


def test_minutes_from_midnight():
    assert minutes_from_midnight("00:00") == 0
    assert minutes_from_midnight("23:59") == 1439


def test_normalize_time_minutes_rejects_bad_anchor():
    with pytest.raises(ValueError):
        normalize_time_minutes("10:00", -1)
    with pytest.raises(ValueError):
        normalize_time_minutes("10:00", 1440)


def test_normalize_time_minutes_same_day_and_overnight():
    anchor = 18 * 60  # sleep-tracking anchor at 18:00
    assert normalize_time_minutes("23:30", anchor) == 330
    assert normalize_time_minutes("00:30", anchor) == 390
    # Values right at the anchor normalize to 0.
    assert normalize_time_minutes("18:00", anchor) == 0


def test_local_time_from_normalized_is_inverse_of_normalize():
    anchor = 18 * 60
    for raw in ("23:30", "00:30", "18:00", "17:59"):
        normalized = normalize_time_minutes(raw, anchor)
        assert local_time_from_normalized(normalized, anchor) == raw
