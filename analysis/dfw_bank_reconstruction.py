"""Signature analysis #1: reconstruct DFW's bank structure from the public
schedule, before vs after the April 2026 rebank.

Local prototype -- this is the logic that becomes
`fabric/nb_analysis_rebank.Notebook/` once Phase 6 moves it into the
pipeline. Run it against the raw monthly BTS Marketing Carrier CSVs (see
contracts/bts_marketing_ontime.yaml for where to get them); it does not
depend on anything already loaded into Fabric.

Usage:
    uv run python analysis/dfw_bank_reconstruction.py \
        --pre /path/to/On_Time_..._2026_3.csv --pre-label "2026-03 (pre-rebank)" \
        --post /path/to/On_Time_..._2026_8.csv --post-label "2026-08 (post-rebank)" \
        --out-dir docs/analysis
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hubops.banks import detect_bank_windows, slot_to_hhmm

SLOT_MIN = 15
N_SLOTS = (24 * 60) // SLOT_MIN
# Tue/Wed/Thu only: avoids Monday ramp-up / Friday leisure-travel effects
# and weekend schedule differences, per the plan's "typical weekday" ask.
TYPICAL_WEEKDAY_CODES = [2, 3, 4]


def hhmm_to_slot(raw) -> int | None:
    if pd.isna(raw):
        return None
    text = str(raw)
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        return None
    value = int(text)
    if value == 2400:
        value = 0
    hour, minute = divmod(value, 100)
    if hour > 23 or minute > 59:
        return None
    return ((hour * 60) + minute) // SLOT_MIN


def load_slot_counts(csv_path: Path, direction: str) -> tuple[np.ndarray, int]:
    """direction: 'dep' for DFW-origin scheduled departures, 'arr' for
    DFW-dest scheduled arrivals. Counts scheduled flights (including
    cancellations -- a cancelled flight still occupied a planned slot).
    Returns (avg count per 15-min slot across sampled weekdays, n weekdays)."""
    usecols = [
        "FlightDate", "DayOfWeek", "Marketing_Airline_Network", "Origin", "Dest",
        "CRSDepTime", "CRSArrTime", "Duplicate",
    ]
    counts = np.zeros(N_SLOTS)
    dates_seen: set[str] = set()
    time_col = "CRSDepTime" if direction == "dep" else "CRSArrTime"
    airport_col = "Origin" if direction == "dep" else "Dest"

    for chunk in pd.read_csv(csv_path, usecols=usecols, chunksize=200_000, low_memory=False):
        chunk = chunk[chunk["Duplicate"] != "Y"]
        chunk = chunk[chunk["Marketing_Airline_Network"] == "AA"]
        chunk = chunk[chunk["DayOfWeek"].isin(TYPICAL_WEEKDAY_CODES)]
        sub = chunk[chunk[airport_col] == "DFW"]

        for _, row in sub.iterrows():
            slot = hhmm_to_slot(row[time_col])
            if slot is not None:
                counts[slot] += 1
            dates_seen.add(row["FlightDate"])

    n_days = len(dates_seen) or 1
    return counts / n_days, len(dates_seen)


def bank_windows_to_rows(period_label: str, direction: str, series: np.ndarray) -> list[dict]:
    windows = sorted(detect_bank_windows(series.tolist()), key=lambda w: w.peak_slot)
    return [
        {
            "station": "DFW",
            "period": period_label,
            "direction": direction,
            "bank_number": w.bank_number,
            "peak_time": slot_to_hhmm(w.peak_slot),
            "peak_avg_flights": round(w.peak_count, 1),
            "window_start": slot_to_hhmm(w.start_slot),
            "window_end": slot_to_hhmm(w.end_slot),
        }
        for w in windows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre", type=Path, required=True, help="pre-rebank month's BTS CSV")
    parser.add_argument("--pre-label", required=True)
    parser.add_argument("--post", type=Path, required=True, help="post-rebank month's BTS CSV")
    parser.add_argument("--post-label", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    series_by_period = {}
    rows: list[dict] = []
    for label, csv_path in [(args.pre_label, args.pre), (args.post_label, args.post)]:
        dep, n_dep = load_slot_counts(csv_path, "dep")
        arr, n_arr = load_slot_counts(csv_path, "arr")
        print(f"{label}: sampled {n_dep} dep-days, {n_arr} arr-days")
        series_by_period[label] = {"dep": dep, "arr": arr}
        rows += bank_windows_to_rows(label, "departure", dep)
        rows += bank_windows_to_rows(label, "arrival", arr)

    out_csv = args.out_dir / "dim_bank_prototype.csv"
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    print(f"wrote {len(rows)} bank-window rows to {out_csv}")

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    slot_hours = np.arange(N_SLOTS) * SLOT_MIN / 60
    colors = ["#4C72B0", "#DD8452"]

    for ax, direction, title in [
        (axes[0], "dep", "AA scheduled departures at DFW, avg per 15-min slot (Tue/Wed/Thu)"),
        (axes[1], "arr", "AA scheduled arrivals at DFW, avg per 15-min slot (Tue/Wed/Thu)"),
    ]:
        for color, label in zip(colors, [args.pre_label, args.post_label]):
            ax.plot(slot_hours, series_by_period[label][direction], label=label,
                     color=color, linewidth=1.8)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("avg flights / 15-min slot")
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 2))
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25)

    axes[1].set_xlabel("hour of day (local)")
    fig.suptitle("DFW bank structure before vs after the April 2026 rebank", fontsize=13, y=0.995)
    fig.tight_layout()

    out_png = args.out_dir / "dfw_bank_structure_pre_post_rebank.png"
    fig.savefig(out_png, dpi=150)
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
