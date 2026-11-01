"""Build nb_gold_passenger_flow: loads the two small hourly-profile tables
signature analysis #6 (analysis/dfw_passenger_flow.py) produced locally --
docs/analysis/passenger_flow_tsa_departures_by_hour.csv and
docs/analysis/passenger_flow_cbp_intl_arrivals_by_hour.csv -- into wh_gold,
for the Power BI report's Passenger Flow page (ADR 0016's "keep the
UTC/local alignment work in Python" pattern, reused from
nb_gold_station_scorecard rather than wiring the full fact_checkpoint_hour/
fact_cbp_wait_hour tables and hitting the Direct Lake calculated-column
limitation again -- see ADR 0014).

Must run after the CSVs are uploaded to lh_bronze's Files/analysis/ (ADR
0006 pattern). Uses fast_executemany + chunking from the start (ADR 0014's
real pyodbc-performance bug), not the plain executemany that was fine for
the original 35/3/16-row CSVs.

Usage:
    uv run python scripts/fabric/build_gold_passenger_flow_notebook.py --out fabric/nb_gold_passenger_flow.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELL_0_CONNECT = '''import pyodbc, struct, json as _json, traceback as _tb

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
    cursor.fast_executemany = True
    print("connected")
except Exception as e:
    write_diag("gold_passenger_flow_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_LOAD = '''import time

try:
    analysis_root = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis"

    def wait_for_table(name, attempts=10):
        for _ in range(attempts):
            cursor.execute("SELECT COUNT(*) FROM sys.tables WHERE name = ?", name)
            if cursor.fetchone()[0] == 1:
                return
            time.sleep(2)
        raise RuntimeError(f"{name} not visible in sys.tables after {attempts} retries")

    # --- passenger_flow_tsa_departures_by_hour -> fact_tsa_departure_hour_profile ---
    cursor.execute("DROP TABLE IF EXISTS dbo.fact_tsa_departure_hour_profile")
    cursor.execute("""
        CREATE TABLE dbo.fact_tsa_departure_hour_profile (
            local_hour INT NOT NULL,
            mean_passengers_screened FLOAT NOT NULL,
            mean_scheduled_departures FLOAT NOT NULL
        )
    """)
    wait_for_table("fact_tsa_departure_hour_profile")

    tsa_df = spark.read.option("header", True).csv(f"{analysis_root}/passenger_flow_tsa_departures_by_hour.csv")
    tsa_rows = [(int(r["local_hour"]), float(r["mean_passengers_screened"]), float(r["mean_scheduled_departures"])) for r in tsa_df.collect()]
    cursor.executemany(
        "INSERT INTO dbo.fact_tsa_departure_hour_profile (local_hour, mean_passengers_screened, mean_scheduled_departures) VALUES (?, ?, ?)",
        tsa_rows,
    )

    # --- passenger_flow_cbp_intl_arrivals_by_hour -> fact_cbp_hour_profile ---
    cursor.execute("DROP TABLE IF EXISTS dbo.fact_cbp_hour_profile")
    cursor.execute("""
        CREATE TABLE dbo.fact_cbp_hour_profile (
            local_hour INT NOT NULL,
            mean_avg_wait_us_min FLOAT NULL,
            mean_avg_wait_non_us_min FLOAT NULL,
            mean_cbp_flight_count FLOAT NULL,
            mean_cbp_total_passengers FLOAT NULL,
            mean_bts_proxy_intl_arrivals FLOAT NOT NULL
        )
    """)
    wait_for_table("fact_cbp_hour_profile")

    def f_or_none(v):
        return float(v) if v not in (None, "") else None

    cbp_df = spark.read.option("header", True).csv(f"{analysis_root}/passenger_flow_cbp_intl_arrivals_by_hour.csv")
    cbp_rows = [
        (
            int(r["local_hour"]),
            f_or_none(r["mean_avg_wait_us_min"]),
            f_or_none(r["mean_avg_wait_non_us_min"]),
            f_or_none(r["mean_cbp_flight_count"]),
            f_or_none(r["mean_cbp_total_passengers"]),
            float(r["mean_bts_proxy_intl_arrivals"]),
        )
        for r in cbp_df.collect()
    ]
    cursor.executemany(
        "INSERT INTO dbo.fact_cbp_hour_profile (local_hour, mean_avg_wait_us_min, mean_avg_wait_non_us_min, mean_cbp_flight_count, mean_cbp_total_passengers, mean_bts_proxy_intl_arrivals) VALUES (?, ?, ?, ?, ?, ?)",
        cbp_rows,
    )

    cursor.execute("SELECT COUNT(*) FROM dbo.fact_tsa_departure_hour_profile")
    n_tsa = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM dbo.fact_cbp_hour_profile")
    n_cbp = cursor.fetchone()[0]
    print(f"fact_tsa_departure_hour_profile: {n_tsa} rows, fact_cbp_hour_profile: {n_cbp} rows")
    write_diag("gold_passenger_flow_load", {"tsa_rows": n_tsa, "cbp_rows": n_cbp})
except Exception as e:
    write_diag("gold_passenger_flow_load_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_LOAD]


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
            "displayName": "nb_gold_passenger_flow",
            "description": "Loads the two small TSA/CBP hourly-profile tables (signature analysis #6's real local output) into wh_gold for the Power BI report's Passenger Flow page.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(json.dumps(build_notebook_json(), indent=2), encoding="utf-8")
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
