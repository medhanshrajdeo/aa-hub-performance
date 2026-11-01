"""Build nb_gold_station_scorecard: fact_station_day_scorecard, the real
output of signature analysis #4 (analysis/dfw_weather_scorecard.py's
weather/volume-adjusted expected-D0 model), loaded into wh_gold.

The plan says "Analyses 2 and 4 write results to gold tables so the
report reads modeled numbers, not notebook screenshots" (planning/
project-plan.md's "Signature analyses" section). Analysis #2 already
does this (fact_rebank_effect, built by nb_gold_build). Analysis #4's
output only lived in docs/analysis/station_day_scorecard.csv until now
-- a real gap against the plan, caught while building Phase 5's hub
scorecard page (which needs this table for its "weather-adjusted D0
gap" tile). This notebook closes it, following the exact same small-
CSV-via-pyodbc pattern nb_gold_build already uses for dim_bank /
fact_rebank_effect / fact_rebank_event_study (ADR 0006 upload, Spark
read, pyodbc executemany insert) -- not folded into nb_gold_build itself
to avoid re-running that already-reconciled 27.8M-row build.

Must run after nb_gold_build (dbo.fact_flight_leg exists, though this
table has no FK relationship to it -- see below) and after
docs/analysis/station_day_scorecard.csv has been uploaded to
lh_bronze's Files/analysis/ (same path/pattern as the other three
analysis-output CSVs already there).

Usage:
    uv run python scripts/fabric/build_gold_station_scorecard_notebook.py --out fabric/nb_gold_station_scorecard.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELL_0_CONNECT = '''# See nb_gold_build for why pyodbc + AAD token from inside a Fabric
# Spark notebook is the chosen way to run T-SQL here, and why every cell
# writes its result/traceback to Files/diagnostics/ (ADR 0013).
import pyodbc, struct, json as _json, traceback as _tb

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"

def write_diag(name, obj):
    path = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/diagnostics/{name}.json"
    notebookutils.fs.put(path, _json.dumps(obj, indent=2, default=str), overwrite=True)
    print(f"wrote {path}")

try:
    server = "4oa7g2g2i24uposxn4zcxdynue-y2u3efqymrpurbs7yr2efofwyy.datawarehouse.fabric.microsoft.com"
    database = "wh_gold"
    token = notebookutils.credentials.getToken("https://database.windows.net/")
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    conn_str = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={database};Encrypt=yes;"
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}, autocommit=True)
    cursor = conn.cursor()
    print("connected")
    write_diag("gold_station_scorecard_connect", {"status": "ok"})
except Exception as e:
    write_diag("gold_station_scorecard_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_CREATE_AND_LOAD = '''import time

try:
    cursor.execute("DROP TABLE IF EXISTS dbo.fact_station_day_scorecard")
    cursor.execute("""
        CREATE TABLE dbo.fact_station_day_scorecard (
            station VARCHAR(3) NOT NULL,
            date_key INT NOT NULL,
            scheduled_departures INT NOT NULL,
            operated_departures INT NOT NULL,
            d0_count INT NOT NULL,
            max_severity_score INT NULL,
            d0_rate FLOAT NOT NULL,
            expected_d0_rate FLOAT NOT NULL,
            d0_vs_expected_pp FLOAT NOT NULL
        )
    """)

    # Fabric Warehouse DDL isn't always immediately visible to a
    # same-session DML statement right after (a real quirk hit here,
    # not hypothetical -- the first run of this notebook got "Invalid
    # object name" on the INSERT seconds after a successful CREATE
    # TABLE). Poll sys.tables instead of assuming immediate visibility.
    for attempt in range(10):
        cursor.execute("SELECT COUNT(*) FROM sys.tables WHERE name = 'fact_station_day_scorecard'")
        if cursor.fetchone()[0] == 1:
            break
        time.sleep(2)
    else:
        raise RuntimeError("fact_station_day_scorecard not visible in sys.tables after 10 retries")

    analysis_root = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis"
    scorecard_df = spark.read.option("header", True).csv(f"{analysis_root}/station_day_scorecard.csv")
    rows = [
        (
            r["station"],
            int(r["date"].replace("-", "")),
            int(r["scheduled_departures"]),
            int(r["operated_departures"]),
            int(r["d0_count"]),
            int(float(r["max_severity_score"])) if r["max_severity_score"] not in (None, "") else None,
            float(r["d0_rate"]),
            float(r["expected_d0_rate"]),
            float(r["d0_vs_expected_pp"]),
        )
        for r in scorecard_df.collect()
    ]
    # Plain pyodbc executemany sends one round-trip per row -- fine for
    # the 35/3/16-row CSVs nb_gold_build already loads this way, but a
    # real bottleneck at 8,028 rows (observed stalling for minutes
    # mid-insert on the first attempt). fast_executemany batches the
    # parameters into one call; insert in chunks too so a single bad
    # chunk fails fast and visibly instead of sitting mid-batch.
    cursor.fast_executemany = True
    insert_sql = "INSERT INTO dbo.fact_station_day_scorecard (station, date_key, scheduled_departures, operated_departures, d0_count, max_severity_score, d0_rate, expected_d0_rate, d0_vs_expected_pp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    CHUNK = 1000
    for start in range(0, len(rows), CHUNK):
        cursor.executemany(insert_sql, rows[start:start + CHUNK])
    cursor.execute("SELECT COUNT(*) FROM dbo.fact_station_day_scorecard")
    n = cursor.fetchone()[0]
    print(f"fact_station_day_scorecard: {n:,} rows")
    write_diag("gold_station_scorecard_load", {"rows": n})
except Exception as e:
    write_diag("gold_station_scorecard_load_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_CREATE_AND_LOAD]


def build_notebook_json() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [
            {"cell_type": "code", "source": src.splitlines(keepends=True), "execution_count": None, "outputs": [], "metadata": {}}
            for src in CELLS
        ],
        "metadata": {"language_info": {"name": "python"}},
    }


def build_platform_json() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": "nb_gold_station_scorecard",
            "description": "Loads fact_station_day_scorecard from analysis #4's real output (docs/analysis/station_day_scorecard.csv) -- closes the plan's 'analyses 2 and 4 write to gold' gap for analysis #4. Must run after nb_gold_build.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_gold_station_scorecard.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
