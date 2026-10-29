# DFW Hub Performance Analytics

Did American's April 2026 DFW rebank improve departure reliability, net of
weather -- and where are frontline leaders still losing controllable
minutes? An independent, end-to-end analysis on public federal aviation
data (BTS, TSA, CBP, DOT, FAA, IEM), built on Microsoft Fabric.

Full project plan, data sources, KPI definitions, and the phase-by-phase
build log live in [`planning/project-plan.md`](planning/project-plan.md).

**Status:** Phases 0-1 done (Fabric workspace + lakehouses/warehouse live,
BTS source validated against real data); first signature analysis
prototyped locally. See the project plan's "Build phases" section for
what's left.

## Findings

### 1. DFW's bank structure did change around the April 2026 rebank

Reconstructed AA's DFW departure and arrival bank structure directly from
the public schedule (BTS `CRSDepTime`/`CRSArrTime`, Tue/Wed/Thu only, no
internal schedule data) for March 2026 (pre-rebank) vs August 2026
(post-rebank), using `scipy.signal.find_peaks` on scheduled-flight counts
per 15-minute slot -- see `src/hubops/banks.py` and
[`docs/analysis/dim_bank_prototype.csv`](docs/analysis/dim_bank_prototype.csv).

![DFW bank structure before vs after the April 2026 rebank](docs/analysis/dfw_bank_structure_pre_post_rebank.png)

- **Arrival structure fragmented**: 8 clean arrival banks pre-rebank became
  11 narrower, messier ones post-rebank. Departure bank *count* held at 8,
  but banks 2-3 moved earlier (08:15/10:15 -> 08:30/09:30) and morning peak
  heights dropped (e.g. the 08:15 bank fell from ~35 to ~27 avg departures),
  spreading volume across more, smaller waves rather than a few sharp ones.
- **This isn't just a schedule shift, it's schedule volume growth too**:
  total average AA/DFW scheduled departures per weekday rose from 722.6
  (March) to 765.7 (August), +5.9%. Any reliability comparison that doesn't
  control for this (and for weather, the next analysis's job) would
  wrongly blame the rebank for what's partly just more traffic.
- This is the input to signature analysis #2 (the weather-adjusted
  difference-in-differences test) -- not yet run. The bank windows above
  define the treatment-period schedule structure that analysis will test
  reliability against.

_(difference-in-differences, turn-length cliff, and weather-adjusted
scorecard still to come)_

## Architecture

_(diagram + notes land in phase 8)_

## Running locally

```
uv sync
uv run pytest
```

Pure-Python wrangling logic (`src/hubops/`) runs and tests with no Fabric or
network dependency. Fabric items (`fabric/`) sync via this workspace's Git
integration.

## Limits

See "Interview story and honest limits" in the project plan -- BTS delay
causes only exist for 15+ minute-late arrivals, BTS times are gate-out not
door-close, TSA/CBP data describes the airport not AA's customers
specifically, and the rebank test is observational (confounded by weather
and parallel ATC changes), which is why the event-study pre-trend check
exists.
