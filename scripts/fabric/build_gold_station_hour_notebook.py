"""Build nb_gold_station_hour: fact_station_hour, "the join point for
weather and passenger flow" per the plan. Grain is station + UTC date +
UTC hour of scheduled departure.

Must run AFTER nb_gold_build (needs dbo.fact_flight_leg) and
nb_silver_weather (needs lh_silver.weather_station_hour_severity).

Deliberate scope cut, documented rather than silently papered over:
TSA throughput (fact_checkpoint_hour) is NOT joined in here. TSA's
`hour` column is the station's LOCAL hour, while this table's hour is
UTC (chosen to match fact_flight_leg's sched_dep_utc and weather's
valid_utc, both naturally UTC, without inventing a second DST-aware
local/UTC conversion on top of the one nb_silver_flights already did for
BTS times). Joining TSA correctly would need that same conversion
applied to TSA's hour -- a real piece of work, not done here to avoid
rushing a second time-zone-rollover bug blind. TSA throughput remains
available in `fact_checkpoint_hour` for any analysis that doesn't need
it joined to this specific table.

Usage:
    uv run python scripts/fabric/build_gold_station_hour_notebook.py --out fabric/nb_gold_station_hour.Notebook
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
    write_diag("gold_station_hour_connect", {"status": "ok"})
except Exception as e:
    write_diag("gold_station_hour_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_DDL_AND_LOAD = '''try:
    cursor.execute("DROP TABLE IF EXISTS dbo.fact_station_hour")
    cursor.execute("""
        CREATE TABLE dbo.fact_station_hour (
            station VARCHAR(3) NOT NULL,
            date_key INT NOT NULL,
            hour24 INT NOT NULL,
            scheduled_departures INT NOT NULL,
            actual_departures INT NOT NULL,
            mean_dep_delay_min FLOAT NULL,
            weather_severity_score INT NULL
        )
    """)

    # date_key and hour24 are BOTH derived from sched_dep_utc's own UTC
    # components here -- NOT from fact_flight_leg's existing date_key
    # column, which is anchored to FlightDate (the SCHEDULED departure's
    # ORIGIN-LOCAL date per BTS convention, see ADR 0010). Mixing that
    # locally-anchored date with a UTC hour would misalign the two by
    # however many hours the origin's UTC offset is -- the same class of
    # date/time bug this project has already been bitten by twice
    # (nb_silver_flights' day-rollover, ADR 0010). Deriving both pieces
    # from the same UTC timestamp keeps this table internally consistent
    # and correctly joinable with weather_station_hour_severity, which is
    # bucketed the same way (UTC date + UTC hour of valid_utc).
    cursor.execute("""
        ;WITH flight_agg AS (
            SELECT
                origin_airport_key AS station,
                CAST(FORMAT(sched_dep_utc, 'yyyyMMdd') AS INT) AS date_key,
                DATEPART(HOUR, sched_dep_utc) AS hour24,
                COUNT(*) AS scheduled_departures,
                SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS actual_departures,
                AVG(CASE WHEN is_cancelled = 0 THEN dep_delay_min END) AS mean_dep_delay_min
            FROM dbo.fact_flight_leg
            WHERE sched_dep_utc IS NOT NULL
            GROUP BY origin_airport_key, CAST(FORMAT(sched_dep_utc, 'yyyyMMdd') AS INT), DATEPART(HOUR, sched_dep_utc)
        )
        INSERT INTO dbo.fact_station_hour (station, date_key, hour24, scheduled_departures, actual_departures, mean_dep_delay_min, weather_severity_score)
        SELECT
            f.station, f.date_key, f.hour24, f.scheduled_departures, f.actual_departures, f.mean_dep_delay_min,
            w.severity_score
        FROM flight_agg f
        LEFT JOIN [lh_silver].[dbo].[weather_station_hour_severity] w
            ON f.station = w.station
           AND f.date_key = CAST(REPLACE(CAST(w.obs_date AS VARCHAR(10)), '-', '') AS INT)
           AND f.hour24 = w.obs_hour
    """)
    cursor.execute("SELECT COUNT(*) FROM dbo.fact_station_hour")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM dbo.fact_station_hour WHERE weather_severity_score IS NOT NULL")
    matched = cursor.fetchone()[0]
    print(f"fact_station_hour: {total:,} rows, {matched:,} with a weather match ({matched/total*100:.1f}%)")
    write_diag("gold_station_hour_load", {"rows": total, "weather_matched": matched, "weather_match_pct": matched / total * 100 if total else None})
except Exception as e:
    write_diag("gold_station_hour_load_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_2_VALIDATE = '''try:
    cursor.execute("""
        SELECT TOP 10 station, date_key, hour24, scheduled_departures, actual_departures, mean_dep_delay_min, weather_severity_score
        FROM dbo.fact_station_hour
        WHERE station = 'DFW' AND weather_severity_score IS NOT NULL
        ORDER BY date_key DESC, hour24 DESC
    """)
    rows = cursor.fetchall()
    sample = [
        {"station": r[0], "date_key": r[1], "hour24": r[2], "scheduled_departures": r[3], "actual_departures": r[4], "mean_dep_delay_min": r[5], "weather_severity_score": r[6]}
        for r in rows
    ]
    print(f"DFW sample: {len(sample)} rows")
    write_diag("gold_station_hour_dfw_sample", {"sample": sample})
except Exception as e:
    write_diag("gold_station_hour_dfw_sample_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_DDL_AND_LOAD, CELL_2_VALIDATE]


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
            "displayName": "nb_gold_station_hour",
            "description": "Phase 4/6: fact_station_hour (station x UTC hour), joining fact_flight_leg with weather severity. Must run after nb_gold_build and nb_silver_weather.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_gold_station_hour.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
