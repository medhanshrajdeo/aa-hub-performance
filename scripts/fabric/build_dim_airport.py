"""Build dim_airport: airport coordinates + resolved IANA timezone for
every US (+ territory) airport that appears in real BTS flight data.

Needed to unblock Phase 2 silver -- hubops.bts_times.resolve_leg_times
needs each leg's origin/destination timezone, and BTS covers ~370
distinct airports, far more than the handful hardcoded for the two
completed signature analyses. See contracts/dim_airport.yaml for the two
real data-quality decisions this required (OurAirports' unreliable
scheduled_service flag, and the PBI/DJT Palm Beach rename), found by
validating coverage against the real distinct Origin/Dest codes in BTS
data rather than trusting the reference source's own flags.

Usage:
    uv run python scripts/fabric/build_dim_airport.py \
        --ourairports-csv /path/to/airports.csv \
        --out docs/reference/dim_airport.csv

Download the source first:
    curl -o airports.csv https://davidmegginson.github.io/ourairports-data/airports.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from hubops.airports import resolve_airport_tz

AA_HUBS = {"DFW", "CLT", "MIA", "ORD", "PHX", "PHL"}

# US + its territories -- BTS treats these as domestic, OurAirports codes
# them by their own iso_country (PR/GU/AS/MP/VI), not "US".
US_AND_TERRITORIES = {"US", "PR", "GU", "AS", "MP", "VI"}

# BTS still reports flights under historical IATA codes that OurAirports
# has since reassigned after a real-world airport rename. Code -> (real
# current `ident` to borrow coordinates/tz from, reason).
RENAMED_CODE_ALIASES = {
    "PBI": ("KPBI", "Palm Beach Intl renamed; BTS still files flights as PBI"),
}

FIELDNAMES = ["iata", "name", "municipality", "iso_region", "latitude", "longitude", "tz", "is_aa_hub", "note"]


def build(ourairports_csv: Path) -> list[dict]:
    with open(ourairports_csv, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    by_ident = {row["ident"]: row for row in all_rows}
    rows_by_iata: dict[str, dict] = {}

    for row in all_rows:
        if row["iso_country"] not in US_AND_TERRITORIES:
            continue
        iata = row["iata_code"].strip()
        if not iata:
            continue
        try:
            lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
        except ValueError:
            continue
        try:
            tz = resolve_airport_tz(lat, lon)
        except ValueError:
            continue
        rows_by_iata[iata] = {
            "iata": iata,
            "name": row["name"],
            "municipality": row["municipality"],
            "iso_region": row["iso_region"].split("-", 1)[-1],
            "latitude": lat,
            "longitude": lon,
            "tz": tz,
            "is_aa_hub": iata in AA_HUBS,
            "note": "",
        }

    for bts_code, (ident, reason) in RENAMED_CODE_ALIASES.items():
        if bts_code in rows_by_iata:
            continue
        src = by_ident.get(ident)
        if src is None:
            print(f"WARNING: alias source {ident} not found for {bts_code}")
            continue
        lat, lon = float(src["latitude_deg"]), float(src["longitude_deg"])
        rows_by_iata[bts_code] = {
            "iata": bts_code,
            "name": src["name"],
            "municipality": src["municipality"],
            "iso_region": src["iso_region"].split("-", 1)[-1],
            "latitude": lat,
            "longitude": lon,
            "tz": resolve_airport_tz(lat, lon),
            "is_aa_hub": bts_code in AA_HUBS,
            "note": reason,
        }

    return sorted(rows_by_iata.values(), key=lambda r: r["iata"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ourairports-csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = build(args.ourairports_csv)
    print(f"{len(rows)} airports in dim_airport")
    hubs_found = {r["iata"] for r in rows if r["is_aa_hub"]}
    print(f"AA hubs found: {sorted(hubs_found)} (expected {sorted(AA_HUBS)})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
