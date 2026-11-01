"""Build nb_validate_semantic_model: Phase 5's "done when" check --
D0/A14/CF from sm_hubops (DAX, already queried via powerbi-modeling-mcp)
must match the same metrics computed directly in T-SQL against wh_gold
for three test slices, to the decimal.

Throwaway validation notebook, not part of the regular build pipeline.

Usage:
    uv run python scripts/fabric/build_semantic_model_validation_notebook.py --out fabric/nb_validate_semantic_model.Notebook
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
    write_diag("validate_sm_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_SLICES = '''try:
    def kpi_for(where_clause):
        cursor.execute(f"""
            SELECT
                COUNT(*) AS flights,
                SUM(CASE WHEN is_cancelled = 0 AND dep_delay_min <= 0 THEN 1 ELSE 0 END) * 1.0
                    / SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS d0,
                SUM(CASE WHEN is_cancelled = 0 AND arr_delay_min <= 14 THEN 1 ELSE 0 END) * 1.0
                    / SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS a14,
                1.0 - SUM(CASE WHEN is_cancelled = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS cf
            FROM dbo.fact_flight_leg
            WHERE {where_clause}
        """)
        r = cursor.fetchone()
        return {"flights": r[0], "d0": float(r[1]), "a14": float(r[2]), "cf": float(r[3])}

    slices = {
        "overall": kpi_for("1=1"),
        "dfw_aug_2026": kpi_for("origin_airport_key = 'DFW' AND sched_dep_utc >= '2026-08-01' AND sched_dep_utc < '2026-09-01'"),
        "aa_2024": kpi_for("marketing_carrier_key = 'AA' AND sched_dep_utc >= '2024-01-01' AND sched_dep_utc < '2025-01-01'"),
    }
    print(_json.dumps(slices, indent=2))
    write_diag("validate_sm_slices", slices)
except Exception as e:
    write_diag("validate_sm_slices_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_2_SCORECARD_CHECK = '''try:
    cursor.execute("SELECT COUNT(*) FROM sys.tables WHERE name = 'fact_station_day_scorecard'")
    exists = cursor.fetchone()[0]
    result = {"table_exists": bool(exists)}
    if exists:
        cursor.execute("SELECT COUNT(*) FROM dbo.fact_station_day_scorecard")
        result["row_count"] = cursor.fetchone()[0]
    print(_json.dumps(result, indent=2))
    write_diag("validate_sm_scorecard_check", result)
except Exception as e:
    write_diag("validate_sm_scorecard_check_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_SLICES, CELL_2_SCORECARD_CHECK]


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
            "displayName": "nb_validate_semantic_model",
            "description": "Throwaway: computes D0/A14/CF via T-SQL for 3 test slices, to compare against sm_hubops DAX for Phase 5's done-when check.",
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
