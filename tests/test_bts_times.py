from datetime import date, datetime

import pytest

from hubops.bts_times import combine_local, parse_hhmm_to_minutes, resolve_leg_times


class TestParseHhmmToMinutes:
    def test_ordinary_time(self):
        assert parse_hhmm_to_minutes("0835") == 8 * 60 + 35
        assert parse_hhmm_to_minutes(835) == 8 * 60 + 35

    def test_float_with_trailing_dot_zero(self):
        assert parse_hhmm_to_minutes("835.0") == 8 * 60 + 35

    def test_midnight_code_2400_is_1440(self):
        assert parse_hhmm_to_minutes("2400") == 1440

    def test_zero_is_midnight(self):
        assert parse_hhmm_to_minutes("0000") == 0

    def test_blank_and_none_are_missing(self):
        assert parse_hhmm_to_minutes(None) is None
        assert parse_hhmm_to_minutes("") is None
        assert parse_hhmm_to_minutes(float("nan")) is None

    def test_rejects_out_of_range_hour(self):
        with pytest.raises(ValueError):
            parse_hhmm_to_minutes("2401")

    def test_rejects_out_of_range_minute(self):
        with pytest.raises(ValueError):
            parse_hhmm_to_minutes("0160")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_hhmm_to_minutes("abcd")


class TestCombineLocal:
    def test_ordinary_time_stays_on_day(self):
        dt = combine_local(date(2026, 4, 15), 8 * 60 + 35)
        assert dt == datetime(2026, 4, 15, 8, 35)

    def test_2400_rolls_to_next_day_midnight(self):
        dt = combine_local(date(2026, 4, 15), 1440)
        assert dt == datetime(2026, 4, 16, 0, 0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            combine_local(date(2026, 4, 15), -1)


class TestResolveLegTimes:
    def test_same_day_domestic_leg(self):
        # DFW (CDT, UTC-5) -> a same-day Eastern leg: 0800 dep + 2h15m
        # elapsed lands at 1115 EDT the same calendar date -- no rollover.
        result = resolve_leg_times(
            flight_date=date(2026, 4, 15),
            dep_hhmm="0800",
            arr_hhmm="1115",
            elapsed_min=135,
            origin_tz="America/Chicago",
            dest_tz="America/New_York",
        )
        assert result is not None
        assert result.arr_date == date(2026, 4, 15)
        assert abs(result.elapsed_mismatch_min) < 1

    def test_redeye_crossing_midnight_despite_westward_timezone(self):
        # DFW (CDT, UTC-5) -> LAX (PDT, UTC-7): departs 2330, a 3h flight lands
        # at 0030 PDT -- the -2hr timezone shift is NOT enough to cancel out
        # the elapsed time, so local midnight is still crossed. This is
        # exactly the case a naive "arr_hhmm < dep_hhmm => same day" or
        # "always add a day" heuristic gets wrong.
        result = resolve_leg_times(
            flight_date=date(2026, 4, 15),
            dep_hhmm="2330",
            arr_hhmm="0030",
            elapsed_min=180,
            origin_tz="America/Chicago",
            dest_tz="America/Los_Angeles",
        )
        assert result is not None
        assert result.arr_date == date(2026, 4, 16)
        assert abs(result.elapsed_mismatch_min) < 1

    def test_scheduled_midnight_code_on_departure(self):
        # CRSDepTime of "2400" on FlightDate means the scheduled departure is
        # actually 00:00 the next calendar day.
        result = resolve_leg_times(
            flight_date=date(2026, 4, 15),
            dep_hhmm="2400",
            arr_hhmm="0200",
            elapsed_min=120,
            origin_tz="America/Chicago",
            dest_tz="America/Chicago",
        )
        assert result is not None
        assert result.dep_local.date() == date(2026, 4, 16)
        assert result.arr_date == date(2026, 4, 16)

    def test_missing_elapsed_returns_none(self):
        result = resolve_leg_times(
            flight_date=date(2026, 4, 15),
            dep_hhmm="0800",
            arr_hhmm="1015",
            elapsed_min=None,
            origin_tz="America/Chicago",
            dest_tz="America/New_York",
        )
        assert result is None

    def test_cancelled_leg_blank_times_returns_none(self):
        result = resolve_leg_times(
            flight_date=date(2026, 4, 15),
            dep_hhmm="0800",
            arr_hhmm="",
            elapsed_min=135,
            origin_tz="America/Chicago",
            dest_tz="America/New_York",
        )
        assert result is None
