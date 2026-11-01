"""Build nb_silver_weather: parse lh_bronze.weather_metar_raw's raw METAR
text into the four severity signals (thunderstorm, ceiling <1000ft,
visibility <3mi, gusts >30kt), same scoring as the tested
src/hubops/metar.py (reimplemented inline per ADR 0008's established
pattern -- Fabric's Spark environment doesn't have this project's local
package), bucket to station-hour (UTC), and write
lh_silver.weather_station_hour_severity.

METAR token parsing is genuinely sequential/stateful (wind, visibility,
cloud layers, weather phenomena intermixed in one space-delimited
string) and doesn't vectorize into Spark SQL expressions the way the
BTS/TSA transforms do -- same reasoning as using mapInPandas for the
BTS time resolution in nb_silver_flights.

Usage:
    uv run python scripts/fabric/build_silver_weather_notebook.py --out fabric/nb_silver_weather.Notebook
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CELL_0_SETUP = '''# Phase 4/6: weather severity scoring, silver layer. Bronze stays RAW
# (station, valid_utc, metar text) -- this is the transform.
#
# Every cell writes its result, or on failure its full traceback, to
# Files/diagnostics/<name>.json -- the Fabric REST job-status API never
# surfaces cell-level errors, so this is the only self-diagnosable path
# (established in nb_gold_build/nb_gold_turns, ADR 0013).
import json as _json, traceback as _tb

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
LH_SILVER_ID = "852a5b52-5eee-4ca5-8380-3f9a970fa0a0"
BRONZE_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"
SILVER_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_SILVER_ID}"

def write_diag(name, obj):
    path = f"{BRONZE_ROOT}/Files/diagnostics/{name}.json"
    notebookutils.fs.put(path, _json.dumps(obj, indent=2, default=str), overwrite=True)
    print(f"wrote {path}")

try:
    raw = spark.read.format("delta").load(f"{BRONZE_ROOT}/Tables/weather_metar_raw")
    raw_count = raw.count()
    print(f"weather_metar_raw rows: {raw_count:,}")
    raw.groupBy("station").count().orderBy("station").show()
    write_diag("silver_weather_setup", {"status": "ok", "rows": raw_count})
except Exception as e:
    write_diag("silver_weather_setup", {"status": "failed", "error": repr(e), "traceback": _tb.format_exc()})
    raise
'''

# Same regexes and parsing logic as src/hubops/metar.py -- reimplemented
# inline rather than imported, per ADR 0008 (Fabric's Spark environment
# doesn't have this project's local package installed).
CELL_1_PARSE = '''import re
import pandas as pd
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, FloatType, BooleanType

_WIND_RE = re.compile(r"^(?:\\d{3}|VRB)(\\d{2,3})(?:G(\\d{2,3}))?KT$")
_VISIBILITY_WHOLE_RE = re.compile(r"^(\\d{1,2})SM$")
_VISIBILITY_FRACTION_RE = re.compile(r"^(M)?(\\d+)/(\\d+)SM$")
_VISIBILITY_MIXED_RE = re.compile(r"^(\\d{1,2}) (\\d+)/(\\d+)SM$")
_CLOUD_RE = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\\d{3})$")
_WEATHER_PHENOMENON_RE = re.compile(
    r"^[+-]?(VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(DZ|RA|SN|SG|IC|PL|GR|GS|UP|FG|BR|SA|DU|HZ|FU|VA|PY|PO|SQ|FC|SS|DS)*$"
)
_CEILING_COVER_CODES = {"BKN", "OVC", "VV"}


def _parse_visibility_token(tokens, i):
    token = tokens[i]
    m = _VISIBILITY_WHOLE_RE.match(token)
    if m:
        return float(m.group(1)), 1
    m = _VISIBILITY_FRACTION_RE.match(token)
    if m:
        _, num, den = m.groups()
        return int(num) / int(den), 1
    if i + 1 < len(tokens):
        combined = f"{token} {tokens[i + 1]}"
        m = _VISIBILITY_MIXED_RE.match(combined)
        if m:
            whole, num, den = m.groups()
            return int(whole) + int(num) / int(den), 2
    return None


def _parse_metar_one(raw_text):
    if raw_text is None:
        return (None, None, None, False)
    body = raw_text.split("RMK")[0].strip()
    tokens = body.split()

    wind_gust_kt = None
    visibility_mi = None
    ceiling_ft = None
    has_thunderstorm = False

    i = 0
    while i < len(tokens):
        token = tokens[i]

        wind_match = _WIND_RE.match(token)
        if wind_match and wind_gust_kt is None:
            gust = wind_match.group(2)
            if gust is not None:
                wind_gust_kt = int(gust)
            i += 1
            continue

        vis = _parse_visibility_token(tokens, i)
        if vis is not None:
            visibility_mi, consumed = vis
            i += consumed
            continue

        cloud_match = _CLOUD_RE.match(token)
        if cloud_match:
            cover, height_hundreds_ft = cloud_match.groups()
            if cover in _CEILING_COVER_CODES:
                height_ft = int(height_hundreds_ft) * 100
                if ceiling_ft is None or height_ft < ceiling_ft:
                    ceiling_ft = height_ft
            i += 1
            continue

        if "TS" in token and _WEATHER_PHENOMENON_RE.match(token):
            has_thunderstorm = True
            i += 1
            continue

        i += 1

    return (wind_gust_kt, visibility_mi, ceiling_ft, has_thunderstorm)


def parse_batch(iterator):
    # Same mapInPandas iterator-of-batches-in/out signature as
    # nb_silver_flights -- a single-DataFrame function was the real bug
    # caught building that notebook (ADR 0010); not repeating it here.
    for pdf in iterator:
        parsed = pdf["metar"].apply(_parse_metar_one)
        pdf["wind_gust_kt"] = parsed.apply(lambda t: t[0])
        pdf["visibility_mi"] = parsed.apply(lambda t: t[1])
        pdf["ceiling_ft"] = parsed.apply(lambda t: t[2])
        pdf["has_thunderstorm"] = parsed.apply(lambda t: t[3])
        pdf["severity_score"] = (
            pdf["has_thunderstorm"].astype(int)
            + (pdf["ceiling_ft"].notna() & (pdf["ceiling_ft"] < 1000)).astype(int)
            + (pdf["visibility_mi"].notna() & (pdf["visibility_mi"] < 3)).astype(int)
            + (pdf["wind_gust_kt"].notna() & (pdf["wind_gust_kt"] > 30)).astype(int)
        )
        yield pdf


output_schema = StructType(
    list(raw.schema.fields)
    + [
        StructField("wind_gust_kt", IntegerType()),
        StructField("visibility_mi", FloatType()),
        StructField("ceiling_ft", IntegerType()),
        StructField("has_thunderstorm", BooleanType()),
        StructField("severity_score", IntegerType()),
    ]
)

parsed_df = raw.mapInPandas(parse_batch, schema=output_schema)
parsed_df.cache()
parsed_count = parsed_df.count()
print(f"parsed rows: {parsed_count:,}")
severity_counts = {str(r["severity_score"]): r["count"] for r in parsed_df.groupBy("severity_score").count().collect()}
parsed_df.groupBy("severity_score").count().orderBy("severity_score").show()
write_diag("silver_weather_parse", {"status": "ok", "rows": parsed_count, "severity_counts": severity_counts})
'''

CELL_2_BUCKET_WRITE = '''from pyspark.sql.functions import col, to_timestamp, date_trunc, to_date, hour, max as smax, min as smin, count as scount

# valid_utc from IEM is "YYYY-MM-DD HH:MM" UTC (confirmed by the
# nb_bronze_weather source -- IEM's asos.py with tz=UTC). Bucket to the
# UTC hour -- same timezone basis as fact_flight_leg's sched/actual_*_utc
# columns in gold, so these join cleanly without a local/UTC conversion.
bucketed = parsed_df.withColumn("valid_ts", to_timestamp(col("valid_utc"), "yyyy-MM-dd HH:mm"))
bucketed = bucketed.withColumn("obs_date", to_date(col("valid_ts")))
bucketed = bucketed.withColumn("obs_hour", hour(col("valid_ts")))

station_hour = (
    bucketed.groupBy("station", "obs_date", "obs_hour")
    .agg(
        smax("severity_score").alias("severity_score"),
        smax(col("has_thunderstorm").cast("int")).alias("has_thunderstorm_any"),
        smin("visibility_mi").alias("min_visibility_mi"),
        smin("ceiling_ft").alias("min_ceiling_ft"),
        smax("wind_gust_kt").alias("max_wind_gust_kt"),
        scount("*").alias("obs_count"),
    )
)
print(f"station-hour rows: {station_hour.count():,}")

silver_path = f"{SILVER_ROOT}/Tables/weather_station_hour_severity"
station_hour.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(silver_path)
print(f"wrote {silver_path}")

check = spark.read.format("delta").load(silver_path)
check_count = check.count()
print(f"weather_station_hour_severity row count: {check_count:,}")
station_summary = {
    r["station"]: {"hours": r["hours"], "max_severity": r["max_severity"]}
    for r in check.groupBy("station").agg(scount("*").alias("hours"), smax("severity_score").alias("max_severity")).collect()
}
check.groupBy("station").agg(scount("*").alias("hours"), smax("severity_score").alias("max_severity")).orderBy("station").show()
write_diag("silver_weather_bucket_write", {"status": "ok", "rows": check_count, "by_station": station_summary})
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


CELL_1_PARSE = _wrap_cell("silver_weather_parse", CELL_1_PARSE)
CELL_2_BUCKET_WRITE = _wrap_cell("silver_weather_bucket_write", CELL_2_BUCKET_WRITE)

CELLS = [CELL_0_SETUP, CELL_1_PARSE, CELL_2_BUCKET_WRITE]


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
            "displayName": "nb_silver_weather",
            "description": "Phase 4/6: parse raw METAR into severity scores, bucket to station-UTC-hour, write lh_silver.weather_station_hour_severity.",
        },
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="e.g. fabric/nb_silver_weather.Notebook")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "notebook-content.ipynb").write_text(
        json.dumps(build_notebook_json(), indent=2), encoding="utf-8"
    )
    (args.out / ".platform").write_text(json.dumps(build_platform_json(), indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
