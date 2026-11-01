"""Build nb_gold_turns: fact_aircraft_turn, "the table nobody else builds
from public data" per the plan. Pairs each tail's inbound arrival with
its next departure from the same station -- same rule as the tested
src/hubops/turns.py.pair_turns() (same tail, same station, non-negative
gap, gap under 6 hours), reimplemented as a set-based T-SQL window-
function query (LEAD() per tail partition ordered by actual-or-scheduled
departure) instead of row-by-row Python, since this runs over
dbo.fact_flight_leg's ~27.8M rows in wh_gold.

Must run AFTER nb_gold_build (needs dbo.fact_flight_leg and
dbo.dim_aircraft already populated).

Usage:
    uv run python scripts/fabric/build_gold_turns_notebook.py --out fabric/nb_gold_turns.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELL_0_CONNECT = '''# See nb_gold_build for why pyodbc + AAD token from inside a Fabric
# Spark notebook is the chosen way to run T-SQL here. Every cell writes
# its result (or, on failure, the full traceback) to Files/diagnostics/
# so failures are self-diagnosable via the OneLake DFS API -- the Fabric
# REST job-status API never surfaces cell-level errors.
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
    write_diag("gold_turns_connect", {"status": "ok"})
except Exception as e:
    write_diag("gold_turns_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_DDL = '''cursor.execute("DROP TABLE IF EXISTS dbo.fact_aircraft_turn")
cursor.execute("""
    CREATE TABLE dbo.fact_aircraft_turn (
        turn_key BIGINT NOT NULL,
        tail_number VARCHAR(10) NOT NULL,
        station VARCHAR(3) NOT NULL,
        inbound_arr_sched_utc DATETIME2(6) NULL,
        inbound_arr_actual_utc DATETIME2(6) NULL,
        outbound_dep_sched_utc DATETIME2(6) NULL,
        outbound_dep_actual_utc DATETIME2(6) NULL,
        scheduled_turn_min FLOAT NULL,
        actual_turn_min FLOAT NOT NULL,
        inbound_arr_delay_min FLOAT NOT NULL,
        outbound_dep_delay_min FLOAT NOT NULL,
        minutes_recovered FLOAT NOT NULL,
        fleet_family VARCHAR(50) NULL,
        turn_below_fleet_p10 BIT NULL
    )
""")
print("DDL done")
'''

# Same rule as hubops.turns.pair_turns(): per tail, sort legs by
# actual-departure-or-scheduled-fallback, pair consecutive legs, keep the
# pair only if inbound.dest == outbound.origin, gap >= 0, gap <= 6 hours.
# LEAD() over a tail-partitioned window gives the "next leg" columns
# alongside each row in one pass -- the set-based equivalent of walking
# consecutive pairs in Python, at warehouse scale.
CELL_2_LOAD = '''cursor.execute("""
    WITH ordered_legs AS (
        SELECT
            tail_number,
            origin_airport_key,
            dest_airport_key,
            sched_dep_utc,
            sched_arr_utc,
            actual_dep_utc,
            actual_arr_utc,
            dep_delay_min,
            arr_delay_min,
            COALESCE(actual_dep_utc, sched_dep_utc) AS seq_key
        FROM dbo.fact_flight_leg
        WHERE tail_number IS NOT NULL
    ),
    paired AS (
        SELECT
            tail_number,
            dest_airport_key AS inbound_dest,
            sched_arr_utc AS inbound_arr_sched,
            actual_arr_utc AS inbound_arr_actual,
            COALESCE(arr_delay_min, 0) AS inbound_arr_delay,
            LEAD(origin_airport_key) OVER (PARTITION BY tail_number ORDER BY seq_key) AS outbound_origin,
            LEAD(sched_dep_utc) OVER (PARTITION BY tail_number ORDER BY seq_key) AS outbound_dep_sched,
            LEAD(actual_dep_utc) OVER (PARTITION BY tail_number ORDER BY seq_key) AS outbound_dep_actual,
            LEAD(COALESCE(dep_delay_min, 0)) OVER (PARTITION BY tail_number ORDER BY seq_key) AS outbound_dep_delay
        FROM ordered_legs
    )
    INSERT INTO dbo.fact_aircraft_turn (
        turn_key, tail_number, station, inbound_arr_sched_utc, inbound_arr_actual_utc,
        outbound_dep_sched_utc, outbound_dep_actual_utc, scheduled_turn_min, actual_turn_min,
        inbound_arr_delay_min, outbound_dep_delay_min, minutes_recovered
    )
    SELECT
        ROW_NUMBER() OVER (ORDER BY tail_number, inbound_arr_actual),
        tail_number,
        inbound_dest,
        inbound_arr_sched,
        inbound_arr_actual,
        outbound_dep_sched,
        outbound_dep_actual,
        CASE WHEN inbound_arr_sched IS NOT NULL AND outbound_dep_sched IS NOT NULL
             THEN DATEDIFF(SECOND, inbound_arr_sched, outbound_dep_sched) / 60.0 END,
        DATEDIFF(SECOND, inbound_arr_actual, outbound_dep_actual) / 60.0,
        inbound_arr_delay,
        outbound_dep_delay,
        inbound_arr_delay - outbound_dep_delay
    FROM paired
    WHERE inbound_dest = outbound_origin
      AND inbound_arr_actual IS NOT NULL
      AND outbound_dep_actual IS NOT NULL
      AND DATEDIFF(SECOND, inbound_arr_actual, outbound_dep_actual) >= 0
      AND DATEDIFF(SECOND, inbound_arr_actual, outbound_dep_actual) <= 21600
""")
cursor.execute("SELECT COUNT(*) FROM dbo.fact_aircraft_turn")
turn_count = cursor.fetchone()[0]
print(f"fact_aircraft_turn: {turn_count:,} rows")
write_diag("gold_turns_load", {"rows": turn_count})
'''

# fleet_family + turn_below_fleet_p10 is a nice-to-have on top of the
# core pairing result -- wrapped in try/except so a PERCENTILE_CONT
# syntax or support surprise in Fabric Warehouse doesn't block the rest
# of this notebook (or anything that already succeeded above) from
# counting as done.
CELL_3_FLEET_P10 = '''try:
    cursor.execute("""
        ;WITH flagged AS (
            SELECT
                t.turn_key,
                a.fleet_family,
                PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY t.actual_turn_min)
                    OVER (PARTITION BY a.fleet_family) AS p10_min,
                t.actual_turn_min
            FROM dbo.fact_aircraft_turn t
            JOIN dbo.dim_aircraft a ON t.tail_number = a.tail_number
        )
        UPDATE t
        SET t.fleet_family = f.fleet_family,
            t.turn_below_fleet_p10 = CASE WHEN f.actual_turn_min < f.p10_min THEN 1 ELSE 0 END
        FROM dbo.fact_aircraft_turn t
        JOIN flagged f ON t.turn_key = f.turn_key
    """)
    cursor.execute("SELECT COUNT(*) FROM dbo.fact_aircraft_turn WHERE fleet_family IS NOT NULL")
    flagged_count = cursor.fetchone()[0]
    print(f"fleet_family/turn_below_fleet_p10 set on {flagged_count:,} rows")
    write_diag("gold_turns_fleet_p10", {"status": "ok", "flagged_rows": flagged_count})
except Exception as e:
    print("fleet p10 step FAILED (non-fatal, core pairing already committed):", repr(e))
    write_diag("gold_turns_fleet_p10", {"status": "failed", "error": repr(e)})
'''

CELL_4_VALIDATE = '''# Hand-check: plan's Phase 2 "done when" asked for 20 hand-checked DFW
# turns pairing correctly -- sample 20 real DFW turns with enough detail
# to eyeball-verify the pairing (same tail, same station, sane gap).
cursor.execute("""
    SELECT TOP 20 tail_number, station, inbound_arr_sched_utc, inbound_arr_actual_utc,
           outbound_dep_sched_utc, outbound_dep_actual_utc, actual_turn_min, minutes_recovered
    FROM dbo.fact_aircraft_turn
    WHERE station = 'DFW'
    ORDER BY inbound_arr_actual_utc DESC
""")
rows = cursor.fetchall()
sample = [
    {
        "tail_number": r[0], "station": r[1],
        "inbound_arr_sched_utc": str(r[2]), "inbound_arr_actual_utc": str(r[3]),
        "outbound_dep_sched_utc": str(r[4]), "outbound_dep_actual_utc": str(r[5]),
        "actual_turn_min": r[6], "minutes_recovered": r[7],
    }
    for r in rows
]
cursor.execute("SELECT COUNT(*) FROM dbo.fact_aircraft_turn WHERE station = 'DFW'")
dfw_turn_count = cursor.fetchone()[0]
print(f"DFW turns: {dfw_turn_count:,}")
write_diag("gold_turns_dfw_sample", {"dfw_turn_count": dfw_turn_count, "sample_20": sample})
'''

def _wrap_cell(name: str, body: str) -> str:
    """See build_gold_notebook.py's _wrap_cell -- same reasoning."""
    import textwrap

    indented = textwrap.indent(body.rstrip() + "\n", "    ")
    return (
        f"try:\n{indented}"
        f"except Exception as e:\n"
        f"    write_diag('{name}_ERROR', {{'error': repr(e), 'traceback': _tb.format_exc()}})\n"
        f"    raise\n"
    )


CELL_1_DDL = _wrap_cell("gold_turns_ddl", CELL_1_DDL)
CELL_2_LOAD = _wrap_cell("gold_turns_load_wrapped", CELL_2_LOAD)
CELL_4_VALIDATE = _wrap_cell("gold_turns_dfw_sample_wrapped", CELL_4_VALIDATE)

CELLS = [CELL_0_CONNECT, CELL_1_DDL, CELL_2_LOAD, CELL_3_FLEET_P10, CELL_4_VALIDATE]


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
            "displayName": "nb_gold_turns",
            "description": "Phase 6 (table nobody else builds from public data): pair each tail's inbound arrival with its next departure into fact_aircraft_turn. Must run after nb_gold_build.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_gold_turns.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
