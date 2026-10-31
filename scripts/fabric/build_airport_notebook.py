"""Build nb_bronze_airports: reads the clean dim_airport.csv (built from
OurAirports + hubops.airports -- see docs/reference/dim_airport.csv and
contracts/dim_airport.yaml) and writes lh_bronze.dim_airport as Delta.

Usage:
    uv run python scripts/fabric/build_airport_notebook.py --out fabric/nb_bronze_airports.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELLS = [
    """# Phase 4 (reference data): airport coordinates + timezone, needed to
# resolve BTS's local-clock, no-timezone flight times (hubops.bts_times)
# during silver. Source: OurAirports (public-domain aviation reference
# data) -- see contracts/dim_airport.yaml for sourcing notes, including a
# real cross-source inconsistency found building this (Palm Beach Intl's
# IATA code changed to DJT in OurAirports' current data, but BTS still
# files flights under the historical PBI code).

from pyspark.sql.functions import col, current_timestamp, lit
from pyspark.sql.types import BooleanType, DoubleType

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
ABFSS_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"

csv_path = f"{ABFSS_ROOT}/Files/airports/dim_airport.csv"
df = spark.read.option("header", True).csv(csv_path)
print(f"read {df.count():,} rows from {csv_path}")
""",
    """df = df.withColumn("latitude", col("latitude").cast(DoubleType()))
df = df.withColumn("longitude", col("longitude").cast(DoubleType()))
df = df.withColumn("is_aa_hub", col("is_aa_hub").cast(BooleanType()))
df = (
    df.withColumn("_source", lit("ourairports_dim_airport"))
    .withColumn("_loaded_at", current_timestamp())
)
df.printSchema()
""",
    """table_path = f"{ABFSS_ROOT}/Tables/dim_airport"
df.write.format("delta").mode("overwrite").save(table_path)
print(f"wrote {table_path}")
""",
    """check = spark.read.format("delta").load(table_path)
print(f"dim_airport row count: {check.count():,}")
check.filter(col("is_aa_hub") == True).select("iata", "name", "tz").show(truncate=False)
""",
]

notebook = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "cells": [
        {"cell_type": "code", "source": src.splitlines(keepends=True), "execution_count": None, "outputs": [], "metadata": {}}
        for src in CELLS
    ],
    "metadata": {"language_info": {"name": "python"}},
}

platform = {
    "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
    "metadata": {
        "type": "Notebook",
        "displayName": "nb_bronze_airports",
        "description": "Phase 4: land dim_airport (OurAirports + resolved timezones) into lh_bronze.dim_airport.",
    },
    "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    (args.out / ".platform").write_text(json.dumps(platform, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
