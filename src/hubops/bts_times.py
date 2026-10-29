"""BTS on-time performance time wrangling.

BTS marketing-carrier on-time files give every departure column in the
*origin* airport's local clock time and every arrival column in the
*destination* airport's local clock time -- as a bare "hhmm" string/number,
with no time zone and no explicit arrival date. Two quirks make this
genuinely ambiguous to parse, not just tedious:

1. "2400" is BTS's way of writing "00:00 of the next day" rather than
   "24:00 of today" -- it shows up for scheduled times that land exactly on
   a day boundary.
2. There's no ArrDate column. A flight can look like it "arrives before it
   departs" either because it's a redeye crossing local midnight, or because
   it crossed time zones -- and these look identical in the raw hhmm fields.

The one number in the row that's immune to both problems is
CRSElapsedTime / ActualElapsedTime (scheduled/actual flight duration in
minutes) -- it doesn't care about calendar dates or time zones. So instead
of guessing with heuristics ("if arr_hhmm < dep_hhmm, add a day"), we search
a small set of candidate arrival dates and keep the one whose implied
elapsed time best matches the reported value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_MIDNIGHT_CODE = 2400
_MINUTES_PER_DAY = 1440
_CANDIDATE_DAY_OFFSETS = (-1, 0, 1, 2)


def parse_hhmm_to_minutes(raw: str | int | float | None) -> int | None:
    """Parse a BTS hhmm field into minutes since local midnight (0..1440).

    Returns None for missing/blank values (BTS leaves actual times blank for
    cancelled legs). "2400" parses to 1440, the minutes-since-midnight value
    for the *next* calendar day's 00:00.
    """
    if raw is None:
        return None
    if isinstance(raw, float) and raw != raw:  # NaN
        return None
    text = str(raw).strip()
    if text == "" or text.lower() == "nan":
        return None

    # BTS sometimes emits floats like "835.0" for integer hhmm codes.
    if text.endswith(".0"):
        text = text[:-2]

    if not text.isdigit():
        raise ValueError(f"not a valid hhmm value: {raw!r}")

    value = int(text)
    if value < 0 or value > _MIDNIGHT_CODE:
        raise ValueError(f"hhmm out of range: {raw!r}")

    if value == _MIDNIGHT_CODE:
        return _MINUTES_PER_DAY

    hour, minute = divmod(value, 100)
    if hour > 23 or minute > 59:
        raise ValueError(f"hhmm out of range: {raw!r}")
    return hour * 60 + minute


def combine_local(day: date, minutes_since_midnight: int) -> datetime:
    """Combine a calendar date with a minutes-since-midnight value (naive).

    1440 (the parsed form of "2400") rolls onto the *next* day's 00:00,
    per the BTS convention described in the module docstring.
    """
    if minutes_since_midnight < 0 or minutes_since_midnight > _MINUTES_PER_DAY:
        raise ValueError(f"minutes_since_midnight out of range: {minutes_since_midnight}")
    if minutes_since_midnight == _MINUTES_PER_DAY:
        return datetime(day.year, day.month, day.day) + timedelta(days=1)
    hour, minute = divmod(minutes_since_midnight, 60)
    return datetime(day.year, day.month, day.day, hour, minute)


@dataclass(frozen=True)
class ResolvedLegTimes:
    dep_local: datetime  # tz-aware, origin local
    dep_utc: datetime
    arr_local: datetime  # tz-aware, destination local
    arr_utc: datetime
    arr_date: date
    elapsed_mismatch_min: float  # (arr_utc - dep_utc) minus reported elapsed; ~0 is a clean match


def resolve_leg_times(
    flight_date: date,
    dep_hhmm: str | int | float | None,
    arr_hhmm: str | int | float | None,
    elapsed_min: float | None,
    origin_tz: str,
    dest_tz: str,
) -> ResolvedLegTimes | None:
    """Resolve a flight leg's departure/arrival instants, inferring arrival date.

    Returns None if either time or the elapsed minutes is missing (cancelled
    leg, or an actual-time triplet where the leg never flew) -- caller routes
    those rows accordingly rather than getting a guessed timestamp.
    """
    dep_minutes = parse_hhmm_to_minutes(dep_hhmm)
    arr_minutes = parse_hhmm_to_minutes(arr_hhmm)
    if dep_minutes is None or arr_minutes is None or elapsed_min is None:
        return None

    dep_naive = combine_local(flight_date, dep_minutes)
    dep_local = dep_naive.replace(tzinfo=ZoneInfo(origin_tz))
    dep_utc = dep_local.astimezone(ZoneInfo("UTC"))

    best: ResolvedLegTimes | None = None
    best_abs_mismatch = float("inf")
    for offset in _CANDIDATE_DAY_OFFSETS:
        candidate_date = flight_date + timedelta(days=offset)
        candidate_naive = combine_local(candidate_date, arr_minutes)
        candidate_local = candidate_naive.replace(tzinfo=ZoneInfo(dest_tz))
        candidate_utc = candidate_local.astimezone(ZoneInfo("UTC"))

        implied_elapsed = (candidate_utc - dep_utc).total_seconds() / 60
        mismatch = implied_elapsed - elapsed_min
        if abs(mismatch) < best_abs_mismatch:
            best_abs_mismatch = abs(mismatch)
            best = ResolvedLegTimes(
                dep_local=dep_local,
                dep_utc=dep_utc,
                arr_local=candidate_local,
                arr_utc=candidate_utc,
                arr_date=candidate_local.date(),
                elapsed_mismatch_min=mismatch,
            )

    return best
