# Fabric notebook source


# CELL ********************

# Phase 4: land raw hourly METAR observations for AA's six hub airports
# into lh_bronze. Bronze stays RAW (no severity scoring here -- that's a
# silver-layer transform via hubops.metar, same as BTS times aren't
# parsed until silver) -- just station, timestamp, and the raw METAR text,
# plus lineage columns, matching every other bronze table's contract.

import urllib.request

WORKSPACE_ID = "16b2a9c6-6418-485f-865f-c47442b8b6c6"
LH_BRONZE_ID = "09a8b1f5-f33c-4eed-912d-40b9cbdc9b06"
ABFSS_ROOT = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LH_BRONZE_ID}"

# AA's six primary hubs (not focus cities like LAX/JFK) -- matches the
# plan's "IEM hourly METAR for all AA hubs" phase 4 scope.
STATIONS = ["DFW", "CLT", "MIA", "ORD", "PHX", "PHL"]
START = "2023-01-01"
END = "2026-08-31"


def fetch_station_metar(station: str) -> str:
    y1, m1, d1 = START.split("-")
    y2, m2, d2 = END.split("-")
    url = (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={station}&data=metar&year1={y1}&month1={m1}&day1={d1}"
        f"&year2={y2}&month2={m2}&day2={d2}&tz=UTC&format=onlycomma"
        "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3"
    )
    print(f"fetching {station}: {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read().decode("utf-8")


raw_by_station = {s: fetch_station_metar(s) for s in STATIONS}
for s, text in raw_by_station.items():
    print(f"{s}: {len(text.splitlines())} lines")


# CELL ********************

# land each station's raw CSV response into Files/ for traceability,
# matching every other source's landing-before-transform pattern
for station, text in raw_by_station.items():
    local_path = f"/tmp/metar_{station}.csv"
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(text)
    dest = f"{ABFSS_ROOT}/Files/weather/{station}_{START}_to_{END}.csv"
    notebookutils.fs.cp(f"file:{local_path}", dest)
    print(f"landed {station} to {dest}")


# CELL ********************

from datetime import datetime, timezone
from pyspark.sql import Row
from pyspark.sql.functions import current_timestamp, lit

rows = []
for station, text in raw_by_station.items():
    lines = text.splitlines()
    header = lines[0].split(",") if lines else []
    for line in lines[1:]:
        parts = line.split(",", 2)  # station,valid,metar -- metar text may contain commas? it doesn't, but keep maxsplit safe
        if len(parts) < 3:
            continue
        rows.append(Row(station=parts[0], valid_utc=parts[1], metar=parts[2]))

df = spark.createDataFrame(rows)
df = df.withColumn("_source", lit("iowa_environmental_mesonet_asos")).withColumn(
    "_loaded_at", current_timestamp()
)
print(f"total rows across {len(STATIONS)} stations: {df.count():,}")
df.groupBy("station").count().orderBy("station").show()


# CELL ********************

bronze_table_path = f"{ABFSS_ROOT}/Tables/weather_metar_raw"
df.write.format("delta").partitionBy("station").mode("overwrite").save(bronze_table_path)
print(f"wrote {bronze_table_path}")


# CELL ********************

check = spark.read.format("delta").load(bronze_table_path)
print(f"weather_metar_raw row count: {check.count():,}")
check.groupBy("station").count().orderBy("station").show()
check.show(5, truncate=False)

