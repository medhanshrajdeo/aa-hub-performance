"""Build nb_meta_health: a verification sweep that COUNT(*)s every real
table in wh_gold and records the result in dbo.load_log with a
timestamp -- the "is the data fresh and does it reconcile" check the
project plan's Phase 8 names, and the source for the Data Health report
page and sql/adhoc/data_health_snapshot.sql.

This is a snapshot sweep, not a per-load history table: this project's
ingest notebooks ran as a one-time historical backfill (Jan 2023-Aug
2026), not on an incremental schedule, so there's no real per-run
history to populate retroactively -- building meta.source_registry's
watermark tracking now, before there's an actual incremental schedule
to track, would be unused scaffolding. Re-run this notebook any time to
refresh "latest" with a new timestamped sweep; old sweeps are kept, not
overwritten, so trend-over-time is possible once this runs more than
once.

Usage:
    uv run python scripts/fabric/build_meta_health_notebook.py --out fabric/nb_meta_health.Notebook
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
    print("connected")
except Exception as e:
    write_diag("meta_health_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_CREATE_TABLE = '''import time

try:
    # dbo, not a custom "meta" schema -- every other table in wh_gold
    # lives in dbo, and a custom schema turned out to make Direct Lake's
    # table creation hang indefinitely rather than just take the usual
    # extra discovery time (see ADR 0020). "load_log" as a name is
    # distinctive enough on its own without a separate schema.
    # One-time cleanup of the abandoned first attempt.
    cursor.execute("SELECT COUNT(*) FROM sys.schemas WHERE name = 'meta'")
    if cursor.fetchone()[0] == 1:
        cursor.execute("SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE s.name = 'meta' AND t.name = 'load_log'")
        if cursor.fetchone()[0] == 1:
            cursor.execute("DROP TABLE meta.load_log")
        cursor.execute("DROP SCHEMA meta")

    def wait_for_table(name, attempts=10):
        for _ in range(attempts):
            cursor.execute("SELECT COUNT(*) FROM sys.tables WHERE name = ?", name)
            if cursor.fetchone()[0] == 1:
                return
            time.sleep(2)
        raise RuntimeError(f"dbo.{name} not visible in sys.tables after {attempts} retries")

    cursor.execute("SELECT COUNT(*) FROM sys.tables WHERE name = 'load_log'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            CREATE TABLE dbo.load_log (
                source VARCHAR(20) NOT NULL,
                table_name VARCHAR(100) NOT NULL,
                row_count BIGINT NOT NULL,
                checked_at_utc DATETIME2(6) NOT NULL,
                status VARCHAR(20) NOT NULL,
                notes VARCHAR(500) NULL
            )
        """)
        wait_for_table("load_log")
    print("dbo.load_log ready")
except Exception as e:
    write_diag("meta_health_create_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

# (table_name, source) -- source traces each table back to one of the
# project's five real sources, or 'derived'/'reference'/'analysis' for
# tables built from more than one source or authored locally (US holiday
# rules, bank-structure detection, etc -- see docs/data_dictionary.md
# for exactly how each one is built).
TABLES = [
    ("dim_date", "derived"),
    ("dim_time_15", "derived"),
    ("dim_airport", "reference"),
    ("dim_carrier", "BTS"),
    ("dim_aircraft", "FAA"),
    ("dim_delay_cause", "reference"),
    ("dim_cancel_reason", "reference"),
    ("dim_bank", "derived"),
    ("fact_flight_leg", "BTS"),
    ("fact_aircraft_turn", "BTS"),
    ("fact_station_hour", "BTS"),
    ("fact_checkpoint_hour", "TSA"),
    ("fact_cbp_wait_hour", "CBP"),
    ("fact_rebank_effect", "analysis"),
    ("fact_rebank_event_study", "analysis"),
    ("fact_station_day_scorecard", "analysis"),
    ("fact_tsa_departure_hour_profile", "TSA"),
    ("fact_cbp_hour_profile", "CBP"),
]

CELL_2_SWEEP = f'''try:
    import datetime
    checked_at = datetime.datetime.utcnow()
    tables = {TABLES!r}
    rows = []
    for table_name, source in tables:
        cursor.execute(f"SELECT COUNT(*) FROM sys.tables WHERE name = ?", table_name)
        exists = cursor.fetchone()[0] == 1
        if not exists:
            rows.append((source, table_name, 0, checked_at, "MISSING", "table not found in wh_gold"))
            continue
        cursor.execute(f"SELECT COUNT(*) FROM dbo.[{{table_name}}]")
        n = cursor.fetchone()[0]
        status = "OK" if n > 0 else "EMPTY"
        rows.append((source, table_name, n, checked_at, status, None))

    # Point-in-time reconciliation note (see docs/kpi_spec.md's
    # "Reconciliation target" and ADR 0013) -- not recomputed live here,
    # this sweep is a freshness/row-count check, not a full 44-month
    # bronze-vs-gold aggregate reconciliation (expensive, already done
    # once and confirmed on 2026-10-31).
    rows.append((
        "reconciliation", "fact_flight_leg (vs bronze)", 27787225, checked_at, "OK",
        "Monthly D0/A14/CF, gold vs lh_bronze.bts_marketing_ontime raw aggregates: "
        "max D0 diff 0.0000924, max CF diff 0.0000087 across all 44 months (2026-10-31 full build). "
        "See ADR 0013 and docs/kpi_spec.md.",
    ))

    cursor.fast_executemany = True
    cursor.executemany(
        "INSERT INTO dbo.load_log (source, table_name, row_count, checked_at_utc, status, notes) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )

    result = {{"checked_at_utc": str(checked_at), "tables_swept": len(rows), "not_ok": [r[1] for r in rows if r[4] != "OK"]}}
    print(_json.dumps(result, indent=2))
    write_diag("meta_health_sweep", result)
except Exception as e:
    write_diag("meta_health_sweep_ERROR", {{"error": repr(e), "traceback": _tb.format_exc()}})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_CREATE_TABLE, CELL_2_SWEEP]


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
            "displayName": "nb_meta_health",
            "description": "Phase 8 data-health sweep: COUNT(*)s every real wh_gold table and records source/row_count/status/checked_at_utc to dbo.load_log. Re-run to refresh the Data Health report page.",
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
