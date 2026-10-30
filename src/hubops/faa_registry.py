"""FAA Releasable Aircraft Database parser.

Joins MASTER.txt (registration: tail number -> model code, engine code,
owner info) with ACFTREF.txt (model code -> manufacturer, model, seats)
and ENGINE.txt (engine code -> manufacturer, model) into one clean fleet
record per tail number.

PII never enters the returned records: only N-NUMBER, MFR MDL CODE,
ENG MFR MDL, and YEAR MFR are read from MASTER.txt -- the owner identity
and address columns (NAME, STREET, STREET2, CITY, STATE, ZIP CODE,
COUNTY) are never even looked up, not just filtered out afterward. See
planning/project-plan.md's "Security and governance" hardening item
("FAA owner fields dropped at bronze so no personal data lands").

Despite being described as "fixed-width text" in casual references, the
real files are comma-delimited with fixed-width-PADDED values inside each
field (e.g. "100  ,5334..."). Plain csv parsing works; every value just
needs stripping. Also note MASTER.txt's N-NUMBER column omits the leading
"N" (FAA registry convention for a US-only database) -- it has to be
prepended to match BTS's Tail_Number format.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AircraftRecord:
    tail_number: str
    manufacturer: str
    model: str
    seats: int | None
    num_engines: int | None
    engine_manufacturer: str | None
    engine_model: str | None
    year_mfr: int | None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def _parse_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def load_aircraft_reference(acftref_path: Path) -> dict[str, dict]:
    """model code -> {manufacturer, model, seats, num_engines}"""
    out: dict[str, dict] = {}
    for row in _read_csv_rows(acftref_path):
        code = row.get("CODE", "")
        if not code:
            continue
        out[code] = {
            "manufacturer": row.get("MFR", ""),
            "model": row.get("MODEL", ""),
            "seats": _parse_int(row.get("NO-SEATS", "")),
            "num_engines": _parse_int(row.get("NO-ENG", "")),
        }
    return out


def load_engine_reference(engine_path: Path) -> dict[str, dict]:
    """engine code -> {manufacturer, model}"""
    out: dict[str, dict] = {}
    for row in _read_csv_rows(engine_path):
        code = row.get("CODE", "")
        if not code:
            continue
        out[code] = {
            "manufacturer": row.get("MFR", ""),
            "model": row.get("MODEL", ""),
        }
    return out


def parse_fleet(
    master_path: Path,
    acftref_path: Path,
    engine_path: Path,
    tail_numbers: set[str] | None = None,
) -> list[AircraftRecord]:
    """Build one AircraftRecord per registered tail in MASTER.txt.

    MASTER.txt covers every US-registered aircraft (Cessnas, homebuilts,
    gliders -- everything), not just this project's fleet. Pass
    `tail_numbers` (e.g. the set of tails actually seen in BTS data) to
    filter during the scan rather than materializing records for aircraft
    this project will never use.
    """
    aircraft_ref = load_aircraft_reference(acftref_path)
    engine_ref = load_engine_reference(engine_path)

    records = []
    for row in _read_csv_rows(master_path):
        n_number = row.get("N-NUMBER", "")
        if not n_number:
            continue
        tail = f"N{n_number}"
        if tail_numbers is not None and tail not in tail_numbers:
            continue

        model_info = aircraft_ref.get(row.get("MFR MDL CODE", ""), {})
        engine_info = engine_ref.get(row.get("ENG MFR MDL", ""), {})

        records.append(
            AircraftRecord(
                tail_number=tail,
                manufacturer=model_info.get("manufacturer", ""),
                model=model_info.get("model", ""),
                seats=model_info.get("seats"),
                num_engines=model_info.get("num_engines"),
                engine_manufacturer=engine_info.get("manufacturer"),
                engine_model=engine_info.get("model"),
                year_mfr=_parse_int(row.get("YEAR MFR", "")),
            )
        )
    return records
