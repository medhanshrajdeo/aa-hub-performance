"""Compute real US federal holiday observed dates for a year range, using
the actual federal holiday rules (fixed date + nearest-weekday observance,
or Nth weekday of month), not a hand-typed list. Output feeds dim_date's
is_holiday flag in the Gold warehouse build.

Usage:
    uv run python scripts/fabric/compute_us_holidays.py --start-year 2023 --end-year 2026
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0 ... Sunday=6. n: 1-indexed occurrence."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset + 7 * (n - 1))
    return d


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def observed(fixed_date: date) -> date:
    """Federal fixed-date holidays shift to the nearest weekday if they
    fall on a weekend: Saturday -> observed the preceding Friday,
    Sunday -> observed the following Monday."""
    if fixed_date.weekday() == 5:  # Saturday
        return fixed_date - timedelta(days=1)
    if fixed_date.weekday() == 6:  # Sunday
        return fixed_date + timedelta(days=1)
    return fixed_date


def holidays_for_year(year: int) -> list[tuple[date, str]]:
    return [
        (observed(date(year, 1, 1)), "New Year's Day"),
        (nth_weekday_of_month(year, 1, 0, 3), "Martin Luther King Jr. Day"),
        (nth_weekday_of_month(year, 2, 0, 3), "Washington's Birthday (Presidents Day)"),
        (last_weekday_of_month(year, 5, 0), "Memorial Day"),
        (observed(date(year, 6, 19)), "Juneteenth National Independence Day"),
        (observed(date(year, 7, 4)), "Independence Day"),
        (nth_weekday_of_month(year, 9, 0, 1), "Labor Day"),
        (nth_weekday_of_month(year, 10, 0, 2), "Columbus Day"),
        (observed(date(year, 11, 11)), "Veterans Day"),
        (nth_weekday_of_month(year, 11, 3, 4), "Thanksgiving Day"),
        (observed(date(year, 12, 25)), "Christmas Day"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    args = parser.parse_args()

    rows = []
    for year in range(args.start_year, args.end_year + 1):
        for d, name in holidays_for_year(year):
            rows.append((d, name))
    rows.sort()
    for d, name in rows:
        print(f"{d.isoformat()},{d.strftime('%A')},{name}")


if __name__ == "__main__":
    main()
