"""Tests against a real 4-page slice of TSA's October 18-24, 2026 weekly
throughput PDF (tests/fixtures/tsa_throughput_sample.pdf). The slice covers:
carried-forward date/hour/airport cells, an hour-only restamp where the date
cell is left blank (same date, new hour, airport list restarts from "ANC"),
numeric "checkpoint names" that are really just terminal numbers, a
multi-checkpoint airport split across rows, and a later-in-the-week hour.
"""

from datetime import date
from pathlib import Path

import pytest

from hubops.tsa_pdf import parse_tsa_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "tsa_throughput_sample.pdf"


@pytest.fixture(scope="module")
def records():
    return parse_tsa_pdf(FIXTURE)


def test_parses_a_substantial_number_of_rows(records):
    assert len(records) > 100


def test_descriptive_checkpoint_name_with_carried_forward_header(records):
    # ATL's row has no date/hour/city/state of its own in the raw table --
    # all carried forward from ANC's row above it.
    atl = [r for r in records if r.iata == "ATL" and r.hour == "00:00"]
    assert len(atl) == 2
    by_checkpoint = {r.checkpoint: r.pax for r in atl}
    assert by_checkpoint == {"F Arrival Checkpoint": 7, "Main Checkpoint": 59}
    assert all(r.date == date(2026, 10, 18) and r.city == "Atlanta" and r.state == "GA" for r in atl)


def test_hour_only_restamp_leaves_date_carried_forward(records):
    # Raw row is [None, "01:00", "ANC", ...] -- date is NOT repeated (same
    # day), only the hour changes, and the airport list restarts from the
    # top of the alphabet. Date must still resolve correctly via carry.
    anc_01 = [r for r in records if r.iata == "ANC" and r.hour == "01:00"]
    assert len(anc_01) == 1
    assert anc_01[0].date == date(2026, 10, 18)
    assert anc_01[0].checkpoint == "South Checkpoint"
    assert anc_01[0].pax == 56

    # And the later rows that follow it (ATL again, re-carried from the new
    # ANC row) correctly pick up hour 01:00, not the stale 00:00.
    atl_01 = [r for r in records if r.iata == "ATL" and r.hour == "01:00"]
    assert len(atl_01) == 2
    assert {r.checkpoint: r.pax for r in atl_01} == {"F Arrival Checkpoint": 9, "Main Checkpoint": 51}


def test_iata_code_wrapped_across_pdf_line_is_cleaned(records):
    # Raw cell value is "MD\nW" from PDF layout wrapping -- must become "MDW".
    mdw = [r for r in records if r.iata == "MDW" and r.hour == "00:00"]
    assert len(mdw) == 1
    assert mdw[0].checkpoint == "Main Checkpoint"
    assert mdw[0].pax == 9


def test_numeric_checkpoint_names_are_not_mistaken_for_garbage(records):
    # MIA's checkpoints at hour 00:00 are literally named "2" and "9" --
    # bare terminal numbers, not descriptive text. Both must parse as real
    # records, not get treated as header/page-number noise.
    mia = [r for r in records if r.iata == "MIA" and r.hour == "00:00"]
    checkpoints = {r.checkpoint: r.pax for r in mia}
    assert checkpoints == {"2": 35, "9": 262}
    assert all(r.city == "Miami" and r.state == "FL" for r in mia)


def test_multi_checkpoint_airport_carries_airport_fields_across_rows(records):
    # ORD has several checkpoint rows under one airport header row; every
    # one must carry the same iata/city/state despite blank cells.
    ord_rows = [r for r in records if r.iata == "ORD" and r.hour == "03:00"]
    checkpoints = {r.checkpoint for r in ord_rows}
    assert "10" in checkpoints
    assert all(r.city == "Chicago" and r.state == "IL" for r in ord_rows)


def test_later_week_hour_is_tracked(records):
    hours = {r.hour for r in records}
    assert {"00:00", "01:00", "03:00"} <= hours

    las = [
        r for r in records
        if r.iata == "LAS" and r.checkpoint == "Terminal 3 - E Upper" and r.hour == "03:00"
    ]
    assert len(las) == 1
    assert las[0].pax == 335


def test_garbage_rows_produce_no_records(records):
    # Every record's pax must be a real int -- title rows, blank rows, and
    # page-number rows must never slip through.
    assert all(isinstance(r.pax, int) for r in records)
