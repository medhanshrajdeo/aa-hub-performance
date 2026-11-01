"""Signature analysis #6: passenger flow.

"TSA checkpoint peaks at DFW vs departure bank peaks (lag in minutes,
before and after rebank). CBP max wait at DFW by hour vs international
arrival waves (biometrics subject area)." (planning/project-plan.md)

The plan's original analysis #6 also asked for AA denied boardings vs
Delta/United and bag/wheelchair rates -- dropped, those are DOT Air
Travel Consumer Report metrics, scoped out by
docs/decisions/0012-dot-t100-scope-out.md.

Inputs (pre-aggregated exports from wh_gold, DFW only -- see
scripts/fabric/build_analysis_export_notebook.py):
  - tsa_dfw_hourly.csv: passengers screened by date + station-LOCAL hour
    (TSA's own convention), Jan-Sep 2026 (the TSA backfill window,
    chosen to straddle the April 2026 rebank).
  - dfw_station_hour.csv: scheduled/actual departures by date + UTC hour
    (fact_station_hour, reused as-is).
  - cbp_dfw_hourly.csv: Terminal D wait times by date + station-LOCAL
    HourRange (CBP's own convention, e.g. "0400 - 0500").
  - dfw_intl_arrivals_hourly.csv: count of arrivals into DFW by date +
    UTC hour, from an origin airport whose dim_airport.iso_region is
    outside the US (the international-arrival proxy -- BTS has no
    explicit domestic/international flag).

TSA and CBP hours are already station-local; fact_flight_leg-derived
hours are UTC. Converting the UTC side to DFW local time happens here,
in Python, via zoneinfo -- not as ad hoc T-SQL offset math, per
CLAUDE.md's rule for any code touching airport local time.

"Lag" is found via circular cross-correlation of the two 24-point
local-hour profiles (z-scored) across hour-of-day shifts -- this is a
24-hour cycle, not a one-shot time series, so a circular shift is the
right lag search, not scipy.signal.correlate on the raw series.

Usage:
    uv run python analysis/dfw_passenger_flow.py --out-dir docs/analysis
"""

from __future__ import annotations

import argparse
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DFW_TZ = ZoneInfo("America/Chicago")
REBANK_DATE = pd.Timestamp("2026-04-06")


def to_dfw_local(date_key: pd.Series, hour24_utc: pd.Series) -> tuple[pd.Series, pd.Series]:
    """UTC date_key + hour -> (DFW-local date, DFW-local hour-of-day)."""
    utc_ts = pd.to_datetime(date_key.astype(str), format="%Y%m%d", utc=True) + pd.to_timedelta(hour24_utc, unit="h")
    local_ts = utc_ts.dt.tz_convert(DFW_TZ)
    return local_ts.dt.tz_localize(None).dt.normalize(), local_ts.dt.hour


def best_circular_lag(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    """Hour-of-day shift (apply to b) that best aligns b's peak to a's, by
    z-scored circular correlation. Returns (lag_hours, correlation_at_lag)."""
    az = (a - a.mean()) / a.std()
    bz = (b - b.mean()) / b.std()
    best_lag, best_corr = 0, -np.inf
    for lag in range(-12, 13):
        shifted = np.roll(bz, lag)
        corr = float(np.dot(az, shifted) / len(az))
        if corr > best_corr:
            best_lag, best_corr = lag, corr
    return best_lag, best_corr


def tsa_vs_departures(tsa: pd.DataFrame, station_hour: pd.DataFrame, out_dir: Path) -> None:
    sh = station_hour.copy()
    sh["local_date"], sh["local_hour"] = to_dfw_local(sh["date_key"], sh["hour24"])

    tsa_dates = pd.to_datetime(tsa["date_key"].astype(str), format="%Y%m%d")
    tsa_min, tsa_max = tsa_dates.min(), tsa_dates.max()
    sh = sh[(sh["local_date"] >= tsa_min) & (sh["local_date"] <= tsa_max)]
    print(f"TSA window: {tsa_min.date()} to {tsa_max.date()} -- restricting departures to the same window")

    rows = []
    for label, tsa_cut, sh_cut in [
        ("full_window", tsa, sh),
        ("pre_rebank", tsa[tsa_dates < REBANK_DATE], sh[sh["local_date"] < REBANK_DATE]),
        ("post_rebank", tsa[tsa_dates >= REBANK_DATE], sh[sh["local_date"] >= REBANK_DATE]),
    ]:
        tsa_profile = tsa_cut.groupby("hour24")["passengers_screened"].mean().reindex(range(24), fill_value=0)
        dep_profile = sh_cut.groupby("local_hour")["scheduled_departures"].mean().reindex(range(24), fill_value=0)
        lag, corr = best_circular_lag(tsa_profile.values, dep_profile.values)
        rows.append({
            "window": label,
            "n_days_tsa": tsa_cut["date_key"].nunique(),
            "tsa_peak_hour": int(tsa_profile.idxmax()),
            "departures_peak_hour": int(dep_profile.idxmax()),
            "best_lag_hours_dep_after_tsa": lag,
            "corr_at_best_lag": round(corr, 3),
        })
        if label == "full_window":
            profile_out = pd.DataFrame({
                "local_hour": range(24),
                "mean_passengers_screened": tsa_profile.values,
                "mean_scheduled_departures": dep_profile.values,
            })
            profile_out.to_csv(out_dir / "passenger_flow_tsa_departures_by_hour.csv", index=False)

            fig, ax1 = plt.subplots(figsize=(9, 5))
            ax1.plot(range(24), tsa_profile.values, color="tab:blue", marker="o", label="TSA passengers screened")
            ax1.set_xlabel("DFW local hour of day")
            ax1.set_ylabel("mean passengers screened / hour", color="tab:blue")
            ax2 = ax1.twinx()
            ax2.plot(range(24), dep_profile.values, color="tab:orange", marker="s", label="scheduled departures")
            ax2.set_ylabel("mean scheduled departures / hour", color="tab:orange")
            ax1.set_title(f"DFW: TSA checkpoint throughput vs departure volume by local hour\n(best lag: departures {lag:+d}h vs TSA, corr={corr:.2f})")
            fig.tight_layout()
            fig.savefig(out_dir / "passenger_flow_tsa_departures.png", dpi=150)

    result = pd.DataFrame(rows)
    result.to_csv(out_dir / "passenger_flow_tsa_departures_lag.csv", index=False)
    print("\nTSA checkpoint throughput vs departure volume, by window:")
    print(result.to_string(index=False))


def cbp_vs_intl_arrivals(cbp: pd.DataFrame, intl: pd.DataFrame, out_dir: Path) -> None:
    cbp = cbp.copy()
    cbp["local_hour"] = cbp["hour_range"].str.slice(0, 2).astype(int)

    # Primary comparison: CBP's own flight_count/total_passengers against
    # its own wait times -- same table, no cross-source hour alignment
    # needed, so this is the more trustworthy of the two volume measures.
    wait_profile = cbp.groupby("local_hour")[["avg_wait_us", "avg_wait_non_us"]].mean().reindex(range(24))
    volume_profile = cbp.groupby("local_hour")[["flight_count", "total_passengers"]].mean().reindex(range(24))
    valid_hours = wait_profile["avg_wait_non_us"].dropna().index
    lag, corr = best_circular_lag(
        wait_profile.loc[valid_hours, "avg_wait_non_us"].values,
        volume_profile.loc[valid_hours, "total_passengers"].values,
    )
    peak_wait_hour = int(wait_profile["avg_wait_non_us"].idxmax())
    peak_volume_hour = int(volume_profile["total_passengers"].idxmax())
    print(f"CBP peak non-US wait hour: {peak_wait_hour}:00 local; peak CBP-reported passenger-volume hour: {peak_volume_hour}:00 local")
    print(f"corr(CBP wait, CBP passenger volume) at best circular lag {lag:+d}h: {corr:.3f}")

    # Secondary cross-check: an independent, BTS-derived international-
    # arrival proxy (origin airport outside the US), converted from UTC
    # to DFW local time -- tests whether CBP's own counts and an
    # outside source agree on the shape of the volume curve.
    intl = intl.copy()
    intl["local_date"], intl["local_hour"] = to_dfw_local(intl["date_key"], intl["hour24"])
    cbp_dates = pd.to_datetime(cbp["date_key"].astype(str), format="%Y%m%d")
    cbp_min, cbp_max = cbp_dates.min(), cbp_dates.max()
    intl_cut = intl[(intl["local_date"] >= cbp_min) & (intl["local_date"] <= cbp_max)]
    arrivals_profile = intl_cut.groupby("local_hour")["n_intl_arrivals"].mean().reindex(range(24), fill_value=0)
    cross_check_corr = float(np.corrcoef(volume_profile.loc[valid_hours, "total_passengers"], arrivals_profile.loc[valid_hours])[0, 1])
    print(f"cross-check: corr(CBP's own passenger volume, BTS-derived international-arrival proxy) = {cross_check_corr:.3f} (same-hour, no lag search)")

    profile_out = pd.DataFrame({
        "local_hour": wait_profile.index,
        "mean_avg_wait_us_min": wait_profile["avg_wait_us"].values,
        "mean_avg_wait_non_us_min": wait_profile["avg_wait_non_us"].values,
        "mean_cbp_flight_count": volume_profile["flight_count"].values,
        "mean_cbp_total_passengers": volume_profile["total_passengers"].values,
        "mean_bts_proxy_intl_arrivals": arrivals_profile.values,
    })
    profile_out.to_csv(out_dir / "passenger_flow_cbp_intl_arrivals_by_hour.csv", index=False)

    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax1.plot(wait_profile.index, wait_profile["avg_wait_non_us"], color="tab:red", marker="o", label="CBP avg wait, non-US")
    ax1.set_xlabel("DFW local hour of day")
    ax1.set_ylabel("avg CBP wait, non-US pax (min)", color="tab:red")
    ax2 = ax1.twinx()
    ax2.plot(volume_profile.index, volume_profile["total_passengers"], color="tab:green", marker="s", label="CBP-reported passengers")
    ax2.set_ylabel("mean CBP-reported passengers / hour", color="tab:green")
    ax1.set_title(f"DFW Terminal D: CBP wait vs CBP-reported passenger volume by local hour\n(peak wait {peak_wait_hour}:00 vs peak volume {peak_volume_hour}:00, corr={corr:.2f})")
    fig.tight_layout()
    fig.savefig(out_dir / "passenger_flow_cbp_intl_arrivals.png", dpi=150)

    pd.DataFrame([{
        "peak_wait_hour_local": peak_wait_hour,
        "peak_cbp_volume_hour_local": peak_volume_hour,
        "corr_wait_vs_volume_best_lag": round(corr, 3),
        "best_lag_hours": lag,
        "cross_check_corr_cbp_vs_bts_proxy": round(cross_check_corr, 3),
    }]).to_csv(out_dir / "passenger_flow_cbp_summary.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tsa = pd.read_csv(args.out_dir / "tsa_dfw_hourly.csv")
    station_hour = pd.read_csv(args.out_dir / "dfw_station_hour.csv")
    cbp = pd.read_csv(args.out_dir / "cbp_dfw_hourly.csv")
    intl = pd.read_csv(args.out_dir / "dfw_intl_arrivals_hourly.csv")

    tsa_vs_departures(tsa, station_hour, args.out_dir)
    cbp_vs_intl_arrivals(cbp, intl, args.out_dir)
    print(f"\nwrote outputs to {args.out_dir}")


if __name__ == "__main__":
    main()
