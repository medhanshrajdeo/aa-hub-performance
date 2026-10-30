"""Download and parse every TSA weekly throughput PDF listed in
docs/fabric_backfill_tsa_weeks.txt into one combined CSV, ready to upload
to Fabric and land via build_tsa_final_notebook.py's notebook.

Runs LOCALLY, not in Fabric -- see ADR 0008 (Fabric's batch RunNotebook
jobs can't %pip install pdfplumber, and the parsing itself is
single-threaded driver-side work regardless of where it runs, so nothing
is lost by doing it here). Each weekly PDF takes a few minutes to parse
(pdfplumber walking ~1,300+ pages); the full 35-week run takes roughly
2-3 hours.

Resumable: if interrupted (this is a real possibility for a multi-hour
local run -- it happened once during development, killed by the host for
memory pressure, and resumed cleanly), re-running skips any week whose
rows are already present in the output CSV and picks up where it left
off, rather than redoing finished work.

Usage:
    uv run python scripts/fabric/download_and_parse_tsa_weeks.py \
        --weeks-file docs/fabric_backfill_tsa_weeks.txt \
        --pdf-dir /some/local/scratch/dir \
        --out-csv /some/local/scratch/tsa_checkpoint_hour_all.csv

Then upload --out-csv to Fabric (OneLake path Files/tsa/
tsa_checkpoint_hour_all.csv) via PowerShell (az rest silently drops bytes
on binary uploads -- see ADR 0006) and run nb_bronze_tsa.
"""

from __future__ import annotations

import argparse
import csv
import time
import urllib.request
from pathlib import Path

from hubops.tsa_pdf import parse_tsa_pdf

USER_AGENT = "Mozilla/5.0 (compatible; dfw-hub-performance-analytics-research/1.0)"
CSV_COLUMNS = ["week_start", "date", "hour", "iata", "airport_name", "city", "state", "checkpoint", "pax"]


def load_weeks(weeks_file: Path) -> list[tuple[str, str]]:
    weeks = []
    with open(weeks_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            week_start, url = line.split("\t")
            weeks.append((week_start, url))
    return weeks


def load_done_weeks(out_csv: Path) -> set[str]:
    if not out_csv.exists():
        return set()
    done = set()
    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            done.add(row["week_start"])
    return done


def download_pdf(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks-file", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    args = parser.parse_args()

    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    weeks = load_weeks(args.weeks_file)
    done = load_done_weeks(args.out_csv)
    remaining = [(w, u) for w, u in weeks if w not in done]

    print(f"{len(done)} weeks already done, {len(remaining)} remaining")
    if not remaining:
        return

    write_header = not args.out_csv.exists()
    with open(args.out_csv, "a", newline="", encoding="utf-8") as out_f:
        writer = csv.writer(out_f)
        if write_header:
            writer.writerow(CSV_COLUMNS)

        for i, (week_start, url) in enumerate(remaining):
            pdf_path = args.pdf_dir / f"{week_start}.pdf"
            t0 = time.time()
            if not pdf_path.exists():
                print(f"[{i+1}/{len(remaining)}] {week_start}: downloading...", flush=True)
                download_pdf(url, pdf_path)

            print(f"[{i+1}/{len(remaining)}] {week_start}: parsing...", flush=True)
            records = parse_tsa_pdf(pdf_path)
            for r in records:
                writer.writerow(
                    [week_start, r.date.isoformat(), r.hour, r.iata, r.airport_name, r.city, r.state, r.checkpoint, r.pax]
                )
            out_f.flush()
            print(f"[{i+1}/{len(remaining)}] {week_start}: {len(records):,} rows in {time.time()-t0:.0f}s", flush=True)

    print(f"\nall remaining weeks done, wrote to {args.out_csv}")


if __name__ == "__main__":
    main()
