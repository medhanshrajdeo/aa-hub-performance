"""Tests against real rows pulled from the actual FAA Releasable Aircraft
Database (registry.faa.gov) -- 4 real tail numbers that show up in AA's
real August 2026 BTS flights, including one deliberately missing engine
reference row (N225NN's engine code isn't in the tiny ENGINE fixture) to
prove a bad/missing join doesn't crash, just returns None.
"""

import dataclasses
from pathlib import Path

from hubops.faa_registry import AircraftRecord, parse_fleet

FIXTURES = Path(__file__).parent / "fixtures" / "faa"
MASTER = FIXTURES / "MASTER_sample.txt"
ACFTREF = FIXTURES / "ACFTREF_sample.txt"
ENGINE = FIXTURES / "ENGINE_sample.txt"


def test_parses_all_rows_when_unfiltered():
    records = parse_fleet(MASTER, ACFTREF, ENGINE)
    tails = {r.tail_number for r in records}
    assert tails == {"N225NN", "N320BK", "N322RC", "N750BD"}


def test_tail_numbers_filter_narrows_scan():
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N750BD"})
    assert len(records) == 1
    assert records[0].tail_number == "N750BD"


def test_n_prefix_is_prepended():
    # MASTER.txt stores N-NUMBER without the leading "N" -- confirm it's added.
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N225NN"})
    assert records[0].tail_number == "N225NN"


def test_real_erj170_200lr_join():
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N225NN"})
    r = records[0]
    assert r.manufacturer == "EMBRAER S A"
    assert r.model == "ERJ 170-200 LR"
    assert r.seats == 88
    assert r.num_engines == 2
    assert r.year_mfr == 2016


def test_real_cf34_engine_join():
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N750BD"})
    r = records[0]
    assert r.manufacturer == "EMBRAER"
    assert r.model == "ERJ 170-100 SE"
    assert r.seats == 70
    assert r.engine_manufacturer == "GE"
    assert r.engine_model == "CF34 SERIES"
    assert r.year_mfr == 2005


def test_blank_engine_and_year_fields_are_none_not_crash():
    # N320BK and N322RC have blank ENG MFR MDL and blank YEAR MFR in the
    # real file -- must resolve to None, not 0 or an exception.
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N320BK", "N322RC"})
    assert len(records) == 2
    for r in records:
        assert r.engine_manufacturer is None
        assert r.engine_model is None
        assert r.year_mfr is None
        # the aircraft-model join still succeeds independent of the engine join
        assert r.model == "ERJ 170-200 LR"


def test_unmatched_engine_code_does_not_crash():
    # N225NN's real engine code (30061) is deliberately NOT in the tiny
    # ENGINE fixture -- a missing reference row must degrade to None, not error.
    records = parse_fleet(MASTER, ACFTREF, ENGINE, tail_numbers={"N225NN"})
    r = records[0]
    assert r.engine_manufacturer is None
    assert r.engine_model is None


def test_no_pii_fields_exist_on_the_record_type():
    field_names = {f.name for f in dataclasses.fields(AircraftRecord)}
    pii_markers = {"name", "street", "street2", "city", "state", "zip", "zip_code", "county"}
    assert field_names.isdisjoint(pii_markers)
