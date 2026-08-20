from __future__ import annotations

from datetime import date, datetime

from adapters.util import parse_date, status_to_event, to_float, to_int


def test_parse_date_returns_none_on_missing_or_blank() -> None:
    assert parse_date(None) is None
    assert parse_date("   ") is None


def test_parse_date_accepts_datetime_and_date_objects() -> None:
    assert parse_date(datetime(2026, 8, 15, 10, 30, 0)) == date(2026, 8, 15)
    assert parse_date(date(2026, 8, 15)) == date(2026, 8, 15)


def test_parse_date_handles_iso_with_time_and_z() -> None:
    assert parse_date("2026-08-15T10:00:00Z") == date(2026, 8, 15)


def test_parse_date_handles_bad_iso_with_t_and_other_formats() -> None:
    assert parse_date("2026-08-15Tnot-a-time") == date(2026, 8, 15)  # falls back to ISO prefix
    assert parse_date("08/15/2026") == date(2026, 8, 15)
    assert parse_date("15 Aug 2026") == date(2026, 8, 15)


def test_parse_date_handles_dot_separated_iso() -> None:
    assert parse_date("2026.08.15") == date(2026, 8, 15)
    assert parse_date("2023.01.01") == date(2023, 1, 1)


def test_to_float_rejects_missing_bool_and_blank() -> None:
    assert to_float(None) is None
    assert to_float(True) is None
    assert to_float("   ") is None


def test_to_float_rejects_non_numeric_and_non_finite() -> None:
    assert to_float("abc") is None
    assert to_float(float("inf")) is None
    assert to_float("12.5") == 12.5
    assert to_float("1,250") == 1250.0


def test_to_int_only_accepts_whole_numbers() -> None:
    assert to_int("abc") is None
    assert to_int("3.7") is None
    assert to_int("5") == 5
    assert to_int(5.0) == 5


def test_status_to_event_missing_and_bool() -> None:
    assert status_to_event(None) is None
    assert status_to_event(True) == 1
    assert status_to_event(False) == 0


def test_status_to_event_numeric_and_unknown() -> None:
    assert status_to_event(1) == 1
    assert status_to_event(0) == 0
    assert status_to_event(2.0) is None


def test_status_to_event_strings() -> None:
    assert status_to_event("ACTIVE") == 0
    assert status_to_event("churned") == 1
    assert status_to_event("CANCELED") == 1
    assert status_to_event("mystery_status") is None
