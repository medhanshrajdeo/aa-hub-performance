"""Build nb_gold_build: creates the star schema in wh_gold (T-SQL DDL)
and loads it from lh_bronze/lh_silver. Runs entirely via pyodbc + an AAD
access token from inside a Fabric Spark notebook (ODBC Driver 18 for SQL
Server and notebookutils.credentials.getToken are both available in
Fabric's default runtime -- confirmed via a throwaway diagnostic before
committing to this approach).

Architecture decision: Fabric Warehouses in the same workspace as a
Lakehouse support cross-database T-SQL queries (three-part names like
[lh_silver].[dbo].[fact_flight_leg]), confirmed via another throwaway
diagnostic (SELECT COUNT(*) cross-database returned 27,787,225 rows --
matching the full silver run). This means the big loads (fact_flight_leg,
fact_checkpoint_hour, fact_cbp_wait_hour, dim_airport, dim_aircraft) are
pure server-side INSERT INTO ... SELECT ... FROM, no data moves through
Python. The small dims (dim_date, dim_time_15, dim_carrier,
dim_delay_cause, dim_cancel_reason) and the locally-computed analysis
outputs (dim_bank, fact_rebank_effect, fact_rebank_event_study) are
generated/read in Python and inserted via pyodbc.executemany -- simpler
and more robust than relying on T-SQL recursive CTEs I can't test
interactively before running for real.

DOT Air Travel Consumer Report and BTS T-100 are out of scope (ADR 0012)
-- fact_carrier_month_customer and fact_route_month are dropped from this
schema accordingly. Phase 3's "done when" (reconciliation) is checked
against BTS's own raw aggregates, not DOT -- see CELL_RECONCILE.

Every cell writes its result to Files/diagnostics/<name>.json in
lh_bronze instead of relying on printed cell output, so results can be
read back via the OneLake DFS API without needing the Fabric portal.

Usage:
    uv run python scripts/fabric/build_gold_notebook.py --out fabric/nb_gold_build.Notebook

Then push/run with scripts/fabric/push_notebook.py.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"

# ---------------------------------------------------------------------------
# Real US federal holiday dates, 2023-2026 -- computed the same way as
# scripts/fabric/compute_us_holidays.py (Nth-weekday-of-month / fixed-date-
# with-nearest-weekday-observance rules), inlined here so the notebook
# doesn't need that script available at runtime.
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(fixed: date) -> date:
    if fixed.weekday() == 5:
        return fixed - timedelta(days=1)
    if fixed.weekday() == 6:
        return fixed + timedelta(days=1)
    return fixed


def _us_federal_holidays(start_year: int, end_year: int) -> list[date]:
    out = []
    for year in range(start_year, end_year + 1):
        out += [
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _last_weekday(year, 5, 0),
            _observed(date(year, 6, 19)),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 10, 0, 2),
            _observed(date(year, 11, 11)),
            _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25)),
        ]
    return sorted(out)


HOLIDAYS = _us_federal_holidays(2023, 2026)
DIM_DATE_START = date(2023, 1, 1)
DIM_DATE_END = date(2026, 12, 31)
DFW_REBANK_START = date(2026, 4, 6)  # ADR 0003: week 15, detected from the data

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
DAYPARTS = (
    ["Overnight"] * 5 + ["Early Morning"] * 2 + ["Morning"] * 5
    + ["Midday"] * 2 + ["Afternoon"] * 3 + ["Evening"] * 3 + ["Night"] * 4
)
assert len(DAYPARTS) == 24

REAL_CARRIERS = [
    # (code, name, is_mainline, is_aa_family) -- is_aa_family is TRUE only
    # for AA itself and its wholly-owned regional subsidiaries that fly
    # exclusively as American Eagle (Envoy/PSA/Piedmont). Independent
    # regionals that contract with multiple majors (SkyWest, Mesa,
    # Republic, Air Wisconsin, GoJet, CommutAir, Endeavor, Horizon) are
    # NOT flagged AA-family here even though some of their flights do fly
    # under AA's brand -- that's determined per-leg by
    # fact_flight_leg.marketing_carrier_key = 'AA', not by a static
    # property of the operating carrier. All 21 codes below are the real,
    # exact set observed in lh_silver.fact_flight_leg's
    # Marketing_Airline_Network / Operating_Airline columns (confirmed via
    # a throwaway diagnostic, not guessed).
    ("AA", "American Airlines", True, True),
    ("MQ", "Envoy Air", False, True),
    ("OH", "PSA Airlines", False, True),
    ("PT", "Piedmont Airlines", False, True),
    ("DL", "Delta Air Lines", True, False),
    ("9E", "Endeavor Air", False, False),
    ("UA", "United Airlines", True, False),
    ("C5", "CommutAir", False, False),
    ("G7", "GoJet Airlines", False, False),
    ("WN", "Southwest Airlines", True, False),
    ("AS", "Alaska Airlines", True, False),
    ("QX", "Horizon Air", False, False),
    ("B6", "JetBlue Airways", True, False),
    ("NK", "Spirit Airlines", True, False),
    ("F9", "Frontier Airlines", True, False),
    ("G4", "Allegiant Air", True, False),
    ("HA", "Hawaiian Airlines", True, False),
    ("OO", "SkyWest Airlines", False, False),
    ("YV", "Mesa Airlines", False, False),
    ("YX", "Republic Airways", False, False),
    ("ZW", "Air Wisconsin", False, False),
]

# BTS's own delay-cause and cancellation-reason codes (real, documented by
# BTS -- see docs/kpi_spec.md). Only CarrierDelay / code A are
# "controllable" per the plan's Controllable CF / Controllable delay
# share definitions.
DELAY_CAUSES = [
    ("CarrierDelay", "Air Carrier Delay", True),
    ("WeatherDelay", "Weather Delay", False),
    ("NASDelay", "National Aviation System Delay", False),
    ("SecurityDelay", "Security Delay", False),
    ("LateAircraftDelay", "Late Aircraft Delay", False),
]
CANCEL_REASONS = [
    ("A", "Carrier", True),
    ("B", "Weather", False),
    ("C", "National Air System", False),
    ("D", "Security", False),
]


def _py_literal(v) -> str:
    """Render a Python value as a T-SQL literal for an inline VALUES list."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    escaped = str(v).replace("'", "''")
    return f"'{escaped}'"


def _values_rows(rows: list[tuple]) -> str:
    return ",\n".join("(" + ", ".join(_py_literal(v) for v in row) + ")" for row in rows)


# ---------------------------------------------------------------------------
# Cell source
# ---------------------------------------------------------------------------

CELL_0_CONNECT = '''# Phase 3 gold: star schema in wh_gold via T-SQL, executed from this
# Spark notebook via pyodbc + an AAD access token (confirmed available in
# Fabric's default runtime -- ODBC Driver 18 for SQL Server,
# notebookutils.credentials.getToken). Cross-database T-SQL queries let
# the Warehouse read Lakehouse Delta tables directly
# ([lh_silver].[dbo].[table]) -- confirmed working (COUNT(*) across
# lh_silver.fact_flight_leg returned 27,787,225, matching the full silver
# run), so the big loads need no Spark data movement at all.
#
# Every cell writes its result (or, on failure, the full traceback) to
# Files/diagnostics/<name>.json instead of relying on printed cell
# output -- the Fabric REST job-status API never surfaces cell-level
# errors (confirmed repeatedly this project), and this lets failures be
# self-diagnosed via the OneLake DFS API with no portal access needed.
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
    write_diag("gold_connect", {"status": "ok"})
except Exception as e:
    write_diag("gold_connect", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

DDL_STATEMENTS = [
    "DROP TABLE IF EXISTS dbo.dim_date",
    """CREATE TABLE dbo.dim_date (
        date_key INT NOT NULL,
        full_date DATE NOT NULL,
        [year] INT NOT NULL,
        [month] INT NOT NULL,
        [day] INT NOT NULL,
        day_of_week INT NOT NULL,
        day_name VARCHAR(10) NOT NULL,
        month_name VARCHAR(10) NOT NULL,
        quarter INT NOT NULL,
        is_weekend BIT NOT NULL,
        is_holiday BIT NOT NULL,
        schedule_season VARCHAR(10) NOT NULL,
        is_post_dfw_rebank BIT NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_time_15",
    """CREATE TABLE dbo.dim_time_15 (
        time_key INT NOT NULL,
        hour24 INT NOT NULL,
        minute15 INT NOT NULL,
        hhmm VARCHAR(5) NOT NULL,
        daypart VARCHAR(20) NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_airport",
    """CREATE TABLE dbo.dim_airport (
        airport_key VARCHAR(3) NOT NULL,
        airport_name VARCHAR(100) NULL,
        municipality VARCHAR(100) NULL,
        iso_region VARCHAR(10) NULL,
        latitude FLOAT NULL,
        longitude FLOAT NULL,
        tz VARCHAR(50) NULL,
        is_aa_hub BIT NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_carrier",
    """CREATE TABLE dbo.dim_carrier (
        carrier_key VARCHAR(2) NOT NULL,
        carrier_name VARCHAR(100) NOT NULL,
        is_mainline BIT NOT NULL,
        is_aa_family BIT NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_aircraft",
    """CREATE TABLE dbo.dim_aircraft (
        tail_number VARCHAR(10) NOT NULL,
        manufacturer VARCHAR(50) NULL,
        model VARCHAR(50) NULL,
        fleet_family VARCHAR(50) NULL,
        seats INT NULL,
        num_engines INT NULL,
        year_mfr INT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_delay_cause",
    """CREATE TABLE dbo.dim_delay_cause (
        cause_code VARCHAR(20) NOT NULL,
        label VARCHAR(50) NOT NULL,
        is_controllable BIT NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_cancel_reason",
    """CREATE TABLE dbo.dim_cancel_reason (
        reason_code VARCHAR(2) NOT NULL,
        label VARCHAR(50) NOT NULL,
        is_controllable BIT NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.dim_bank",
    """CREATE TABLE dbo.dim_bank (
        bank_key INT NOT NULL,
        station VARCHAR(3) NOT NULL,
        period VARCHAR(30) NOT NULL,
        direction VARCHAR(15) NOT NULL,
        bank_number INT NOT NULL,
        peak_time VARCHAR(5) NULL,
        peak_avg_flights FLOAT NULL,
        window_start VARCHAR(5) NOT NULL,
        window_end VARCHAR(5) NOT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.fact_flight_leg",
    """CREATE TABLE dbo.fact_flight_leg (
        date_key INT NOT NULL,
        origin_airport_key VARCHAR(3) NOT NULL,
        dest_airport_key VARCHAR(3) NOT NULL,
        marketing_carrier_key VARCHAR(2) NOT NULL,
        operating_carrier_key VARCHAR(2) NOT NULL,
        flight_number VARCHAR(10) NULL,
        tail_number VARCHAR(10) NULL,
        sched_dep_utc DATETIME2(6) NULL,
        sched_arr_utc DATETIME2(6) NULL,
        actual_dep_utc DATETIME2(6) NULL,
        actual_arr_utc DATETIME2(6) NULL,
        dep_delay_min FLOAT NULL,
        arr_delay_min FLOAT NULL,
        taxi_out_min FLOAT NULL,
        taxi_in_min FLOAT NULL,
        crs_elapsed_min FLOAT NULL,
        actual_elapsed_min FLOAT NULL,
        carrier_delay_min FLOAT NULL,
        weather_delay_min FLOAT NULL,
        nas_delay_min FLOAT NULL,
        security_delay_min FLOAT NULL,
        late_aircraft_delay_min FLOAT NULL,
        is_cancelled BIT NOT NULL,
        is_diverted BIT NOT NULL,
        is_d0 BIT NULL,
        is_a14 BIT NULL,
        is_b0 BIT NULL,
        is_controllable_cancel BIT NOT NULL,
        cancellation_code VARCHAR(2) NULL,
        distance_mi FLOAT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.fact_checkpoint_hour",
    """CREATE TABLE dbo.fact_checkpoint_hour (
        station VARCHAR(3) NOT NULL,
        [checkpoint] VARCHAR(50) NULL,
        date_key INT NOT NULL,
        hour24 INT NOT NULL,
        week_start DATE NULL,
        passengers_screened INT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.fact_cbp_wait_hour",
    """CREATE TABLE dbo.fact_cbp_wait_hour (
        station VARCHAR(10) NOT NULL,
        terminal VARCHAR(50) NULL,
        date_key INT NOT NULL,
        hour_range VARCHAR(20) NULL,
        avg_wait_us FLOAT NULL,
        max_wait_us FLOAT NULL,
        avg_wait_non_us FLOAT NULL,
        max_wait_non_us FLOAT NULL,
        booths_open INT NULL,
        flight_count INT NULL,
        total_passengers INT NULL
    )""",
    "DROP TABLE IF EXISTS dbo.fact_rebank_effect",
    """CREATE TABLE dbo.fact_rebank_effect (
        hub VARCHAR(3) NOT NULL,
        metric VARCHAR(20) NOT NULL,
        model VARCHAR(200) NOT NULL,
        estimate FLOAT NOT NULL,
        se FLOAT NULL,
        ci_low FLOAT NULL,
        ci_high FLOAT NULL,
        p_value FLOAT NULL,
        n BIGINT NULL,
        controls VARCHAR(500) NULL
    )""",
    "DROP TABLE IF EXISTS dbo.fact_rebank_event_study",
    """CREATE TABLE dbo.fact_rebank_event_study (
        week_relative INT NOT NULL,
        coef FLOAT NOT NULL,
        ci_low FLOAT NULL,
        ci_high FLOAT NULL
    )""",
]

CELL_1_DDL = "ddl_statements = " + repr(DDL_STATEMENTS) + '''
ddl_errors = []
ran = 0
for i, stmt in enumerate(ddl_statements):
    try:
        cursor.execute(stmt)
        ran += 1
    except Exception as e:
        ddl_errors.append({"index": i, "statement": stmt, "error": repr(e), "traceback": _tb.format_exc()})
print(f"ran {ran}/{len(ddl_statements)} DDL statements, {len(ddl_errors)} errors")
write_diag("gold_ddl", {"statements_run": ran, "total": len(ddl_statements), "errors": ddl_errors})
if ddl_errors:
    raise RuntimeError(f"{len(ddl_errors)} DDL statement(s) failed -- see Files/diagnostics/gold_ddl.json")
'''


def _build_dim_date_rows() -> list[tuple]:
    rows = []
    d = DIM_DATE_START
    holiday_set = set(HOLIDAYS)
    while d <= DIM_DATE_END:
        dow0 = d.weekday()  # Monday=0
        rows.append((
            int(d.strftime("%Y%m%d")),
            d.isoformat(),
            d.year, d.month, d.day,
            dow0 + 1,
            DAY_NAMES[dow0],
            MONTH_NAMES[d.month - 1],
            (d.month - 1) // 3 + 1,
            dow0 >= 5,
            d in holiday_set,
            "summer" if 4 <= d.month <= 10 else "winter",
            d >= DFW_REBANK_START,
        ))
        d += timedelta(days=1)
    return rows


CELL_2_LOAD_DIM_DATE = f'''# dim_date: generated in Python (not a T-SQL recursive CTE -- simpler and
# avoids any uncertainty about Fabric Warehouse's recursive-CTE support)
# for {DIM_DATE_START.isoformat()}..{DIM_DATE_END.isoformat()}, covering lh_silver.fact_flight_leg's
# real FlightDate range (2023-01-01 to 2026-08-31) with headroom. Holidays
# are the real US federal holiday observed dates (scripts/fabric/
# compute_us_holidays.py's logic, inlined) -- Nth-weekday-of-month or
# fixed-date-with-nearest-weekday-observance rules, not a hand-typed list.
# is_post_dfw_rebank uses week 15 (April 6-12, 2026), the sustained-jump
# week detected from the data per ADR 0003 -- not the April 2026 the
# Cranky Flier article names loosely.
dim_date_rows = {_build_dim_date_rows()!r}
cursor.executemany(
    "INSERT INTO dbo.dim_date (date_key, full_date, [year], [month], [day], day_of_week, day_name, month_name, quarter, is_weekend, is_holiday, schedule_season, is_post_dfw_rebank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    dim_date_rows,
)
print(f"inserted {{len(dim_date_rows):,}} dim_date rows")
write_diag("gold_load_dim_date", {{"rows": len(dim_date_rows)}})
'''


def _build_dim_time_rows() -> list[tuple]:
    rows = []
    for n in range(96):
        hour24, minute15 = divmod(n, 4)
        minute = minute15 * 15
        rows.append((n, hour24, minute, f"{hour24:02d}:{minute:02d}", DAYPARTS[hour24]))
    return rows


CELL_3_LOAD_DIM_TIME = f'''# dim_time_15: 96 rows (24h x 4 15-min slots), generated in Python.
dim_time_rows = {_build_dim_time_rows()!r}
cursor.executemany(
    "INSERT INTO dbo.dim_time_15 (time_key, hour24, minute15, hhmm, daypart) VALUES (?, ?, ?, ?, ?)",
    dim_time_rows,
)
print(f"inserted {{len(dim_time_rows)}} dim_time_15 rows")
write_diag("gold_load_dim_time", {{"rows": len(dim_time_rows)}})
'''

CELL_4_LOAD_DIM_STATIC = f'''# dim_delay_cause, dim_cancel_reason, dim_carrier: real BTS-defined codes
# and real carrier designators, not fabricated. dim_carrier's 21 codes are
# the EXACT set observed in lh_silver.fact_flight_leg (confirmed via a
# throwaway diagnostic before writing this) -- see build_gold_notebook.py's
# REAL_CARRIERS comment for the is_aa_family reasoning.
cursor.executemany(
    "INSERT INTO dbo.dim_delay_cause (cause_code, label, is_controllable) VALUES (?, ?, ?)",
    {DELAY_CAUSES!r},
)
cursor.executemany(
    "INSERT INTO dbo.dim_cancel_reason (reason_code, label, is_controllable) VALUES (?, ?, ?)",
    {CANCEL_REASONS!r},
)
cursor.executemany(
    "INSERT INTO dbo.dim_carrier (carrier_key, carrier_name, is_mainline, is_aa_family) VALUES (?, ?, ?, ?)",
    {REAL_CARRIERS!r},
)
print("inserted dim_delay_cause, dim_cancel_reason, dim_carrier")
write_diag("gold_load_dim_static", {{"delay_causes": {len(DELAY_CAUSES)}, "cancel_reasons": {len(CANCEL_REASONS)}, "carriers": {len(REAL_CARRIERS)}}})
'''

CELL_5_LOAD_DIM_CROSSDB = '''# dim_airport, dim_aircraft: cross-database INSERT...SELECT straight from
# the Lakehouse Delta tables (no Spark data movement needed).
cursor.execute("""
    INSERT INTO dbo.dim_airport (airport_key, airport_name, municipality, iso_region, latitude, longitude, tz, is_aa_hub)
    SELECT iata, name, municipality, iso_region, latitude, longitude, tz, is_aa_hub
    FROM [lh_bronze].[dbo].[dim_airport]
""")
cursor.execute("""
    INSERT INTO dbo.dim_aircraft (tail_number, manufacturer, model, fleet_family, seats, num_engines, year_mfr)
    SELECT tail_number, manufacturer, model, model, seats, num_engines, year_mfr
    FROM [lh_bronze].[dbo].[dim_aircraft]
""")
cursor.execute("SELECT COUNT(*) FROM dbo.dim_airport")
airport_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM dbo.dim_aircraft")
aircraft_count = cursor.fetchone()[0]
print(f"dim_airport: {airport_count:,} rows, dim_aircraft: {aircraft_count:,} rows")
write_diag("gold_load_dim_crossdb", {"dim_airport_rows": airport_count, "dim_aircraft_rows": aircraft_count})
'''

CELL_6_LOAD_DIM_BANK_AND_REBANK = '''# dim_bank, fact_rebank_effect, fact_rebank_event_study: real output from
# the local analyses (analysis/dfw_bank_reconstruction.py,
# analysis/dfw_rebank_diffindiff.py -- see docs/analysis/*.csv), uploaded
# to Files/analysis/ via the established PowerShell OneLake upload
# pattern (ADR 0006). Tiny (35/3/16 rows) -- read with Spark, insert via
# pyodbc rather than a cross-db query, since these aren't Lakehouse Delta
# tables, just landed CSVs.
analysis_root = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}/Files/analysis"

bank_df = spark.read.option("header", True).csv(f"{analysis_root}/dim_bank_prototype.csv")
bank_rows = [
    (i + 1, r["station"], r["period"], r["direction"], int(r["bank_number"]), r["peak_time"], float(r["peak_avg_flights"]), r["window_start"], r["window_end"])
    for i, r in enumerate(bank_df.collect())
]
cursor.executemany(
    "INSERT INTO dbo.dim_bank (bank_key, station, period, direction, bank_number, peak_time, peak_avg_flights, window_start, window_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    bank_rows,
)

effect_df = spark.read.option("header", True).csv(f"{analysis_root}/fact_rebank_effect_prototype.csv")
effect_rows = [
    (r["hub"], r["metric"], r["model"], float(r["estimate"]), float(r["se"]), float(r["ci_low"]), float(r["ci_high"]), float(r["p_value"]), int(r["n"]), r["controls"])
    for r in effect_df.collect()
]
cursor.executemany(
    "INSERT INTO dbo.fact_rebank_effect (hub, metric, model, estimate, se, ci_low, ci_high, p_value, n, controls) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    effect_rows,
)

event_df = spark.read.option("header", True).csv(f"{analysis_root}/event_study_d0.csv")
event_rows = [
    (int(r["week_relative"]), float(r["coef"]), float(r["ci_low"]), float(r["ci_high"]))
    for r in event_df.collect()
]
cursor.executemany(
    "INSERT INTO dbo.fact_rebank_event_study (week_relative, coef, ci_low, ci_high) VALUES (?, ?, ?, ?)",
    event_rows,
)
print(f"dim_bank: {len(bank_rows)} rows, fact_rebank_effect: {len(effect_rows)} rows, fact_rebank_event_study: {len(event_rows)} rows")
write_diag("gold_load_bank_rebank", {"dim_bank_rows": len(bank_rows), "fact_rebank_effect_rows": len(effect_rows), "fact_rebank_event_study_rows": len(event_rows)})
'''

CELL_7_LOAD_FACT_FLIGHT_LEG = '''# fact_flight_leg: cross-database INSERT...SELECT from lh_silver's full
# 44-month/~27.8M-row Delta table. Pure server-side T-SQL -- no Spark
# DataFrame movement. TRY_CAST handles BTS's blank-string-for-missing
# convention (e.g. TaxiIn blank on cancelled legs) by returning NULL
# instead of erroring, matching this project's "don't treat a blank as
# zero" rule for the delay-cause columns (kpi_spec.md).
cursor.execute("""
    INSERT INTO dbo.fact_flight_leg (
        date_key, origin_airport_key, dest_airport_key, marketing_carrier_key, operating_carrier_key,
        flight_number, tail_number, sched_dep_utc, sched_arr_utc, actual_dep_utc, actual_arr_utc,
        dep_delay_min, arr_delay_min, taxi_out_min, taxi_in_min, crs_elapsed_min, actual_elapsed_min,
        carrier_delay_min, weather_delay_min, nas_delay_min, security_delay_min, late_aircraft_delay_min,
        is_cancelled, is_diverted, is_d0, is_a14, is_b0, is_controllable_cancel, cancellation_code, distance_mi
    )
    SELECT
        CAST(REPLACE(FlightDate, '-', '') AS INT),
        Origin,
        Dest,
        Marketing_Airline_Network,
        Operating_Airline,
        Flight_Number_Marketing_Airline,
        NULLIF(Tail_Number, ''),
        sched_dep_utc,
        sched_arr_utc,
        actual_dep_utc,
        actual_arr_utc,
        TRY_CAST(DepDelay AS FLOAT),
        TRY_CAST(ArrDelay AS FLOAT),
        txo_min,
        TRY_CAST(TaxiIn AS FLOAT),
        TRY_CAST(CRSElapsedTime AS FLOAT),
        TRY_CAST(ActualElapsedTime AS FLOAT),
        TRY_CAST(CarrierDelay AS FLOAT),
        TRY_CAST(WeatherDelay AS FLOAT),
        TRY_CAST(NASDelay AS FLOAT),
        TRY_CAST(SecurityDelay AS FLOAT),
        TRY_CAST(LateAircraftDelay AS FLOAT),
        is_cancelled,
        is_diverted,
        is_d0,
        is_a14,
        is_b0,
        is_controllable_cancel,
        NULLIF(CancellationCode, ''),
        TRY_CAST(Distance AS FLOAT)
    FROM [lh_silver].[dbo].[fact_flight_leg]
""")
cursor.execute("SELECT COUNT(*) FROM dbo.fact_flight_leg")
leg_count = cursor.fetchone()[0]
print(f"fact_flight_leg: {leg_count:,} rows")
write_diag("gold_load_fact_flight_leg", {"rows": leg_count})
'''

CELL_8_LOAD_FACT_CHECKPOINT_CBP = '''# fact_checkpoint_hour, fact_cbp_wait_hour: cross-database INSERT...SELECT
# from lh_bronze. hour is stored as a string in bronze TSA data
# (e.g. "0500") -- cast to the leading two digits as an int.
cursor.execute("""
    INSERT INTO dbo.fact_checkpoint_hour (station, [checkpoint], date_key, hour24, week_start, passengers_screened)
    SELECT
        iata,
        [checkpoint],
        CAST(REPLACE(CAST([date] AS VARCHAR(10)), '-', '') AS INT),
        CAST(LEFT(hour, 2) AS INT),
        week_start,
        pax
    FROM [lh_bronze].[dbo].[tsa_checkpoint_hour]
""")
cursor.execute("""
    INSERT INTO dbo.fact_cbp_wait_hour (
        station, terminal, date_key, hour_range, avg_wait_us, max_wait_us,
        avg_wait_non_us, max_wait_non_us, booths_open, flight_count, total_passengers
    )
    SELECT
        AirportCode,
        TerminalName,
        CAST(REPLACE(CAST(FlightDate AS VARCHAR(10)), '-', '') AS INT),
        HourRange,
        UsaPassengerAverageWait,
        UsaPassengerMaxWait,
        NonUsaPassengerAverageWait,
        NonUsaPassengerMaxWait,
        BoothsUsed,
        FlightCount,
        TotalPassengerCount
    FROM [lh_bronze].[dbo].[cbp_wait_hour]
""")
cursor.execute("SELECT COUNT(*) FROM dbo.fact_checkpoint_hour")
checkpoint_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM dbo.fact_cbp_wait_hour")
cbp_count = cursor.fetchone()[0]
print(f"fact_checkpoint_hour: {checkpoint_count:,} rows, fact_cbp_wait_hour: {cbp_count:,} rows")
write_diag("gold_load_fact_checkpoint_cbp", {"fact_checkpoint_hour_rows": checkpoint_count, "fact_cbp_wait_hour_rows": cbp_count})
'''

CELL_9_RECONCILE = '''# Phase 3 "done when" (revised per ADR 0012 -- DOT reconciliation
# replaced since DOT is out of scope): gold-layer D0/A14/CF aggregated by
# month must match the same metrics computed directly from
# lh_bronze.bts_marketing_ontime (the untransformed source), to the
# decimal, for every month checked. This still catches real bugs (a
# broken join, a dropped partition, a miscounted cancellation) using
# only data this project actually has.
cursor.execute("""
    WITH gold_monthly AS (
        SELECT
            date_key / 100 AS year_month,
            CAST(SUM(CASE WHEN is_cancelled = 0 AND is_d0 = 1 THEN 1 ELSE 0 END) AS FLOAT)
                / NULLIF(SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END), 0) AS d0_rate,
            CAST(SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS FLOAT)
                / NULLIF(COUNT(*), 0) AS cf_rate
        FROM dbo.fact_flight_leg
        GROUP BY date_key / 100
    ),
    bronze_monthly AS (
        SELECT
            CAST(Year AS INT) * 100 + CAST(Month AS INT) AS year_month,
            CAST(SUM(CASE WHEN TRY_CAST(Cancelled AS FLOAT) = 0 AND TRY_CAST(DepDelay AS FLOAT) <= 0 THEN 1 ELSE 0 END) AS FLOAT)
                / NULLIF(SUM(CASE WHEN TRY_CAST(Cancelled AS FLOAT) = 0 THEN 1 ELSE 0 END), 0) AS d0_rate,
            CAST(SUM(CASE WHEN TRY_CAST(Cancelled AS FLOAT) = 0 THEN 1 ELSE 0 END) AS FLOAT)
                / NULLIF(COUNT(*), 0) AS cf_rate
        FROM [lh_bronze].[dbo].[bts_marketing_ontime]
        GROUP BY CAST(Year AS INT) * 100 + CAST(Month AS INT)
    )
    SELECT
        g.year_month,
        g.d0_rate AS gold_d0, b.d0_rate AS bronze_d0, ABS(g.d0_rate - b.d0_rate) AS d0_diff,
        g.cf_rate AS gold_cf, b.cf_rate AS bronze_cf, ABS(g.cf_rate - b.cf_rate) AS cf_diff
    FROM gold_monthly g
    JOIN bronze_monthly b ON g.year_month = b.year_month
    ORDER BY g.year_month
""")
rows = cursor.fetchall()
reconciliation = [
    {
        "year_month": r[0], "gold_d0": r[1], "bronze_d0": r[2], "d0_diff": r[3],
        "gold_cf": r[4], "bronze_cf": r[5], "cf_diff": r[6],
    }
    for r in rows
]
max_d0_diff = max((r["d0_diff"] for r in reconciliation), default=None)
max_cf_diff = max((r["cf_diff"] for r in reconciliation), default=None)
print(f"months checked: {len(reconciliation)}, max D0 diff: {max_d0_diff}, max CF diff: {max_cf_diff}")
write_diag("gold_reconciliation", {"months_checked": len(reconciliation), "max_d0_diff": max_d0_diff, "max_cf_diff": max_cf_diff, "detail": reconciliation})
'''

CELL_10_SUMMARY = '''# Final row-count summary across every gold table, for a single
# self-verifiable readout.
tables = [
    "dim_date", "dim_time_15", "dim_airport", "dim_carrier", "dim_aircraft",
    "dim_delay_cause", "dim_cancel_reason", "dim_bank",
    "fact_flight_leg", "fact_checkpoint_hour", "fact_cbp_wait_hour",
    "fact_rebank_effect", "fact_rebank_event_study",
]
summary = {}
for t in tables:
    cursor.execute(f"SELECT COUNT(*) FROM dbo.{t}")
    summary[t] = cursor.fetchone()[0]
print(_json.dumps(summary, indent=2))
write_diag("gold_summary", summary)
'''


def _wrap_cell(name: str, body: str) -> str:
    """Wrap a cell body in try/except so a failure writes its full
    traceback to Files/diagnostics/<name>_ERROR.json instead of just
    dying -- the Fabric REST job-status API never surfaces cell-level
    errors, so this is the only way to self-diagnose a failure without
    portal access."""
    import textwrap

    indented = textwrap.indent(body.rstrip() + "\n", "    ")
    return (
        f"try:\n{indented}"
        f"except Exception as e:\n"
        f"    write_diag('{name}_ERROR', {{'error': repr(e), 'traceback': _tb.format_exc()}})\n"
        f"    raise\n"
    )


CELL_2_LOAD_DIM_DATE = _wrap_cell("gold_load_dim_date", CELL_2_LOAD_DIM_DATE)
CELL_3_LOAD_DIM_TIME = _wrap_cell("gold_load_dim_time", CELL_3_LOAD_DIM_TIME)
CELL_4_LOAD_DIM_STATIC = _wrap_cell("gold_load_dim_static", CELL_4_LOAD_DIM_STATIC)
CELL_5_LOAD_DIM_CROSSDB = _wrap_cell("gold_load_dim_crossdb", CELL_5_LOAD_DIM_CROSSDB)
CELL_6_LOAD_DIM_BANK_AND_REBANK = _wrap_cell("gold_load_bank_rebank", CELL_6_LOAD_DIM_BANK_AND_REBANK)
CELL_7_LOAD_FACT_FLIGHT_LEG = _wrap_cell("gold_load_fact_flight_leg", CELL_7_LOAD_FACT_FLIGHT_LEG)
CELL_8_LOAD_FACT_CHECKPOINT_CBP = _wrap_cell("gold_load_fact_checkpoint_cbp", CELL_8_LOAD_FACT_CHECKPOINT_CBP)
CELL_9_RECONCILE = _wrap_cell("gold_reconciliation", CELL_9_RECONCILE)
CELL_10_SUMMARY = _wrap_cell("gold_summary", CELL_10_SUMMARY)

CELLS = [
    CELL_0_CONNECT,
    CELL_1_DDL,
    CELL_2_LOAD_DIM_DATE,
    CELL_3_LOAD_DIM_TIME,
    CELL_4_LOAD_DIM_STATIC,
    CELL_5_LOAD_DIM_CROSSDB,
    CELL_6_LOAD_DIM_BANK_AND_REBANK,
    CELL_7_LOAD_FACT_FLIGHT_LEG,
    CELL_8_LOAD_FACT_CHECKPOINT_CBP,
    CELL_9_RECONCILE,
    CELL_10_SUMMARY,
]


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
            "displayName": "nb_gold_build",
            "description": "Phase 3: create wh_gold's star schema and load it from lh_bronze/lh_silver via cross-database T-SQL.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_gold_build.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
