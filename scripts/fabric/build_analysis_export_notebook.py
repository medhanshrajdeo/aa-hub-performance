"""Build nb_analysis_export: pulls compact, pre-aggregated CSVs out of
wh_gold for signature analyses #3-#6 (planning/project-plan.md's
"Signature analyses" section) -- aggregation happens in T-SQL where the
data already lives, so only small, analysis-ready tables need to leave
Fabric. The actual statistical work (finding the turn-length cliff,
fitting the weather/volume expected-D0 model, the D0-vs-A14 tradeoff,
the TSA/CBP lag) happens locally in Python afterward, matching the
pattern already proven by analysis/dfw_bank_reconstruction.py and
analysis/dfw_rebank_diffindiff.py.

1. turn_cliff_agg.csv (analysis #3, "split station-caused from
   inbound-caused delay"): fact_aircraft_turn at DFW, filtered to
   on-time-or-early inbound arrivals only (inbound_arr_delay_min <= 0)
   -- isolating turns where any outbound delay is NOT inherited from a
   late inbound, so it's attributable to the station's own turn
   process. Grouped by fleet_family and a 5-minute scheduled-turn-length
   bucket. Bank is NOT one of the cuts here (dim_bank only covers two
   sampled months at DFW, a prototype, not a continuous calendar --
   cutting by it would silently drop most of the window); fleet x turn
   length is the well-supported core of the plan's ask.
2. station_day_d0_weather.csv (analysis #4, "weather-adjusted station
   scorecard"): fact_flight_leg aggregated to station + UTC day for the
   6 AA hub stations (the only ones with weather coverage), joined to
   the day's worst (max) weather severity from
   weather_station_hour_severity.
3. d0_a14_tradeoff_agg.csv (analysis #5, "does chasing D0 buy A14?"):
   fact_flight_leg joined to fact_station_hour (already has
   weather_severity_score per station/UTC-hour -- reuses that join
   rather than re-deriving it), grouped by origin, dest, a dep-delay
   bucket (exactly 0 vs 1-5 min late) and weather severity, for the 6 AA
   hub stations. Restricted to on-time-or-slightly-late departures only
   (0-5 min) -- that's the tradeoff window the plan asks about.
4. tsa_dfw_hourly.csv, dfw_station_hour.csv, cbp_dfw_hourly.csv,
   dfw_intl_arrivals_hourly.csv (analysis #6, "passenger flow"): DFW-only
   exports for TSA checkpoint throughput (station-local hour, TSA's own
   convention), scheduled departures (UTC hour, from fact_station_hour,
   reused as-is), CBP Terminal D wait times (station-local HourRange,
   CBP's own convention -- see contracts/cbp_airport_wait_times.yaml),
   and international arrival counts (UTC hour, origin airport's
   dim_airport.iso_region outside the US as the international proxy).
   Local-hour alignment across these (TSA/CBP are local, fact_station_hour
   is UTC) happens in Python via zoneinfo, not ad hoc T-SQL offset math --
   same rule CLAUDE.md sets for src/hubops/bts_times.py and
   src/hubops/airports.py. The denied-boardings/bag/wheelchair peer
   comparison from the plan's original analysis #6 is dropped -- that's
   DOT Air Travel Consumer Report data, scoped out by ADR 0012.

Must run after nb_gold_build, nb_gold_turns, and nb_silver_weather.

Usage:
    uv run python scripts/fabric/build_analysis_export_notebook.py --out fabric/nb_analysis_export.Notebook
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
    write_diag("analysis_export_connect", {"status": "ok"})
except Exception as e:
    write_diag("analysis_export_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_1_TURN_CLIFF = '''import csv

try:
    cursor.execute("""
        SELECT
            fleet_family,
            CAST(FLOOR(scheduled_turn_min / 5.0) * 5 AS INT) AS turn_bucket_min,
            COUNT(*) AS n_turns,
            SUM(CASE WHEN outbound_dep_delay_min <= 0 THEN 1 ELSE 0 END) AS n_d0
        FROM dbo.fact_aircraft_turn
        WHERE station = 'DFW'
          AND inbound_arr_delay_min <= 0
          AND fleet_family IS NOT NULL
          AND scheduled_turn_min IS NOT NULL
          AND scheduled_turn_min BETWEEN 0 AND 240
        GROUP BY fleet_family, CAST(FLOOR(scheduled_turn_min / 5.0) * 5 AS INT)
        ORDER BY fleet_family, turn_bucket_min
    """)
    rows = cursor.fetchall()

    # Also the headline split: of all DFW turns, what share have an
    # on-time inbound but a delayed outbound (station-originated), vs a
    # delayed inbound (at least partly inbound-caused)?
    cursor.execute("""
        SELECT
            CASE WHEN inbound_arr_delay_min <= 0 THEN 'on_time_inbound' ELSE 'delayed_inbound' END AS inbound_bucket,
            COUNT(*) AS n_turns,
            SUM(CASE WHEN outbound_dep_delay_min <= 0 THEN 1 ELSE 0 END) AS n_outbound_d0,
            AVG(CAST(outbound_dep_delay_min AS FLOAT)) AS mean_outbound_delay_min
        FROM dbo.fact_aircraft_turn
        WHERE station = 'DFW'
        GROUP BY CASE WHEN inbound_arr_delay_min <= 0 THEN 'on_time_inbound' ELSE 'delayed_inbound' END
    """)
    headline_rows = cursor.fetchall()

    WORKSPACE_ID_LOCAL = WORKSPACE_ID
    out_path = f"abfss://{WORKSPACE_ID_LOCAL}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis_export/turn_cliff_agg.csv"
    lines = ["fleet_family,turn_bucket_min,n_turns,n_d0"]
    for r in rows:
        lines.append(f"{r[0]},{r[1]},{r[2]},{r[3]}")
    notebookutils.fs.put(out_path, "\\n".join(lines), overwrite=True)
    print(f"wrote {out_path} ({len(rows)} rows)")

    headline = [
        {"inbound_bucket": r[0], "n_turns": r[1], "n_outbound_d0": r[2], "mean_outbound_delay_min": r[3]}
        for r in headline_rows
    ]
    print("headline station-vs-inbound split:", headline)
    write_diag("analysis_export_turn_cliff", {"agg_rows": len(rows), "headline": headline})
except Exception as e:
    write_diag("analysis_export_turn_cliff_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_2_STATION_DAY = '''try:
    cursor.execute("""
        ;WITH day_flights AS (
            SELECT
                origin_airport_key AS station,
                CAST(FORMAT(sched_dep_utc, 'yyyyMMdd') AS INT) AS date_key,
                COUNT(*) AS scheduled_departures,
                SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS operated_departures,
                SUM(CASE WHEN is_cancelled = 0 AND is_d0 = 1 THEN 1 ELSE 0 END) AS d0_count
            FROM dbo.fact_flight_leg
            WHERE origin_airport_key IN ('DFW','CLT','MIA','ORD','PHX','PHL')
              AND sched_dep_utc IS NOT NULL
            GROUP BY origin_airport_key, CAST(FORMAT(sched_dep_utc, 'yyyyMMdd') AS INT)
        ),
        day_weather AS (
            SELECT
                station,
                CAST(REPLACE(CAST(obs_date AS VARCHAR(10)), '-', '') AS INT) AS date_key,
                MAX(severity_score) AS max_severity_score
            FROM [lh_silver].[dbo].[weather_station_hour_severity]
            GROUP BY station, CAST(REPLACE(CAST(obs_date AS VARCHAR(10)), '-', '') AS INT)
        )
        SELECT f.station, f.date_key, f.scheduled_departures, f.operated_departures, f.d0_count, w.max_severity_score
        FROM day_flights f
        LEFT JOIN day_weather w ON f.station = w.station AND f.date_key = w.date_key
        ORDER BY f.station, f.date_key
    """)
    rows = cursor.fetchall()

    out_path = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis_export/station_day_d0_weather.csv"
    lines = ["station,date_key,scheduled_departures,operated_departures,d0_count,max_severity_score"]
    missing_weather = 0
    for r in rows:
        sev = r[5] if r[5] is not None else ""
        if r[5] is None:
            missing_weather += 1
        lines.append(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{sev}")
    notebookutils.fs.put(out_path, "\\n".join(lines), overwrite=True)
    print(f"wrote {out_path} ({len(rows)} rows, {missing_weather} missing weather)")
    write_diag("analysis_export_station_day", {"rows": len(rows), "missing_weather": missing_weather})
except Exception as e:
    write_diag("analysis_export_station_day_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_3_D0_A14_TRADEOFF = '''try:
    cursor.execute("""
        SELECT
            f.origin_airport_key AS station,
            f.dest_airport_key AS dest,
            CASE WHEN f.dep_delay_min = 0 THEN '0' ELSE '1_5' END AS dep_bucket,
            sh.weather_severity_score,
            COUNT(*) AS n,
            SUM(CASE WHEN f.is_a14 = 1 THEN 1 ELSE 0 END) AS n_a14
        FROM dbo.fact_flight_leg f
        JOIN dbo.fact_station_hour sh
          ON sh.station = f.origin_airport_key
         AND sh.date_key = CAST(FORMAT(f.sched_dep_utc, 'yyyyMMdd') AS INT)
         AND sh.hour24 = DATEPART(HOUR, f.sched_dep_utc)
        WHERE f.is_cancelled = 0
          AND f.dep_delay_min IN (0, 1, 2, 3, 4, 5)
          AND f.origin_airport_key IN ('DFW','CLT','MIA','ORD','PHX','PHL')
        GROUP BY f.origin_airport_key, f.dest_airport_key,
                 CASE WHEN f.dep_delay_min = 0 THEN '0' ELSE '1_5' END,
                 sh.weather_severity_score
    """)
    rows = cursor.fetchall()

    out_path = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis_export/d0_a14_tradeoff_agg.csv"
    lines = ["station,dest,dep_bucket,weather_severity_score,n,n_a14"]
    for r in rows:
        sev = r[3] if r[3] is not None else ""
        lines.append(f"{r[0]},{r[1]},{r[2]},{sev},{r[4]},{r[5]}")
    notebookutils.fs.put(out_path, "\\n".join(lines), overwrite=True)
    print(f"wrote {out_path} ({len(rows)} rows)")
    write_diag("analysis_export_d0_a14", {"rows": len(rows)})
except Exception as e:
    write_diag("analysis_export_d0_a14_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELL_4_PASSENGER_FLOW = '''try:
    def put_csv(path_name, header, rows):
        out_path = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis_export/{path_name}"
        lines = [header] + [",".join("" if v is None else str(v) for v in r) for r in rows]
        notebookutils.fs.put(out_path, "\\n".join(lines), overwrite=True)
        print(f"wrote {out_path} ({len(rows)} rows)")
        return len(rows)

    cursor.execute("""
        SELECT date_key, hour24, SUM(passengers_screened)
        FROM dbo.fact_checkpoint_hour
        WHERE station = 'DFW'
        GROUP BY date_key, hour24
        ORDER BY date_key, hour24
    """)
    tsa_rows = cursor.fetchall()
    n_tsa = put_csv("tsa_dfw_hourly.csv", "date_key,hour24,passengers_screened", tsa_rows)

    cursor.execute("""
        SELECT date_key, hour24, scheduled_departures, actual_departures
        FROM dbo.fact_station_hour
        WHERE station = 'DFW'
        ORDER BY date_key, hour24
    """)
    sh_rows = cursor.fetchall()
    n_sh = put_csv("dfw_station_hour.csv", "date_key,hour24,scheduled_departures,actual_departures", sh_rows)

    cursor.execute("""
        SELECT date_key, hour_range, avg_wait_us, avg_wait_non_us, max_wait_us, max_wait_non_us, total_passengers, flight_count
        FROM dbo.fact_cbp_wait_hour
        WHERE station = 'DFW'
        ORDER BY date_key, hour_range
    """)
    cbp_rows = cursor.fetchall()
    n_cbp = put_csv(
        "cbp_dfw_hourly.csv",
        "date_key,hour_range,avg_wait_us,avg_wait_non_us,max_wait_us,max_wait_non_us,total_passengers,flight_count",
        cbp_rows,
    )

    cursor.execute("""
        SELECT
            CAST(FORMAT(f.sched_arr_utc, 'yyyyMMdd') AS INT) AS date_key,
            DATEPART(HOUR, f.sched_arr_utc) AS hour24,
            COUNT(*) AS n_intl_arrivals
        FROM dbo.fact_flight_leg f
        JOIN dbo.dim_airport a ON a.airport_key = f.origin_airport_key
        WHERE f.dest_airport_key = 'DFW'
          AND f.is_cancelled = 0
          AND f.sched_arr_utc IS NOT NULL
          AND a.iso_region IS NOT NULL AND a.iso_region NOT LIKE 'US-%'
        GROUP BY CAST(FORMAT(f.sched_arr_utc, 'yyyyMMdd') AS INT), DATEPART(HOUR, f.sched_arr_utc)
        ORDER BY date_key, hour24
    """)
    intl_rows = cursor.fetchall()
    n_intl = put_csv("dfw_intl_arrivals_hourly.csv", "date_key,hour24,n_intl_arrivals", intl_rows)

    write_diag("analysis_export_passenger_flow", {
        "tsa_rows": n_tsa, "station_hour_rows": n_sh, "cbp_rows": n_cbp, "intl_arrival_rows": n_intl,
    })
except Exception as e:
    write_diag("analysis_export_passenger_flow_ERROR", {"error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

CELLS = [CELL_0_CONNECT, CELL_1_TURN_CLIFF, CELL_2_STATION_DAY, CELL_3_D0_A14_TRADEOFF, CELL_4_PASSENGER_FLOW]


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
            "displayName": "nb_analysis_export",
            "description": "Pre-aggregated exports for signature analyses #3 (turn-length cliff), #4 (weather-adjusted scorecard), #5 (D0 vs A14 tradeoff), and #6 (passenger flow). Must run after nb_gold_build, nb_gold_turns, nb_silver_weather.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_analysis_export.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
