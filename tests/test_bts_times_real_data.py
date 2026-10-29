"""Integration test against a real sample of BTS data.

The fixture is 143 real rows pulled from the actual August 2026 BTS
Marketing Carrier On-Time Performance file (AA flights touching DFW),
including 5 real HNL<->DFW redeye legs that cross the date line -- the
exact case resolve_leg_times exists to handle. Regenerate the fixture
with scripts referenced in docs/decisions if BTS changes the file format.

This intentionally hardcodes a small airport->tz map rather than importing
airports.py, since wiring the real FAA/airport-coordinate lookup is phase 4
work -- this test only needs to prove bts_times.py's logic holds up
against real rows, not exercise the full pipeline.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from hubops.bts_times import resolve_leg_times

FIXTURE = Path(__file__).parent / "fixtures" / "bts_aa_dfw_2026_08_sample.csv"

_TZ = {
    "DFW": "America/Chicago",
    "ORD": "America/Chicago",
    "HNL": "Pacific/Honolulu",
}


def _load_rows():
    df = pd.read_csv(FIXTURE, low_memory=False)
    df = df[df["Duplicate"] != "Y"]
    df = df[(df["Cancelled"] == 0) & (df["Diverted"] == 0)]
    return df


@pytest.fixture(scope="module")
def rows():
    return _load_rows()


def test_fixture_has_rows(rows):
    assert len(rows) > 50


def test_elapsed_time_reconciles_for_every_row_with_known_tz(rows):
    checked = 0
    for _, row in rows.iterrows():
        origin_tz = _TZ.get(row["Origin"])
        dest_tz = _TZ.get(row["Dest"])
        if origin_tz is None or dest_tz is None:
            continue
        if pd.isna(row["ActualElapsedTime"]):
            continue

        result = resolve_leg_times(
            flight_date=date.fromisoformat(row["FlightDate"]),
            dep_hhmm=row["DepTime"],
            arr_hhmm=row["ArrTime"],
            elapsed_min=float(row["ActualElapsedTime"]),
            origin_tz=origin_tz,
            dest_tz=dest_tz,
        )
        assert result is not None, f"expected a result for tail {row['Tail_Number']}"
        assert abs(result.elapsed_mismatch_min) < 1, (
            f"tail {row['Tail_Number']} {row['Origin']}->{row['Dest']} "
            f"on {row['FlightDate']}: mismatch {result.elapsed_mismatch_min}"
        )
        checked += 1

    assert checked >= 20


# Exact (tail, date) pairs confirmed via a full scan of the real August 2026
# file to be HNL->DFW overnight departures that cross the date line. Not
# every HNL<->DFW row for these tails is one of these -- the same tails also
# fly other routes/directions/days that don't cross midnight, so pairing by
# tail alone (without the date) would wrongly sweep those in too.
_KNOWN_REDEYES = {
    ("N801AC", date(2026, 8, 1)),
    ("N801AC", date(2026, 8, 2)),
    ("N801AC", date(2026, 8, 3)),
    ("N809AA", date(2026, 8, 4)),
    ("N803AL", date(2026, 8, 5)),
    ("N803AL", date(2026, 8, 6)),
}


def test_hnl_dfw_redeyes_correctly_roll_to_next_calendar_date(rows):
    rows = rows.copy()
    rows["_flight_date"] = rows["FlightDate"].apply(date.fromisoformat)
    mask = rows.apply(
        lambda r: (r["Tail_Number"], r["_flight_date"]) in _KNOWN_REDEYES
        and r["Origin"] == "HNL",
        axis=1,
    )
    redeyes = rows[mask]
    assert len(redeyes) >= 3, "fixture should contain the known real redeye rows"

    for _, row in redeyes.iterrows():
        flight_date = date.fromisoformat(row["FlightDate"])
        result = resolve_leg_times(
            flight_date=flight_date,
            dep_hhmm=row["DepTime"],
            arr_hhmm=row["ArrTime"],
            elapsed_min=float(row["ActualElapsedTime"]),
            origin_tz=_TZ["HNL"],
            dest_tz=_TZ["DFW"],
        )
        assert result is not None
        # A ~9 hour HNL->DFW flight always crosses the date line.
        assert result.arr_date == flight_date + timedelta(days=1)
