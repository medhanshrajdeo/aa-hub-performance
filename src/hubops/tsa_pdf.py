"""TSA FOIA weekly throughput PDF parser.

TSA's weekly checkpoint-throughput PDFs (tsa.gov/foia/readingroom) are a
pdfplumber "stream" table with carried-forward cells: date, hour, airport
and city/state only appear on the row where they first change, and every
later row for the same hour/airport leaves those columns blank (None once
pdfplumber extracts the table). The table also repeats its title/header rows
on every physical page, and checkpoint *names* drift wildly -- some are
descriptive ("Main Checkpoint"), many are bare terminal/gate numbers ("2",
"10", "4B") that only mean something next to their airport.

The one column that's never blank on a real data row is pax count, and it's
never a valid integer on a title/header/page-number row -- so "does the last
column parse as an int" is what separates real rows from page furniture,
rather than trying to recognize header text.

A third quirk, confirmed against the real October 2026 file: date and hour
carry forward *independently*. When only the hour changes (same date), the
date cell is left blank and only the hour cell is restamped, with the
airport list restarting from the top of the alphabet -- e.g. a row reading
[None, "01:00", "ANC", ...] right after a block of "23:00" rows. Carrying
each of date/hour/iata/airport_name/city/state forward separately (rather
than, say, only restarting carry-forward when *all* of them are populated
together) is what makes this come out right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class TsaCheckpointHour:
    date: date
    hour: str  # "HH:MM" as published (TSA's own hour label, not necessarily every hour)
    iata: str
    airport_name: str
    city: str
    state: str
    checkpoint: str
    pax: int


def parse_tsa_pdf(path: str | Path) -> list[TsaCheckpointHour]:
    """Parse a TSA weekly throughput PDF into one record per (airport, checkpoint, hour)."""
    records: list[TsaCheckpointHour] = []
    carry = _CarryState()

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    record = _parse_row(row, carry)
                    if record is not None:
                        records.append(record)

    return records


class _CarryState:
    def __init__(self) -> None:
        self.date: date | None = None
        self.hour: str | None = None
        self.iata: str | None = None
        self.airport_name: str | None = None
        self.city: str | None = None
        self.state: str | None = None


def _parse_row(row: list[str | None], carry: _CarryState) -> TsaCheckpointHour | None:
    if len(row) < 8:
        return None

    checkpoint_raw, pax_raw = row[6], row[7]
    if checkpoint_raw is None or pax_raw is None:
        return None
    try:
        pax = int(pax_raw)
    except (TypeError, ValueError):
        return None

    # Only mutate carry-forward state once we know this is a real data row --
    # a page-number row like ["8", None, ...] would otherwise crash date
    # parsing on row[0] before we get a chance to reject it above.
    if row[0] is not None:
        carry.date = datetime.strptime(row[0], "%m/%d/%Y").date()
    if row[1] is not None:
        carry.hour = row[1]
    if row[2] is not None:
        carry.iata = _clean_code(row[2])
    if row[3] is not None:
        carry.airport_name = _clean_text(row[3])
    if row[4] is not None:
        carry.city = _clean_text(row[4])
    if row[5] is not None:
        carry.state = _clean_text(row[5])

    if carry.date is None or carry.iata is None:
        return None  # haven't hit a real header-populated row yet

    return TsaCheckpointHour(
        date=carry.date,
        hour=carry.hour,
        iata=carry.iata,
        airport_name=carry.airport_name,
        city=carry.city,
        state=carry.state,
        checkpoint=_clean_text(checkpoint_raw),
        pax=pax,
    )


def _clean_text(value: str) -> str:
    """Collapse embedded newlines/extra whitespace in descriptive text to single spaces."""
    return " ".join(value.split())


def _clean_code(value: str) -> str:
    """Strip all whitespace from an IATA code -- PDF layout sometimes wraps it
    mid-code (observed: "MDW" rendered as "MD\\nW")."""
    return "".join(value.split())
