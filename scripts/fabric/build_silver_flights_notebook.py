"""Build nb_silver_flights: resolve BTS's local-clock, no-timezone hhmm
fields into real UTC instants, compute per-leg KPI flags (docs/kpi_spec.md),
apply data quality constraints, and write lh_silver.fact_flight_leg (+
fact_flight_leg_quarantine). Same time-resolution algorithm as
hubops.bts_times.resolve_leg_times, reimplemented inline since Fabric's
Spark environment doesn't have this project's local package (ADR 0008).

See docs/decisions/0010-silver-time-resolution-bugs.md and
0011-g4-tail-number-quirk.md for the real debugging history behind this
notebook's current shape -- four rounds of bugs (schema mutation,
mapInPandas signature, array_remove-null, actual-time day-rollover) plus
a genuine BTS-side data quirk (Allegiant/G4 tails missing their leading
"N"), all found by validating against an independent ground truth rather
than trusting internal consistency.

Usage:
    uv run python scripts/fabric/build_silver_flights_notebook.py --out fabric/nb_silver_flights.Notebook

Then push/run with scripts/fabric/push_notebook.py (see its docstring for
the az rest create/update/run/poll commands).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELL_0_SETUP = """# Phase 2 silver: cast types, resolve local-clock/no-timezone BTS times
# into real UTC instants + correct arrival dates (same algorithm as
# hubops.bts_times.resolve_leg_times -- reimplemented inline since
# Fabric's Spark environment doesn't have this project's local package;
# see ADR 0008 for why that's the established pattern here), compute
# KPI flags per docs/kpi_spec.md, apply data quality constraints,
# quarantine failures with a reason code.
#
# TEST_YEAR/TEST_MONTH: when set, filters to a single Year/Month
# partition for fast iteration before running the full 44-month/
# ~25M-row load. Set TEST_YEAR to None for the real run.
TEST_YEAR = "2026"
TEST_MONTH = "8"

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
LH_SILVER_ID = "852a5b52-5eee-4ca5-8380-3f9a970fa0a0"
BRONZE_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"
SILVER_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_SILVER_ID}"

bts = spark.read.format("delta").load(f"{BRONZE_ROOT}/Tables/bts_marketing_ontime")
if TEST_YEAR is not None:
    bts = bts.filter((bts.Year == TEST_YEAR) & (bts.Month == TEST_MONTH))
print(f"bronze rows (filtered={TEST_YEAR is not None}): {bts.count():,}")

# Known BTS export quirk, confirmed via a throwaway diagnostic notebook
# (nb_diag_tail, not committed -- see ADR 0011): Allegiant (G4) tail
# numbers are missing their leading "N" in this data -- e.g. "816NV"
# instead of "N816NV". Allegiant themes its Las Vegas-based fleet's tails
# as N###NV, and 100% of the rows that fail TAIL_NUMBER_RE below belong
# to G4 and match ^[0-9]{2,4}NV$ once the missing "N" is restored. This
# is a deterministic, carrier-specific formatting quirk, not malformed
# data -- reconstruct the real tail instead of quarantining ~1.5% of all
# rows over it (needed later for turn-pairing, which keys on Tail_Number).
from pyspark.sql.functions import col as _col, when as _when, concat as _concat, lit as _lit

_g4_missing_n = (_col("Operating_Airline") == "G4") & _col("Tail_Number").rlike(r"^[0-9]{2,4}NV$")
# Count BEFORE reassigning bts -- after the withColumn below, every fixed
# Tail_Number already has its "N" restored, so filtering on the same
# _g4_missing_n predicate against the NEW bts would always find 0 (a real
# bug caught in this project's first run: the diagnostic print read 0
# fixed even though the quarantine count dropped by ~9,900 rows).
fixed_count = bts.filter(_g4_missing_n).count()
bts = bts.withColumn(
    "Tail_Number",
    _when(_g4_missing_n, _concat(_lit("N"), _col("Tail_Number"))).otherwise(_col("Tail_Number")),
)
print(f"G4 tail numbers normalized (missing leading N restored): {fixed_count:,}")

airports = spark.read.format("delta").load(f"{BRONZE_ROOT}/Tables/dim_airport")
airports_bc = {row["iata"]: row["tz"] for row in airports.select("iata", "tz").collect()}
print(f"airport tz lookup: {len(airports_bc):,} airports")
"""

CELL_1_TZ_JOIN = """from pyspark.sql.functions import udf
from pyspark.sql.types import StringType

tz_lookup_bc = spark.sparkContext.broadcast(airports_bc)

@udf(returnType=StringType())
def lookup_tz(iata):
    return tz_lookup_bc.value.get(iata)

bts = bts.withColumn("origin_tz", lookup_tz(bts.Origin)).withColumn("dest_tz", lookup_tz(bts.Dest))
missing_tz = bts.filter(bts.origin_tz.isNull() | bts.dest_tz.isNull()).count()
print(f"rows with missing origin/dest tz: {missing_tz:,}")
"""

CELL_2_RESOLVE = '''# Same candidate-date-search algorithm as hubops.bts_times.resolve_leg_times
# (see that module's docstring for the full reasoning on why this is
# needed at all). Applied via mapInPandas for Arrow-efficient batching
# across partitions -- this is still single-threaded Python per batch
# (zoneinfo DST-aware conversion doesn't vectorize cleanly), but runs in
# parallel across the cluster's partitions.
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from pyspark.sql.types import DoubleType, DateType, TimestampType

_MIDNIGHT_CODE = 2400
_MINUTES_PER_DAY = 1440
_CANDIDATE_DAY_OFFSETS = (-1, 0, 1, 2)


def _parse_hhmm_to_minutes(raw):
    if raw is None or (isinstance(raw, float) and raw != raw):
        return None
    text = str(raw).strip()
    if text == "" or text.lower() == "nan":
        return None
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        return None
    value = int(text)
    if value < 0 or value > _MIDNIGHT_CODE:
        return None
    if value == _MIDNIGHT_CODE:
        return _MINUTES_PER_DAY
    hour, minute = divmod(value, 100)
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _combine_local(day, minutes_since_midnight):
    if minutes_since_midnight == _MINUTES_PER_DAY:
        return datetime(day.year, day.month, day.day) + timedelta(days=1)
    hour, minute = divmod(minutes_since_midnight, 60)
    return datetime(day.year, day.month, day.day, hour, minute)


def _resolve_leg(flight_date_str, hhmm_dep, hhmm_arr, elapsed_min, origin_tz, dest_tz):
    if origin_tz is None or dest_tz is None:
        return None
    try:
        flight_date = date.fromisoformat(flight_date_str)
        elapsed = float(elapsed_min)
    except (TypeError, ValueError):
        return None

    dep_minutes = _parse_hhmm_to_minutes(hhmm_dep)
    arr_minutes = _parse_hhmm_to_minutes(hhmm_arr)
    if dep_minutes is None or arr_minutes is None:
        return None

    try:
        dep_naive = _combine_local(flight_date, dep_minutes)
        dep_local = dep_naive.replace(tzinfo=ZoneInfo(origin_tz))
        dep_utc = dep_local.astimezone(ZoneInfo("UTC"))
    except Exception:
        return None

    best = None
    best_abs_mismatch = float("inf")
    for offset in _CANDIDATE_DAY_OFFSETS:
        candidate_date = flight_date + timedelta(days=offset)
        try:
            candidate_naive = _combine_local(candidate_date, arr_minutes)
            candidate_local = candidate_naive.replace(tzinfo=ZoneInfo(dest_tz))
            candidate_utc = candidate_local.astimezone(ZoneInfo("UTC"))
        except Exception:
            continue
        implied_elapsed = (candidate_utc - dep_utc).total_seconds() / 60
        mismatch = implied_elapsed - elapsed
        if abs(mismatch) < best_abs_mismatch:
            best_abs_mismatch = abs(mismatch)
            best = (dep_utc.replace(tzinfo=None), candidate_utc.replace(tzinfo=None), candidate_local.date(), mismatch)

    return best


def _to_float(value):
    try:
        if value is None:
            return None
        f = float(value)
        return None if f != f else f  # filter NaN
    except (TypeError, ValueError):
        return None


def _resolve_one_batch(pdf: pd.DataFrame) -> pd.DataFrame:
    # SCHEDULED times: CRSDepTime anchors correctly to FlightDate (BTS
    # defines FlightDate as the *scheduled* departure's date -- this is
    # the one anchor that's genuinely unambiguous), and CRSArrTime is
    # resolved via the candidate-date search against CRSElapsedTime.
    sched = pdf.apply(
        lambda r: _resolve_leg(r["FlightDate"], r["CRSDepTime"], r["CRSArrTime"], r["CRSElapsedTime"], r["origin_tz"], r["dest_tz"]),
        axis=1,
    )
    pdf["sched_dep_utc"] = sched.apply(lambda t: t[0] if t else None)
    pdf["sched_arr_utc"] = sched.apply(lambda t: t[1] if t else None)
    pdf["sched_arr_date"] = sched.apply(lambda t: t[2] if t else None)
    pdf["sched_elapsed_mismatch_min"] = sched.apply(lambda t: t[3] if t else None)

    # ACTUAL times: do NOT re-anchor DepTime/ArrTime to FlightDate the
    # same way -- unlike CRSDepTime, there's no column that unambiguously
    # tells us which calendar date the ACTUAL departure landed on, and a
    # big delay can push it past midnight (confirmed with real data: a
    # flight scheduled 23:05, delayed 78 min per BTS's own DepDelay,
    # actually departs 00:23 the *next* day -- anchoring DepTime to
    # FlightDate instead produces an ~23-hour-wrong "actual" timestamp).
    # BTS's own DepDelay/ArrDelay are already the authoritative, correctly
    # day-aware offsets -- just apply them to the (already-correct)
    # scheduled instant instead of re-deriving from the raw hhmm fields.
    # NOTE: pdf["DepDelay"].apply(_to_float) looks like it should return a
    # Series of clean (float or None) values, but once that Series mixes
    # None with floats, pandas silently coerces the whole column to
    # float64 -- which converts every None into NaN. `d is not None` does
    # NOT catch that (NaN is not None is True in Python), so a NaN slips
    # through into pd.Timedelta(minutes=d) and blows up. Use pd.notna(),
    # which is true for neither None/NaN/NaT, on both sides of the check.
    dep_delay = pdf["DepDelay"].apply(_to_float)
    arr_delay = pdf["ArrDelay"].apply(_to_float)
    pdf["actual_dep_utc"] = [
        (sched_dt + pd.Timedelta(minutes=d)) if (pd.notna(sched_dt) and pd.notna(d)) else None
        for sched_dt, d in zip(pdf["sched_dep_utc"], dep_delay)
    ]
    pdf["actual_arr_utc"] = [
        (sched_dt + pd.Timedelta(minutes=d)) if (pd.notna(sched_dt) and pd.notna(d)) else None
        for sched_dt, d in zip(pdf["sched_arr_utc"], arr_delay)
    ]

    # Cross-check (not used to derive anything): does the actual elapsed
    # time implied by actual_arr_utc - actual_dep_utc match BTS's own
    # reported ActualElapsedTime? A real mismatch here points to a
    # genuine BTS internal data inconsistency, not a resolution bug.
    actual_elapsed_min = pdf["ActualElapsedTime"].apply(_to_float)

    def _elapsed_mismatch(dep_dt, arr_dt, reported):
        if pd.isna(dep_dt) or pd.isna(arr_dt) or pd.isna(reported):
            return None
        return (arr_dt - dep_dt).total_seconds() / 60 - reported

    pdf["actual_elapsed_mismatch_min"] = [
        _elapsed_mismatch(d, a, e)
        for d, a, e in zip(pdf["actual_dep_utc"], pdf["actual_arr_utc"], actual_elapsed_min)
    ]
    pdf["actual_arr_date"] = pdf["actual_arr_utc"].apply(lambda t: t.date() if pd.notna(t) else None)
    return pdf


def resolve_batch(iterator):
    # mapInPandas calls this with an ITERATOR of pandas.DataFrame batches
    # and expects an iterator of transformed batches back (not a single
    # DataFrame in, DataFrame out) -- confirmed against the real
    # workspace: the original single-DataFrame signature failed with
    # "AttributeError: 'map' object has no attribute 'apply'" because
    # Spark was actually handing it something else entirely.
    for pdf in iterator:
        yield _resolve_one_batch(pdf)


from pyspark.sql.types import StructType, StructField

# NOTE: df.schema.add(...) mutates the schema object in place in this
# Spark version rather than returning a safe copy -- confirmed against
# the real workspace (it corrupted bts's own schema metadata, causing
# "UNRESOLVED_COLUMN" errors referencing columns that didn't exist yet).
# Build a genuinely new StructType instead.
output_schema = StructType(
    list(bts.schema.fields)
    + [
        StructField("sched_dep_utc", TimestampType()),
        StructField("sched_arr_utc", TimestampType()),
        StructField("sched_arr_date", DateType()),
        StructField("sched_elapsed_mismatch_min", DoubleType()),
        StructField("actual_dep_utc", TimestampType()),
        StructField("actual_arr_utc", TimestampType()),
        StructField("actual_arr_date", DateType()),
        StructField("actual_elapsed_mismatch_min", DoubleType()),
    ]
)

resolved = bts.mapInPandas(resolve_batch, schema=output_schema)
resolved.cache()
print(f"resolved rows: {resolved.count():,}")
'''

CELL_3_VALIDATE = """# Validate against the plan's Phase 2 "done when": recomputed dep/arr
# delay must match BTS's own reported delay within 1 minute on 99.9% of
# rows. This is now closer to a construction check than an independent
# test -- actual_dep_utc/actual_arr_utc are DEFINED as
# sched_utc + reported_delay (see CELL_2's docstring for why: BTS's own
# delay columns are the authoritative, day-aware source of truth, and
# re-deriving actual times independently from raw DepTime/ArrTime hhmm
# fields reintroduces exactly the ambiguity BTS already resolved for us).
# The genuinely independent check is whether the ELAPSED TIME implied by
# (actual_arr_utc - actual_dep_utc) matches BTS's own reported
# ActualElapsedTime -- that only holds if sched_dep_utc and sched_arr_utc
# were BOTH resolved correctly in the first place.
from pyspark.sql.functions import col, unix_timestamp, abs as sabs, when

check = resolved.filter((col("Cancelled") == 0) & (col("Diverted") == 0) & col("actual_dep_utc").isNotNull() & col("sched_dep_utc").isNotNull())
check = check.withColumn("dep_delay_recomputed", (unix_timestamp(col("actual_dep_utc")) - unix_timestamp(col("sched_dep_utc"))) / 60)
check = check.withColumn("dep_delay_diff", sabs(col("dep_delay_recomputed") - col("DepDelay").cast("double")))
total = check.count()
within_1min = check.filter(col("dep_delay_diff") <= 1.0).count()
pct = (within_1min / total * 100) if total else 0
print(f"dep delay match within 1 min (construction check): {within_1min:,} / {total:,} = {pct:.3f}%")

elapsed_check = resolved.filter(
    (col("Cancelled") == 0) & (col("Diverted") == 0)
    & col("actual_dep_utc").isNotNull() & col("actual_arr_utc").isNotNull()
    & col("ActualElapsedTime").isNotNull() & (col("ActualElapsedTime") != "")
)
elapsed_check = elapsed_check.withColumn(
    "elapsed_recomputed", (unix_timestamp(col("actual_arr_utc")) - unix_timestamp(col("actual_dep_utc"))) / 60
)
elapsed_check = elapsed_check.withColumn("elapsed_diff", sabs(col("elapsed_recomputed") - col("ActualElapsedTime").cast("double")))
etotal = elapsed_check.count()
ewithin = elapsed_check.filter(col("elapsed_diff") <= 1.0).count()
epct = (ewithin / etotal * 100) if etotal else 0
print(f"actual elapsed time match within 1 min (independent check): {ewithin:,} / {etotal:,} = {epct:.3f}%")

elapsed_mismatched = elapsed_check.filter(col("elapsed_diff") > 1.0)
print(f"elapsed-time mismatches: {elapsed_mismatched.count():,}")
elapsed_mismatched.select("Tail_Number", "FlightDate", "Origin", "Dest", "CRSDepTime", "DepTime", "DepDelay", "ArrTime", "ArrDelay", "ActualElapsedTime", "elapsed_recomputed").show(10, truncate=False)
"""

CELL_4_KPIS = """# Per-leg KPI flags, matching docs/kpi_spec.md's definitions exactly.
from pyspark.sql.functions import lit

df = resolved
df = df.withColumn("is_cancelled", col("Cancelled") == 1)
df = df.withColumn("is_diverted", col("Diverted") == 1)

# D0: operated (not cancelled) departures at or before schedule.
df = df.withColumn("is_d0", when(df.is_cancelled, None).otherwise(col("DepDelay").cast("double") <= 0))

# A14: operated arrivals within 14 minutes of schedule.
df = df.withColumn("is_a14", when(df.is_cancelled, None).otherwise(col("ArrDelay").cast("double") <= 14))

# CF handled at gold (aggregate), not per-leg here.
# Controllable cancel: carrier-caused (code A).
df = df.withColumn("is_controllable_cancel", df.is_cancelled & (col("CancellationCode") == "A"))

# B0: actual gate-to-gate at or under scheduled block, diverted excluded.
df = df.withColumn(
    "is_b0",
    when(df.is_cancelled | df.is_diverted, None)
    .otherwise(col("ActualElapsedTime").cast("double") <= col("CRSElapsedTime").cast("double")),
)

# TXO: taxi-out minutes, capped at 180 per kpi_spec.md (log capped rows, don't drop).
df = df.withColumn("txo_raw_min", col("TaxiOut").cast("double"))
df = df.withColumn("txo_capped", col("txo_raw_min") > 180)
df = df.withColumn("txo_min", when(col("txo_raw_min") > 180, lit(180.0)).otherwise(col("txo_raw_min")))
capped_count = df.filter(col("txo_capped") == True).count()
print(f"TXO rows capped at 180 min: {capped_count:,}")
"""

CELL_5_QUALITY = """# Data quality constraints (planning/project-plan.md "Data quality in
# silver"). Each check adds a reason to quarantine_reasons; a leg with
# any non-empty reason goes to the quarantine table, not fact_flight_leg.
from pyspark.sql.functions import concat_ws, array, array_remove, row_number
from pyspark.sql.window import Window
import re as _re

TAIL_NUMBER_RE = r"^N[0-9A-Z]{1,5}$"

# 1. duplicate leg keys -- (carrier, flight number, date, origin, dest).
# Keep the first occurrence (by row order), quarantine the rest.
leg_key_window = Window.partitionBy(
    "Marketing_Airline_Network", "Flight_Number_Marketing_Airline", "FlightDate", "Origin", "Dest"
).orderBy(lit(1))
df = df.withColumn("_leg_key_rownum", row_number().over(leg_key_window))
df = df.withColumn("reason_duplicate_leg_key", when(col("_leg_key_rownum") > 1, lit("duplicate_leg_key")).otherwise(lit(None)))

# 2. arrival before departure (actual) -- should be structurally impossible
# after resolve_leg_times, but check explicitly rather than assume.
df = df.withColumn(
    "reason_arr_before_dep",
    when(
        col("actual_dep_utc").isNotNull() & col("actual_arr_utc").isNotNull() & (col("actual_arr_utc") < col("actual_dep_utc")),
        lit("arr_before_dep"),
    ).otherwise(lit(None)),
)

# 3. actual elapsed time (actual_arr_utc - actual_dep_utc) doesn't match
#    BTS's own reported ActualElapsedTime within 1 min (operated,
#    non-diverted legs only). NOTE: this is NOT a check on DepDelay/
#    ArrDelay themselves -- actual_dep_utc/actual_arr_utc are DEFINED as
#    sched_utc + reported delay (see CELL_2's docstring), so comparing
#    recomputed delay back to DepDelay/ArrDelay would be tautological.
#    This elapsed-time check is the genuinely independent one: it only
#    holds if sched_dep_utc and sched_arr_utc were both resolved
#    correctly in the first place.
df = df.withColumn(
    "_elapsed_recomputed",
    when(col("actual_dep_utc").isNotNull() & col("actual_arr_utc").isNotNull(),
         (unix_timestamp(col("actual_arr_utc")) - unix_timestamp(col("actual_dep_utc"))) / 60),
)
df = df.withColumn(
    "reason_elapsed_mismatch",
    when(
        (~df.is_cancelled) & (~df.is_diverted) & col("_elapsed_recomputed").isNotNull()
        & col("ActualElapsedTime").isNotNull() & (col("ActualElapsedTime") != "")
        & (sabs(col("_elapsed_recomputed") - col("ActualElapsedTime").cast("double")) > 1.0),
        lit("elapsed_mismatch"),
    ).otherwise(lit(None)),
)

# 4. cause minutes don't sum to arrival delay (only checked when BTS
#    actually populates cause buckets -- 15+ min late arrivals; see
#    kpi_spec.md's "Controllable delay share" edge case).
cause_sum = (
    col("CarrierDelay").cast("double") + col("WeatherDelay").cast("double")
    + col("NASDelay").cast("double") + col("SecurityDelay").cast("double")
    + col("LateAircraftDelay").cast("double")
)
df = df.withColumn(
    "reason_cause_minutes_mismatch",
    when(
        (col("ArrDel15") == 1) & col("CarrierDelay").isNotNull()
        & (sabs(cause_sum - col("ArrDelayMinutes").cast("double")) > 1.0),
        lit("cause_minutes_mismatch"),
    ).otherwise(lit(None)),
)

# 5. malformed tail numbers (only checked when present -- blank is
#    expected for some cancelled legs, not malformed). G4/Allegiant's
#    missing-leading-N quirk is normalized in CELL_0, before this check
#    ever runs -- see ADR 0011.
df = df.withColumn(
    "reason_malformed_tail",
    when(
        col("Tail_Number").isNotNull() & (col("Tail_Number") != "") & (~col("Tail_Number").rlike(TAIL_NUMBER_RE)),
        lit("malformed_tail_number"),
    ).otherwise(lit(None)),
)

reason_cols = [
    "reason_duplicate_leg_key", "reason_arr_before_dep", "reason_elapsed_mismatch",
    "reason_cause_minutes_mismatch", "reason_malformed_tail",
]

# Diagnostic: count each reason column's non-null hits BEFORE combining,
# so a bug in "does any reason fire at all" vs "does combining them into
# an array work" is distinguishable from the printed output.
for c in reason_cols:
    n = df.filter(col(c).isNotNull()).count()
    print(f"  {c}: {n:,} rows")

from pyspark.sql.functions import size, filter as spark_filter

# array_remove(arr, None) is ambiguous about null-matching semantics in
# Spark SQL (NULL = NULL is NULL, not TRUE) -- use the higher-order
# `filter` function instead, which applies an explicit predicate and has
# no such ambiguity.
df = df.withColumn("quarantine_reasons", spark_filter(array(*reason_cols), lambda x: x.isNotNull()))
df = df.withColumn("is_quarantined", size(col("quarantine_reasons")) > 0)

quarantine_count = df.filter(col("is_quarantined")).count()
total_count = df.count()
print(f"quarantined: {quarantine_count:,} / {total_count:,} = {quarantine_count/total_count*100:.3f}%")
df.filter(col("is_quarantined")).groupBy("reason_duplicate_leg_key", "reason_arr_before_dep", "reason_elapsed_mismatch", "reason_cause_minutes_mismatch", "reason_malformed_tail").count().show(20, truncate=False)
"""

CELL_6_WRITE = """# Split and write: clean legs to silver fact_flight_leg, quarantined
# legs to a separate table with their reason codes, for traceability.
clean = df.filter(~col("is_quarantined")).drop(
    "_leg_key_rownum", "_elapsed_recomputed", "quarantine_reasons", "is_quarantined",
    *reason_cols,
)
quarantined = df.filter(col("is_quarantined"))

fact_path = f"{SILVER_ROOT}/Tables/fact_flight_leg"
quarantine_path = f"{SILVER_ROOT}/Tables/fact_flight_leg_quarantine"

clean.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(fact_path)
quarantined.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(quarantine_path)
print(f"wrote {clean.count():,} clean rows to {fact_path}")
print(f"wrote {quarantined.count():,} quarantined rows to {quarantine_path}")
"""

CELLS = [CELL_0_SETUP, CELL_1_TZ_JOIN, CELL_2_RESOLVE, CELL_3_VALIDATE, CELL_4_KPIS, CELL_5_QUALITY, CELL_6_WRITE]


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
            "displayName": "nb_silver_flights",
            "description": "Phase 2: resolve BTS times to UTC, compute KPI flags, apply quality constraints, write lh_silver.fact_flight_leg (+ quarantine).",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_silver_flights.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
