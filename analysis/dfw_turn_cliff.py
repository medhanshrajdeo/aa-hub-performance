"""Signature analysis #3: split station-caused from inbound-caused delay,
and find the scheduled turn length where D0 falls off a cliff per fleet.

Input: docs/analysis/turn_cliff_agg.csv, a pre-aggregated export from
wh_gold.fact_aircraft_turn (DFW only, filtered to on-time-or-early
inbound arrivals -- see scripts/fabric/build_analysis_export_notebook.py
for exactly how it's built). Aggregation happens in T-SQL where the
27.8M-row fact_flight_leg/21M-row fact_aircraft_turn data already lives;
this script does the actual statistics locally on the small result.

Filtering to on-time-or-early inbound legs isolates the station's own
effect: when the inbound was already late, any outbound delay could be
inherited from that, not caused by a tight turn at the station. Among
turns where the inbound did NOT contribute a delay, an outbound D0 miss
is attributable to the turn process itself -- schedule, staffing, ground
equipment, not something the inbound flight's crew or an upstream ATC
delay caused. This is "the accountability view: what a DFW leader can
actually fix" per the plan.

The "cliff" for a fleet is the scheduled-turn-length bucket boundary
where outbound D0 rate drops most sharply as turn length decreases --
found as the single steepest increase in D0 rate between consecutive
5-minute buckets (walking from short to long turns), among buckets with
at least MIN_BUCKET_N turns (small buckets are too noisy to trust).

Usage:
    uv run python analysis/dfw_turn_cliff.py \
        --input-csv docs/analysis/turn_cliff_agg.csv \
        --out-dir docs/analysis
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

MIN_BUCKET_N = 50


def find_cliff(fleet_df: pd.DataFrame) -> dict | None:
    """Among consecutive turn-length buckets with enough sample size,
    find the boundary where D0 rate rises most sharply as scheduled
    turn length increases (equivalently: falls off a cliff as turn
    length decreases below that point)."""
    usable = fleet_df[fleet_df["n_turns"] >= MIN_BUCKET_N].sort_values("turn_bucket_min").reset_index(drop=True)
    if len(usable) < 2:
        return None

    usable["d0_rate"] = usable["n_d0"] / usable["n_turns"]
    usable["d0_rate_diff"] = usable["d0_rate"].diff()

    best_idx = usable["d0_rate_diff"].idxmax()
    if pd.isna(usable.loc[best_idx, "d0_rate_diff"]):
        return None

    return {
        "cliff_turn_min": int(usable.loc[best_idx, "turn_bucket_min"]),
        "d0_rate_below": round(usable.loc[best_idx - 1, "d0_rate"], 4) if best_idx > 0 else None,
        "d0_rate_at_or_above": round(usable.loc[best_idx, "d0_rate"], 4),
        "jump_pp": round(usable.loc[best_idx, "d0_rate_diff"] * 100, 2),
        "n_turns_below": int(usable.loc[best_idx - 1, "n_turns"]) if best_idx > 0 else None,
        "n_turns_at_or_above": int(usable.loc[best_idx, "n_turns"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    agg = pd.read_csv(args.input_csv)
    print(f"loaded {len(agg)} fleet x turn-bucket rows, {agg['fleet_family'].nunique()} fleets")

    fleet_totals = agg.groupby("fleet_family")["n_turns"].sum().sort_values(ascending=False)
    top_fleets = fleet_totals[fleet_totals >= MIN_BUCKET_N * 3].index.tolist()
    print(f"fleets with enough data ({MIN_BUCKET_N * 3}+ on-time-inbound turns): {len(top_fleets)}")

    cliff_rows = []
    for fleet in top_fleets:
        fleet_df = agg[agg["fleet_family"] == fleet]
        cliff = find_cliff(fleet_df)
        if cliff is None:
            continue
        cliff_rows.append({"fleet_family": fleet, "n_turns_total": int(fleet_totals[fleet]), **cliff})

    cliff_df = pd.DataFrame(cliff_rows).sort_values("n_turns_total", ascending=False)
    cliff_df.to_csv(args.out_dir / "turn_cliff_by_fleet.csv", index=False)
    print("\n" + cliff_df.to_string(index=False))

    # Plot the top 6 fleets by volume: D0 rate vs scheduled turn length.
    plot_fleets = fleet_totals.index[:6].tolist()
    fig, ax = plt.subplots(figsize=(10, 6))
    for fleet in plot_fleets:
        fleet_df = agg[(agg["fleet_family"] == fleet) & (agg["n_turns"] >= MIN_BUCKET_N)].sort_values("turn_bucket_min")
        if fleet_df.empty:
            continue
        d0_rate = fleet_df["n_d0"] / fleet_df["n_turns"]
        ax.plot(fleet_df["turn_bucket_min"], d0_rate * 100, marker="o", markersize=3, label=fleet)
    ax.set_xlabel("scheduled turn length (min, 5-min buckets)")
    ax.set_ylabel("outbound D0 rate (%), on-time-inbound turns only")
    ax.set_title("DFW: outbound D0 vs scheduled turn length, by fleet\n(isolating station-caused delay -- inbound was on-time or early)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(args.out_dir / "dfw_turn_cliff_by_fleet.png", dpi=150)
    print(f"\nwrote outputs to {args.out_dir}")


if __name__ == "__main__":
    main()
