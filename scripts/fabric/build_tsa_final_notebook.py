"""Build nb_bronze_tsa's FINAL notebook definition: reads the pre-combined,
locally-parsed CSV (produced by combining scripts/fabric/download_and_parse_tsa_weeks.py
runs across all weeks in docs/fabric_backfill_tsa_weeks.txt) and writes
lh_bronze.tsa_checkpoint_hour as Delta, partitioned by week_start.

This replaced the original per-week in-Fabric-PDF-parsing approach
(build_tsa_week_notebook.py + run_tsa_backfill.sh) after %pip install was
found to be disabled for batch RunNotebook jobs -- see ADR 0008. Those
per-week scripts are kept in the repo for the record (and because the
per-week pattern is still the right one for sources that don't need an
extra pip package, like nb_bronze_bts), but this is what actually produced
the live nb_bronze_tsa.

Usage:
    uv run python scripts/fabric/build_tsa_final_notebook.py \
        --out fabric/nb_bronze_tsa.Notebook

Then push with scripts/fabric/push_notebook.py against the same --out dir,
poll the updateDefinition operation to Succeeded (it's async -- see ADR
0009), and only then trigger RunNotebook.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELLS = [
    """# Phase 4/7 (messy source, finalized): TSA weekly checkpoint throughput,
# Jan-Aug 2026 (35 weeks, matching the rebank diff-in-diff window -- see
# ADR 0003). Parsed locally with hubops.tsa_pdf (same carry-forward logic
# documented there: carried-forward date/hour/airport cells, numeric
# checkpoint names, independent date/hour carry-forward) rather than in
# Fabric -- see ADR 0008 for why (%pip is disabled for batch jobs, and
# the parsing itself is single-threaded driver work regardless of where
# it runs, so nothing was lost by doing it locally).

from pyspark.sql.functions import col, current_timestamp, lit, to_date
from pyspark.sql.types import IntegerType

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
ABFSS_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"

csv_path = f"{ABFSS_ROOT}/Files/tsa/tsa_checkpoint_hour_all.csv"
df = spark.read.option("header", True).csv(csv_path)
print(f"read {df.count():,} rows from {csv_path}")
""",
    """df = df.withColumn("date", to_date(col("date")))
df = df.withColumn("pax", col("pax").cast(IntegerType()))
df = (
    df.withColumn("_source", lit("tsa_foia_weekly_throughput"))
    .withColumn("_loaded_at", current_timestamp())
)
df.printSchema()
""",
    """table_path = f"{ABFSS_ROOT}/Tables/tsa_checkpoint_hour"
# overwriteSchema=true: this load intentionally replaces the earlier
# per-week prototype table (which had "date" as a plain string) with the
# real typed version -- see ADR 0009 for the DELTA_FAILED_TO_MERGE_FIELDS
# error this fixes.
(
    df.write.format("delta")
    .partitionBy("week_start")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .save(table_path)
)
print(f"wrote {table_path}")
""",
    """check = spark.read.format("delta").load(table_path)
print(f"tsa_checkpoint_hour row count: {check.count():,}")
check.groupBy("week_start").count().orderBy("week_start").show(40)
""",
]


def build_notebook_json() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [
            {
                "cell_type": "code",
                "source": src.splitlines(keepends=True),
                "execution_count": None,
                "outputs": [],
                "metadata": {},
            }
            for src in CELLS
        ],
        "metadata": {"language_info": {"name": "python"}},
    }


def build_platform_json() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": "nb_bronze_tsa",
            "description": "Phase 4/7: land the locally-parsed TSA weekly checkpoint throughput (Jan-Aug 2026, 35 weeks) into lh_bronze.tsa_checkpoint_hour.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_bronze_tsa.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
