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
- This is the input to signature analysis #2 below -- the bank windows
  here define the treatment-period schedule structure that analysis tests
  reliability against.

### 2. The rebank is associated with a real D0/A14 decline at DFW, net of weather

Weather-adjusted difference-in-differences: treated = AA departures from
DFW, control = AA departures from ORD (an AA hub with no April 2026
structural change), Jan-Aug 2026, clustered by day. Covariates: origin
weather severity (`hubops.metar`), scheduled departures in that hour, month
and hour fixed effects. See
[`analysis/dfw_rebank_diffindiff.py`](analysis/dfw_rebank_diffindiff.py) and
[`docs/analysis/fact_rebank_effect_prototype.csv`](docs/analysis/fact_rebank_effect_prototype.csv).

The rebank week itself wasn't assumed -- it's detected from the schedule:
each week's DFW departure-time histogram is correlated against August's
canonical post-rebank shape, and the first jump that stays sustained for
every remaining week identifies the changeover. That lands on **2026-W15
(April 6-12)**, a much larger and more durable break (+0.21 correlation,
sustained above 0.80 for all 21 remaining weeks) than a smaller, separate
schedule tweak the same method flags around week 8 -- which fades back out
by week 11 and isn't the rebank.

| Metric | DFW effect vs ORD (post - pre) | 95% CI | p-value |
| --- | --- | --- | --- |
| D0 | -6.4 pts | [-10.2, -2.6] | 0.0009 |
| A14 | -8.1 pts | [-12.9, -3.3] | 0.0010 |
| TXO | -0.14 min | [-1.82, 1.53] | 0.87 (null) |

- **D0 and A14 both fell at DFW relative to ORD, net of weather and traffic
  volume** -- consistent with the public tracking this project set out to
  test (Cranky Flier, see the pitch above). TXO shows no measurable effect,
  which is itself informative: whatever changed, it's not showing up as
  slower taxi-out.
- **The effect isn't a step function, it grows in**: the event-study plot
  below shows a mostly flat pre-trend (one borderline week, -8, discussed
  below) followed by a gap that widens over the first ~2 months post-rebank
  rather than appearing immediately -- consistent with an operational
  adjustment period, and a more convincing pattern than an instant jump
  would be (an instant jump is easier to explain by a single confounding
  event on one specific day).

![Event study: DFW D0 vs ORD control, weekly](docs/analysis/event_study_d0.png)

- **Honest caveat on parallel trends**: week -8 (about two months before
  the rebank) shows a marginally significant negative coefficient, the one
  pre-period point whose CI doesn't cross zero. Every other pre-period week
  (-7 through -2) is flat and insignificant. One borderline point in an
  8-week pre-window isn't disqualifying, but it means this result should be
  read as "a real, sizeable, weather-adjusted decline that likely but not
  certainly reflects the rebank" rather than an airtight causal claim --
  exactly why this is a single-control-hub *prototype*, and why the full
  build adds CLT and DFW year-over-year as further controls per the plan.

_(turn-length cliff and weather-adjusted scorecard still to come)_

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
