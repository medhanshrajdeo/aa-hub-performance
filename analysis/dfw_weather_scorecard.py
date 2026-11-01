"""Signature analysis #4: weather-adjusted station scorecard.

"Model expected D0 given weather and volume, then report D0 above or
below expected per station per day. Leaders are not graded on
thunderstorms." (planning/project-plan.md)

Input: docs/analysis/station_day_d0_weather.csv, a pre-aggregated export
from wh_gold (fact_flight_leg joined to weather_station_hour_severity's
daily max severity) for the 6 AA hub stations -- see
scripts/fabric/build_analysis_export_notebook.py. Aggregation happens in
T-SQL where the data already lives; this script fits the model and
ranks days locally, on the small result.

Model: a day-level weighted OLS (weight = operated departures that day)
of D0 rate on weather severity (0-4), log scheduled-departure volume,
station fixed effects, and month fixed effects -- same style as
analysis #2's diff-in-diff (weather + volume + FE covariates). The
residual (actual - predicted D0 rate) is each day's "D0 above/below
expected given the conditions it actually had" score.

Usage:
    uv run python analysis/dfw_weather_scorecard.py \
        --input-csv docs/analysis/station_day_d0_weather.csv \
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
import statsmodels.formula.api as smf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input_csv)
    print(f"loaded {len(df):,} station-days across {df['station'].nunique()} stations")

    df["date"] = pd.to_datetime(df["date_key"], format="%Y%m%d")
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["d0_rate"] = df["d0_count"] / df["operated_departures"]
    df["log_volume"] = np.log(df["scheduled_departures"].clip(lower=1))

    weather_coverage = df["max_severity_score"].notna().mean()
    print(f"weather coverage: {weather_coverage:.1%}")
    df = df.dropna(subset=["max_severity_score", "d0_rate"])
    df = df[df["operated_departures"] > 0]
    print(f"{len(df):,} station-days usable after dropping missing weather/zero-ops days")

    model = smf.wls(
        "d0_rate ~ max_severity_score + log_volume + C(station) + C(month)",
        data=df,
        weights=df["operated_departures"],
    ).fit()

    print(model.summary())

    df["expected_d0_rate"] = model.predict(df)
    df["d0_vs_expected_pp"] = (df["d0_rate"] - df["expected_d0_rate"]) * 100

    out_cols = [
        "station", "date", "scheduled_departures", "operated_departures", "d0_count",
        "max_severity_score", "d0_rate", "expected_d0_rate", "d0_vs_expected_pp",
    ]
    scored = df[out_cols].sort_values(["station", "date"])
    scored.to_csv(args.out_dir / "station_day_scorecard.csv", index=False)

    weather_coef = model.params.get("max_severity_score")
    weather_p = model.pvalues.get("max_severity_score")
    print(f"\nweather severity coefficient: {weather_coef:.4f} pp D0 per severity point (p={weather_p:.4f})")

    print("\nworst 10 station-days vs expected (after controlling for weather/volume -- these are the ones a leader should actually own):")
    print(scored.nsmallest(10, "d0_vs_expected_pp").to_string(index=False))

    print("\nbest 10 station-days vs expected:")
    print(scored.nlargest(10, "d0_vs_expected_pp").to_string(index=False))

    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True, sharey=True)
    for ax, station in zip(axes.flat, sorted(df["station"].unique())):
        sub = scored[scored["station"] == station]
        ax.scatter(sub["date"], sub["d0_vs_expected_pp"], s=6, alpha=0.5)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_title(station)
        ax.tick_params(axis="x", rotation=45)
    fig.suptitle("D0 actual vs weather/volume-expected, by station (pp)")
    fig.tight_layout()
    fig.savefig(args.out_dir / "station_day_scorecard.png", dpi=150)
    print(f"\nwrote outputs to {args.out_dir}")


if __name__ == "__main__":
    main()
