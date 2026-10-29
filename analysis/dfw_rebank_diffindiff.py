"""Signature analysis #2: did the April 2026 DFW rebank work, net of weather?

Local prototype -- becomes `fabric/nb_analysis_rebank.Notebook/` in Phase 6.
Treated unit: AA departures from DFW. Control unit: AA departures from ORD
(an AA hub with no April 2026 structural change). Outcomes: D0, A14, TXO.
Covariates: origin weather severity (hubops.metar), scheduled departure
volume in that hub-date-hour, month and hour fixed effects. Standard errors
clustered by date.

The rebank changeover week is NOT assumed -- it's detected from the
schedule itself: each week's DFW departure-time histogram (15-min slots) is
correlated against a canonical "post" reference (August 2026); the first
jump that stays sustained above 0.80 for every remaining week is the
changeover. See `find_rebank_week()`. On the real data this lands on
2026-W15 (April 6-12, 2026) -- squarely inside "April 2026" per the plan,
and far more convincing than a smaller, separate shift detected around
week 8 that turns out NOT to be sustained.

Known scope limitations (see README "Limits"):
  - Single control hub (ORD) -- a real production version would add CLT
    and/or year-over-year DFW as additional controls, per the plan.
  - Destination weather severity is not joined (would require weather data
    for every US destination airport AA/DFW touches, not just the hub
    pair) -- only origin (DFW/ORD) weather is used as a covariate.
  - DFW and ORD are both America/Chicago, which simplified the weather-join
    timezone handling; a third control hub in a different time zone would
    need the same local-hour-bucketing logic, already written in
    `_weather_lookup`, just parameterized per station.

Usage:
    uv run python analysis/dfw_rebank_diffindiff.py \
        --bts-dir /path/to/monthly/bts/csvs \
        --weather-dir /path/to/station/metar/csvs \
        --months 1 2 3 4 5 6 7 8 \
        --out-dir docs/analysis
"""

from __future__ import annotations

import argparse
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from hubops.bts_times import parse_hhmm_to_minutes
from hubops.metar import weather_severity_score

warnings.filterwarnings("ignore", category=FutureWarning)

TREATED_HUB = "DFW"
CONTROL_HUB = "ORD"
HUB_TZ = "America/Chicago"  # both hubs -- see module docstring limitation
SLOT_MIN = 15
N_SLOTS = (24 * 60) // SLOT_MIN


def _safe_parse_hhmm(raw) -> int | None:
    try:
        return parse_hhmm_to_minutes(raw)
    except ValueError:
        return None  # malformed field in raw BTS data -- treat like missing


def hhmm_to_hour(raw) -> int | None:
    minutes = _safe_parse_hhmm(raw)
    return None if minutes is None else (minutes // 60) % 24


def hhmm_to_slot(raw) -> int | None:
    minutes = _safe_parse_hhmm(raw)
    return None if minutes is None else (minutes // SLOT_MIN) % N_SLOTS


def load_weather_severity(weather_dir: Path, station: str) -> dict[tuple, int]:
    """(date, local_hour) -> max severity score among that hour's METARs."""
    df = pd.read_csv(weather_dir / f"{station}_2026.csv")
    df = df[df["metar"].notna()]
    tz, utc = ZoneInfo(HUB_TZ), ZoneInfo("UTC")
    severity: dict[tuple, int] = {}
    for _, row in df.iterrows():
        try:
            valid_utc = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=utc)
        except ValueError:
            continue
        local = valid_utc.astimezone(tz)
        key = (local.date(), local.hour)
        severity[key] = max(severity.get(key, 0), weather_severity_score(row["metar"]))
    return severity


def load_panel(bts_dir: Path, months: list[int], weather: dict[str, dict]) -> pd.DataFrame:
    usecols = [
        "FlightDate", "DayOfWeek", "Marketing_Airline_Network", "Origin", "Dest",
        "CRSDepTime", "DepTime", "DepDelay", "TaxiOut", "CRSArrTime", "ArrTime",
        "ArrDelay", "Cancelled", "Diverted", "Duplicate",
    ]
    frames = []
    for m in months:
        path = bts_dir / (
            f"On_Time_Marketing_Carrier_On_Time_Performance_(Beginning_January_2018)_2026_{m}.csv"
        )
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=300_000, low_memory=False):
            chunk = chunk[chunk["Duplicate"] != "Y"]
            chunk = chunk[chunk["Marketing_Airline_Network"] == "AA"]
            chunk = chunk[chunk["Origin"].isin([TREATED_HUB, CONTROL_HUB])]
            if len(chunk):
                frames.append(chunk.copy())

    df = pd.concat(frames, ignore_index=True)
    df["flight_date"] = pd.to_datetime(df["FlightDate"])
    df["dep_hour"] = df["CRSDepTime"].apply(hhmm_to_hour)
    df["dep_slot"] = df["CRSDepTime"].apply(hhmm_to_slot)
    df["month"] = df["flight_date"].dt.month
    df["treated"] = (df["Origin"] == TREATED_HUB).astype(int)
    is_cancelled = df["Cancelled"] == 1
    is_diverted = df["Diverted"] == 1

    df["d0"] = np.where(is_cancelled, np.nan, (df["DepDelay"] <= 0).astype(float))
    df["a14"] = np.where(is_cancelled | is_diverted, np.nan, (df["ArrDelay"] <= 14).astype(float))
    df["txo"] = np.where(is_cancelled, np.nan, df["TaxiOut"].clip(upper=180))

    def _lookup(row):
        key = (row["flight_date"].date(), row["dep_hour"])
        return weather.get(row["Origin"], {}).get(key, np.nan)

    df["weather_severity"] = df.apply(_lookup, axis=1)

    hourly_volume = (
        df.groupby(["Origin", "flight_date", "dep_hour"]).size().rename("hour_volume").reset_index()
    )
    df = df.merge(hourly_volume, on=["Origin", "flight_date", "dep_hour"], how="left")
    return df


def find_rebank_week(df: pd.DataFrame, canonical_post_month: int = 8) -> int:
    """Detect the ISO week of the structural schedule break via correlation
    of each week's DFW departure-slot histogram against a canonical
    "fully post-rebank" month's histogram. See module docstring."""
    dfw = df[df["Origin"] == TREATED_HUB].copy()
    iso = dfw["flight_date"].dt.isocalendar()
    dfw["iso_week"] = iso["week"].astype(int)

    def hist_for(sub: pd.DataFrame) -> np.ndarray:
        h = np.zeros(N_SLOTS)
        for s in sub["dep_slot"].dropna():
            h[int(s)] += 1
        n = sub["flight_date"].nunique()
        return h / max(n, 1)

    post_hist = hist_for(dfw[dfw["month"] == canonical_post_month])
    weeks = sorted(dfw["iso_week"].unique())
    series = []
    for wk in weeks:
        h = hist_for(dfw[dfw["iso_week"] == wk])
        c = np.corrcoef(h, post_hist)[0, 1] if h.sum() > 0 else np.nan
        series.append((wk, c))

    for i in range(1, len(series)):
        wk, c = series[i]
        prev_c = series[i - 1][1]
        if np.isnan(c) or np.isnan(prev_c):
            continue
        jump = c - prev_c
        rest = [cc for _, cc in series[i:] if not np.isnan(cc)]
        if jump > 0.1 and all(cc > 0.80 for cc in rest):
            return wk
    raise RuntimeError("no sustained structural break found in the data")


def run_diff_in_diff(df: pd.DataFrame, rebank_week: int, out_dir: Path) -> pd.DataFrame:
    iso = df["flight_date"].dt.isocalendar()
    df = df.copy()
    df["iso_week"] = iso["week"].astype(int)
    df["week_relative"] = df["iso_week"] - rebank_week
    df = df[(df["week_relative"] != 0) & (df["iso_week"] != 1)]  # exclude transition + holiday noise
    df["post"] = (df["week_relative"] > 0).astype(int)
    df["day_cluster"] = df["flight_date"].astype(str)

    results = []
    for outcome, label in [("d0", "D0"), ("a14", "A14"), ("txo", "TXO (minutes)")]:
        sub = df.dropna(subset=[outcome, "weather_severity", "hour_volume"])
        formula = f"{outcome} ~ treated * post + weather_severity + hour_volume + C(month) + C(dep_hour)"
        model = smf.ols(formula, data=sub).fit(
            cov_type="cluster", cov_kwds={"groups": sub["day_cluster"]}
        )
        ci_low, ci_high = model.conf_int().loc["treated:post"]
        results.append({
            "hub": TREATED_HUB, "metric": label,
            "model": f"diff-in-diff vs {CONTROL_HUB}, rebank week {rebank_week}",
            "estimate": round(model.params["treated:post"], 4),
            "se": round(model.bse["treated:post"], 4),
            "ci_low": round(ci_low, 4), "ci_high": round(ci_high, 4),
            "p_value": round(model.pvalues["treated:post"], 4),
            "n": int(model.nobs),
            "controls": "weather_severity, hour_volume, month FE, hour FE",
        })

    out = pd.DataFrame(results)
    out.to_csv(out_dir / "fact_rebank_effect_prototype.csv", index=False)
    return out


def run_event_study(df: pd.DataFrame, rebank_week: int, out_dir: Path, window: int = 8) -> None:
    iso = df["flight_date"].dt.isocalendar()
    df = df.copy()
    df["iso_week"] = iso["week"].astype(int)
    df["week_relative"] = df["iso_week"] - rebank_week
    df = df[(df["week_relative"] != 0) & (df["iso_week"] != 1)]
    df["day_cluster"] = df["flight_date"].astype(str)

    sub = df.dropna(subset=["d0", "weather_severity", "hour_volume"]).copy()
    sub = sub[sub["week_relative"].between(-window, window)]
    sub["wk_cat"] = pd.Categorical(sub["week_relative"].astype(int))

    # No C(month) here: within a narrow +-window-week band, relative week
    # and calendar month are nearly collinear (each week falls almost
    # entirely in one month), which makes the design matrix rank-deficient.
    # The week dummies already absorb time trends at finer resolution than
    # month would, so month FE is redundant in this specification.
    formula = (
        "d0 ~ treated * C(wk_cat, Treatment(reference=-1)) + weather_severity + hour_volume "
        "+ C(dep_hour)"
    )
    model = smf.ols(formula, data=sub).fit(cov_type="cluster", cov_kwds={"groups": sub["day_cluster"]})

    weeks = sorted(sub["week_relative"].unique())
    rows = []
    for wk in weeks:
        if wk == -1:
            rows.append({"week_relative": wk, "coef": 0.0, "ci_low": 0.0, "ci_high": 0.0})
            continue
        key = f"treated:C(wk_cat, Treatment(reference=-1))[T.{wk}]"
        if key not in model.params:
            continue
        lo, hi = model.conf_int().loc[key]
        rows.append({
            "week_relative": wk,
            "coef": round(model.params[key], 4),
            "ci_low": round(lo, 4), "ci_high": round(hi, 4),
        })

    event_df = pd.DataFrame(rows)
    event_df.to_csv(out_dir / "event_study_d0.csv", index=False)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.axvline(-0.5, color="red", linestyle="--", linewidth=1, label="rebank (week 0 excluded)")
    ax.errorbar(
        event_df["week_relative"], event_df["coef"],
        yerr=[event_df["coef"] - event_df["ci_low"], event_df["ci_high"] - event_df["coef"]],
        fmt="o-", capsize=3, color="#4C72B0",
    )
    ax.set_xlabel("weeks relative to rebank (week -1 = reference)")
    ax.set_ylabel("DFW D0 effect vs ORD (pp), relative to week -1")
    ax.set_title(f"Event study: DFW D0 vs {CONTROL_HUB} control, weekly, around the April 2026 rebank")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "event_study_d0.png", dpi=150)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bts-dir", type=Path, required=True)
    parser.add_argument("--weather-dir", type=Path, required=True)
    parser.add_argument("--months", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6, 7, 8])
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("loading weather...")
    weather = {
        TREATED_HUB: load_weather_severity(args.weather_dir, TREATED_HUB),
        CONTROL_HUB: load_weather_severity(args.weather_dir, CONTROL_HUB),
    }

    print("loading BTS panel...")
    df = load_panel(args.bts_dir, args.months, weather)
    print(f"  {len(df)} AA departures from {TREATED_HUB}/{CONTROL_HUB}, "
          f"weather coverage {df['weather_severity'].notna().mean():.1%}")

    rebank_week = find_rebank_week(df)
    print(f"detected rebank week: 2026-W{rebank_week}")

    results = run_diff_in_diff(df, rebank_week, args.out_dir)
    print("\n" + results.to_string(index=False))

    run_event_study(df, rebank_week, args.out_dir)
    print(f"\nwrote outputs to {args.out_dir}")


if __name__ == "__main__":
    main()
