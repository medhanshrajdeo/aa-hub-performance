"""Generate nb_bronze_tsa's notebook-content.ipynb for one TSA weekly
throughput PDF, baking the URL and week-start date directly into the
parameters cell -- same reasoning as build_bts_month_notebook.py (ADR
0005): Fabric's runtime parameter injection didn't reliably take effect
when tested, so each week's notebook definition is self-describing
instead of depending on it.

The actual parsing logic (carried-forward date/hour/airport cells,
numeric checkpoint names, independent date/hour carry-forward) is the
same algorithm as src/hubops/tsa_pdf.py, reimplemented inline in the
notebook cell since the Fabric Spark environment doesn't have this
project's package installed. Keep the two in sync if the parser logic
changes.

Usage:
    uv run python scripts/fabric/build_tsa_week_notebook.py \
        --week-start 2026-01-04 \
        --pdf-url "https://www.tsa.gov/sites/default/files/foia-readingroom/tsa-throughput-data-to-january-4-2026-to-january-10-2026.pdf" \
        --reset \
        --out fabric/nb_bronze_tsa.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PARAMETERS_CELL_TEMPLATE = """# Parameters cell -- baked in per run by
# scripts/fabric/build_tsa_week_notebook.py (see docs/decisions/0005 for
# why this is baked in rather than passed via Fabric's runtime parameter API).
WEEK_START = "{week_start}"
PDF_URL = "{pdf_url}"
RESET = {reset}
"""

SETUP_CELL = """%pip install pdfplumber -q
"""

DOWNLOAD_CELL = """# Phase 4 (messy source): one TSA weekly throughput PDF -> lh_bronze.
# Lands the raw PDF to Files/tsa/{week_start}.pdf for traceability, then
# parses it with the same carry-forward logic as src/hubops/tsa_pdf.py.
import urllib.request

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
ABFSS_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"

LOCAL_PDF = f"/tmp/tsa_{WEEK_START}.pdf"
print(f"downloading {PDF_URL}")
# Fabric's outbound urllib request got 403 Forbidden against a URL that
# plain curl fetches fine with no special headers -- TSA's site shows no
# sign of deliberate bot-protection (unlike FAA/DOT's Akamai walls), so
# this reads as a generic WAF rule against the literal default
# "Python-urllib/x.y" user-agent string. Self-identify honestly rather
# than impersonate a browser.
req = urllib.request.Request(
    PDF_URL,
    headers={"User-Agent": "Mozilla/5.0 (compatible; dfw-hub-performance-analytics-research/1.0)"},
)
with urllib.request.urlopen(req, timeout=120) as resp, open(LOCAL_PDF, "wb") as f:
    f.write(resp.read())
import os
print(f"downloaded {os.path.getsize(LOCAL_PDF):,} bytes")

dest = f"{ABFSS_ROOT}/Files/tsa/{WEEK_START}.pdf"
notebookutils.fs.cp(f"file:{LOCAL_PDF}", dest)
print(f"landed to {dest}")
"""

PARSE_CELL = '''# Same algorithm as src/hubops/tsa_pdf.py -- see that module's docstring
# for why "does the last column parse as an int" separates real data rows
# from title/header/page-number furniture, and why date/hour must carry
# forward INDEPENDENTLY (an hour-only restamp leaves the date cell blank).
import re
from datetime import datetime

import pdfplumber


class _CarryState:
    def __init__(self):
        self.date = None
        self.hour = None
        self.iata = None
        self.airport_name = None
        self.city = None
        self.state = None


def _clean_text(value):
    return " ".join(value.split())


def _clean_code(value):
    return "".join(value.split())


def _parse_row(row, carry):
    if len(row) < 8:
        return None
    checkpoint_raw, pax_raw = row[6], row[7]
    if checkpoint_raw is None or pax_raw is None:
        return None
    try:
        pax = int(pax_raw)
    except (TypeError, ValueError):
        return None

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
        return None

    return {
        "date": carry.date.isoformat(),
        "hour": carry.hour,
        "iata": carry.iata,
        "airport_name": carry.airport_name,
        "city": carry.city,
        "state": carry.state,
        "checkpoint": _clean_text(checkpoint_raw),
        "pax": pax,
    }


records = []
carry = _CarryState()
with pdfplumber.open(LOCAL_PDF) as pdf:
    for page in pdf.pages:
        for table in page.extract_tables():
            for row in table:
                rec = _parse_row(row, carry)
                if rec is not None:
                    records.append(rec)

print(f"parsed {len(records):,} rows from {len(pdf.pages)} pages")
'''

WRITE_CELL = """from pyspark.sql import Row
from pyspark.sql.functions import current_timestamp, lit

rows = [Row(**r, week_start=WEEK_START) for r in records]
df = spark.createDataFrame(rows)
df = df.withColumn("_source", lit("tsa_foia_weekly_throughput")).withColumn(
    "_loaded_at", current_timestamp()
)

from delta.tables import DeltaTable

bronze_table_path = f"{ABFSS_ROOT}/Tables/tsa_checkpoint_hour"

if RESET or not DeltaTable.isDeltaTable(spark, bronze_table_path):
    df.write.format("delta").partitionBy("week_start").mode("overwrite").save(bronze_table_path)
    print("created/reset partitioned bronze table")
else:
    (
        df.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", f"week_start = '{WEEK_START}'")
        .save(bronze_table_path)
    )
    print(f"replaced partition week_start={WEEK_START}")
"""

VERIFY_CELL = """check = spark.read.format("delta").load(bronze_table_path)
week_rows = check.filter(check.week_start == WEEK_START).count()
total = check.count()
print(f"rows for week {WEEK_START}: {week_rows:,}")
print(f"bronze table total rows (all weeks so far): {total:,}")
"""


def build_cells(week_start: str, pdf_url: str, reset: bool) -> list[str]:
    params = PARAMETERS_CELL_TEMPLATE.format(week_start=week_start, pdf_url=pdf_url, reset=reset)
    # SETUP_CELL (%pip install) must run FIRST: Fabric notebooks restart the
    # Python kernel after a %pip install, wiping any variables defined in
    # earlier cells. A parameters cell before it would be silently cleared
    # (confirmed against the real workspace: NameError on WEEK_START in the
    # cell right after the pip-install cell -- see docs/decisions/0007).
    return [SETUP_CELL, params, DOWNLOAD_CELL, PARSE_CELL, WRITE_CELL, VERIFY_CELL]


def build_notebook_json(week_start: str, pdf_url: str, reset: bool) -> dict:
    cells = build_cells(week_start, pdf_url, reset)
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [
            {
                "cell_type": "code",
                "source": src.splitlines(keepends=True),
                "execution_count": None,
                "outputs": [],
                "metadata": ({"tags": ["parameters"]} if i == 1 else {}),
            }
            for i, src in enumerate(cells)
        ],
        "metadata": {"language_info": {"name": "python"}},
    }


def build_platform_json(week_start: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": "nb_bronze_tsa",
            "description": f"Phase 4/7: ingest TSA weekly checkpoint throughput for week of {week_start} into lh_bronze.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--pdf-url", required=True)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(args.week_start, args.pdf_url, args.reset), indent=2),
        encoding="utf-8",
    )
    (args.out / ".platform").write_text(
        json.dumps(build_platform_json(args.week_start), indent=2), encoding="utf-8"
    )
    print(f"wrote {args.out} for week {args.week_start} (reset={args.reset})")


if __name__ == "__main__":
    main()
