from shared.timeutil import (add_days, clock_of, day_of, days_between, is_office_hours, quantize, split_ts,
                             to_seconds, with_date)


def test_split_and_bare_times():
    assert split_ts("2026-01-05 09:14:02") == ("2026-01-05", "09:14:02")
    assert split_ts("09:14:02") == (None, "09:14:02")
    assert day_of("09:14:02") is None and clock_of("2026-01-05 09:14:02") == "09:14:02"


def test_with_date_only_touches_bare_times():
    assert with_date("12:00:00", "2026-01-09") == "2026-01-09 12:00:00"
    assert with_date("2026-01-01 12:00:00", "2026-01-09") == "2026-01-01 12:00:00"
    assert with_date("12:00:00", None) == "12:00:00"


def test_to_seconds_is_absolute_across_days():
    assert to_seconds("2026-01-06 00:00:10") - to_seconds("2026-01-05 23:59:50") == 20
    assert to_seconds("10:00:00") == 36000
    assert days_between("2026-01-12 08:00:00", "2026-01-05 08:00:00") == 7


def test_plain_string_order_matches_time_order_in_the_same_format():
    stamps = ["2026-01-05 17:00:00", "2026-01-06 08:00:00", "2026-01-05 08:30:00"]
    assert sorted(stamps) == sorted(stamps, key=to_seconds)


def test_quantize_keeps_the_date_and_office_hours_use_the_clock():
    assert quantize("2026-01-05 09:44:59", 15) == "2026-01-05 09:30:00"
    assert quantize("09:44:59", 60) == "09:00:00"
    assert is_office_hours("2026-01-05 08:00:00") and not is_office_hours("2026-01-05 18:00:01")
    assert add_days("2026-01-31", 1) == "2026-02-01"
