"""Signature analysis #5: does chasing D0 buy A14?

"Compare A14 for flights pushed at 0 vs 1 to 5 minutes late, controlling
for route and weather. Pair it with the hub throughput metric. Handle
this one carefully in the interview: it questions a metric they pay on,
so frame it as finding where D0 pays off most." (planning/project-plan.md)

Input: docs/analysis/d0_a14_tradeoff_agg.csv, a pre-aggregated export
from wh_gold (fact_flight_leg joined to fact_station_hour for its
already-computed weather_severity_score -- see
scripts/fabric/build_analysis_export_notebook.py) for the 6 AA hub
stations, grouped by origin, dest, a dep-delay bucket (exactly 0 min vs
1-5 min late) and weather severity. Restricted to is_cancelled = 0 and
dep_delay_min in [0, 5] -- the comparison the plan asks for is narrowly
"push it out 1-5 min late instead of exactly on time", not "any delay".

Model: a weighted OLS (weight = n flights in that cell) of A14 rate on
the dep-delay bucket, weather severity, and route fixed effects
(same style as analysis #4's weather/volume model) -- route FE because
A14 odds vary enormously by stage length and destination congestion
regardless of how a flight was pushed. Routes with under
MIN_ROUTE_N total flights are dropped before fitting (too thin to
support their own fixed effect).

Usage:
    uv run python analysis/dfw_d0_a14_tradeoff.py \\
        --input-csv docs/analysis/d0_a14_tradeoff_agg.csv \\
        --out-dir docs/analysis
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import statsmodels.formula.api as smf

MIN_ROUTE_N = 100


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input_csv)
    df["route"] = df["station"] + "-" + df["dest"]
    print(f"loaded {len(df):,} station x route x bucket x severity cells, {df['route'].nunique()} routes")

    totals = df.groupby("dep_bucket")[["n", "n_a14"]].sum()
    totals["a14_rate"] = totals["n_a14"] / totals["n"]
    print("\nraw (unadjusted) A14 rate by dep-delay bucket:")
    print(totals.to_string())

    route_totals = df.groupby("route")["n"].sum()
    keep_routes = route_totals[route_totals >= MIN_ROUTE_N].index
    model_df = df[df["route"].isin(keep_routes)].dropna(subset=["weather_severity_score"]).copy()
    print(f"\n{len(model_df):,} cells on {model_df['route'].nunique()} routes with >= {MIN_ROUTE_N} flights and known weather, after dropping thin/no-weather cells")

    model_df["a14_rate"] = model_df["n_a14"] / model_df["n"]

    model = smf.wls(
        "a14_rate ~ C(dep_bucket) + weather_severity_score + C(route)",
        data=model_df,
        weights=model_df["n"],
    ).fit()

    coef_name = "C(dep_bucket)[T.1_5]"
    lift = model.params.get(coef_name)
    p = model.pvalues.get(coef_name)
    ci = model.conf_int().loc[coef_name] if coef_name in model.params.index else None
    print(f"\nA14 effect of pushing 1-5 min late vs exactly 0: {lift * 100:+.2f} pp (p={p:.4f})"
          + (f", 95% CI [{ci[0] * 100:+.2f}, {ci[1] * 100:+.2f}] pp" if ci is not None else ""))
    print(f"weather severity coefficient: {model.params.get('weather_severity_score', float('nan')) * 100:+.2f} pp per severity point (p={model.pvalues.get('weather_severity_score', float('nan')):.4f})")

    summary_rows = [{
        "comparison": "1_5_min_late_vs_0",
        "n_flights_0": int(totals.loc["0", "n"]),
        "n_flights_1_5": int(totals.loc["1_5", "n"]),
        "raw_a14_rate_0": round(totals.loc["0", "a14_rate"], 4),
        "raw_a14_rate_1_5": round(totals.loc["1_5", "a14_rate"], 4),
        "route_weather_adjusted_a14_lift_pp": round(lift * 100, 3),
        "p_value": round(p, 5),
        "n_cells_in_model": len(model_df),
        "n_routes_in_model": model_df["route"].nunique(),
    }]
    pd.DataFrame(summary_rows).to_csv(args.out_dir / "d0_a14_tradeoff_summary.csv", index=False)

    by_station = model_df.groupby(["station", "dep_bucket"]).apply(
        lambda g: pd.Series({"n": g["n"].sum(), "n_a14": g["n_a14"].sum(), "a14_rate": g["n_a14"].sum() / g["n"].sum()}),
        include_groups=False,
    ).reset_index()
    by_station.to_csv(args.out_dir / "d0_a14_tradeoff_by_station.csv", index=False)
    print("\nraw A14 rate by station and bucket:")
    print(by_station.to_string(index=False))

    fig, ax = plt.subplots(figsize=(8, 5))
    pivot = by_station.pivot(index="station", columns="dep_bucket", values="a14_rate") * 100
    pivot = pivot.sort_values("0", ascending=False)
    pivot.plot(kind="bar", ax=ax)
    ax.set_ylabel("A14 rate (%)")
    ax.set_xlabel("AA hub station")
    ax.set_title("A14 rate: departed exactly on time vs 1-5 min late, by station")
    ax.legend(["departed 0 min late", "departed 1-5 min late"])
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(args.out_dir / "d0_a14_tradeoff_by_station.png", dpi=150)
    print(f"\nwrote outputs to {args.out_dir}")


if __name__ == "__main__":
    main()
