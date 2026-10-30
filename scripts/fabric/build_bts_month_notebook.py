"""Generate nb_bronze_bts's notebook-content.ipynb for a specific
year/month, baking the values directly into the parameters cell.

Why bake values in rather than use Fabric's runtime parameter injection
(executionData.parameters on the RunNotebook job): that path was tested
against the real workspace and did not reliably override the notebook's
defaults (a run with YEAR=2023/MONTH=1/RESET=true produced output
indistinguishable from the notebook's committed defaults -- no Year=/
Month= partition folders were created, meaning RESET never took the reset
branch). Rather than keep debugging an unverifiable API surface, this
script makes each backfill run self-describing: the exact notebook
definition pushed for a given month *is* the record of what ran for it.
See docs/decisions/0005-bts-backfill-mechanism.md.

Usage:
    uv run python scripts/fabric/build_bts_month_notebook.py \
        --year 2023 --month 1 --reset \
        --out fabric/nb_bronze_bts.Notebook

Then push with scripts/fabric/push_notebook.py against the same --out dir.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PARAMETERS_CELL_TEMPLATE = """# Parameters cell -- baked in per run by
# scripts/fabric/build_bts_month_notebook.py (see docs/decisions/0005).
YEAR = {year}
MONTH = {month}
# RESET=True (re)creates the bronze table partitioned by Year/Month,
# wiping any existing data -- used once, for the first month of a fresh
# backfill. Every other month runs with RESET=False.
RESET = {reset}
"""

DOWNLOAD_CELL = """# Phase 1: ingest one BTS Marketing Carrier On-Time Performance month into lh_bronze.
# Downloads the monthly zip, lands the raw CSV into Files/bts/yyyy/mm/ for
# traceability, then writes it to a bronze Delta table with all columns as
# strings plus _source_file and _loaded_at, per contracts/bts_marketing_ontime.yaml.

import os
import urllib.request
import zipfile
from datetime import datetime, timezone

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
ABFSS_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"

URL = (
    "https://transtats.bts.gov/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_"
    f"Beginning_January_2018_{YEAR}_{MONTH}.zip"
)
LOCAL_ZIP = f"/tmp/bts_{YEAR}_{MONTH}.zip"
LOCAL_DIR = f"/tmp/bts_{YEAR}_{MONTH}"

print(f"downloading {URL}")
urllib.request.urlretrieve(URL, LOCAL_ZIP)
print(f"downloaded {os.path.getsize(LOCAL_ZIP):,} bytes")
"""

EXTRACT_CELL = """os.makedirs(LOCAL_DIR, exist_ok=True)
with zipfile.ZipFile(LOCAL_ZIP) as z:
    z.extractall(LOCAL_DIR)
csv_files = [f for f in os.listdir(LOCAL_DIR) if f.endswith(".csv")]
print("extracted:", csv_files)
local_csv_path = os.path.join(LOCAL_DIR, csv_files[0])
"""

LAND_CELL = """# land the raw CSV into the lakehouse Files area (matches planning/project-plan.md's
# Files/bts/{yyyy}/{mm}/ landing pattern) before any transformation, for traceability
files_dest_dir = f"{ABFSS_ROOT}/Files/bts/{YEAR}/{MONTH:02d}"
csv_dest_path = f"{files_dest_dir}/{csv_files[0]}"
notebookutils.fs.cp(f"file:{local_csv_path}", csv_dest_path)
print(f"landed to {csv_dest_path}")
"""

TRANSFORM_CELL = """from pyspark.sql.functions import col, current_timestamp, lit

df = spark.read.option("header", True).csv(csv_dest_path)

# Delta rejects column names containing ' ,;{}()\\n\\t=' -- the real BTS file
# has one (see contracts/bts_marketing_ontime.yaml): "Operating_Airline "
# has a trailing space. Strip whitespace and swap any other invalid char
# for '_' rather than hardcoding the one known offender, in case BTS
# introduces another in a future month.
import re

INVALID_DELTA_CHARS = re.compile(r'[ ,;{}()\\n\\t=]')


def sanitize_column_name(name: str) -> str:
    return INVALID_DELTA_CHARS.sub("_", name.strip())


rename_map = {c: sanitize_column_name(c) for c in df.columns}
changed = {k: v for k, v in rename_map.items() if k != v}
if changed:
    print(f"sanitizing {len(changed)} column name(s): {changed}")
df = df.select([col(c).alias(rename_map[c]) for c in df.columns])

# bronze contract: every column as string, plus lineage columns
df = df.select([col(c).cast("string").alias(c) for c in df.columns])
df = (
    df.withColumn("_source_file", lit(csv_files[0]))
    .withColumn("_loaded_at", current_timestamp())
)

row_count = df.count()
print(f"rows: {row_count:,}, columns: {len(df.columns)}")
"""

WRITE_CELL = """# Idempotent, partitioned write: a re-run of the same month replaces only
# that month's partition (Delta replaceWhere), never duplicates or wipes
# other months -- see planning/project-plan.md "Production hardening".
from delta.tables import DeltaTable

bronze_table_path = f"{ABFSS_ROOT}/Tables/bts_marketing_ontime"
table_exists = DeltaTable.isDeltaTable(spark, bronze_table_path)

if RESET:
    if table_exists:
        # Delta rejects changing an existing table's partition columns via a
        # plain overwrite -- an unpartitioned table can't become
        # partitioned-by-Year/Month with mode("overwrite") alone. Remove the
        # path first so this is a true fresh create.
        notebookutils.fs.rm(bronze_table_path, True)
        print(f"removed existing table at {bronze_table_path} before reset")
    df.write.format("delta").partitionBy("Year", "Month").mode("overwrite").save(bronze_table_path)
    print("created/reset partitioned bronze table")
elif not table_exists:
    df.write.format("delta").partitionBy("Year", "Month").mode("overwrite").save(bronze_table_path)
    print("created partitioned bronze table (first write)")
else:
    (
        df.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", f"Year = '{YEAR}' AND Month = '{MONTH}'")
        .save(bronze_table_path)
    )
    print(f"replaced partition Year={YEAR} Month={MONTH} in existing table")
"""

VERIFY_CELL = """# verification -- row count for just this month's partition
check = spark.read.format("delta").load(bronze_table_path)
month_rows = check.filter((col("Year") == str(YEAR)) & (col("Month") == str(MONTH))).count()
total = check.count()
print(f"rows loaded for {YEAR}-{MONTH:02d}: {month_rows:,}")
print(f"bronze table total rows (all months so far): {total:,}")
"""


def build_cells(year: int, month: int, reset: bool) -> list[tuple[str, str, list[str]]]:
    parameters_source = PARAMETERS_CELL_TEMPLATE.format(year=year, month=month, reset=reset)
    return [
        ("code", parameters_source, ["parameters"]),
        ("code", DOWNLOAD_CELL, []),
        ("code", EXTRACT_CELL, []),
        ("code", LAND_CELL, []),
        ("code", TRANSFORM_CELL, []),
        ("code", WRITE_CELL, []),
        ("code", VERIFY_CELL, []),
    ]


def build_notebook_json(year: int, month: int, reset: bool) -> dict:
    cells = build_cells(year, month, reset)
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [
            {
                "cell_type": cell_type,
                "source": source.splitlines(keepends=True),
                "execution_count": None,
                "outputs": [],
                "metadata": ({"tags": tags} if tags else {}),
            }
            for cell_type, source, tags in cells
        ],
        "metadata": {"language_info": {"name": "python"}},
    }


def build_platform_json(year: int, month: int) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": "nb_bronze_bts",
            "description": (
                f"Phase 1: ingest BTS Marketing Carrier On-Time Performance "
                f"{year}-{month:02d} into lh_bronze."
            ),
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_bronze_bts.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(args.year, args.month, args.reset), indent=2),
        encoding="utf-8",
    )
    (args.out / ".platform").write_text(
        json.dumps(build_platform_json(args.year, args.month), indent=2), encoding="utf-8"
    )
    print(f"wrote {args.out} for {args.year}-{args.month:02d} (reset={args.reset})")


if __name__ == "__main__":
    main()
