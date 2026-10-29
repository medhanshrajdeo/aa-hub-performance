# DFW Hub Performance Analytics

Did American's April 2026 DFW rebank improve departure reliability, net of
weather -- and where are frontline leaders still losing controllable
minutes? An independent, end-to-end analysis on public federal aviation
data (BTS, TSA, CBP, DOT, FAA, IEM), built on Microsoft Fabric.

Full project plan, data sources, KPI definitions, and the phase-by-phase
build log live in [`planning/project-plan.md`](planning/project-plan.md).

**Status:** Phase 1 in progress (BTS ingestion). See that doc's "Build
phases" section for what's done and what's next.

## Findings

_(filled in as phases 5-6 land -- bank reconstruction, rebank
difference-in-differences, turn-length cliff, weather-adjusted scorecard)_

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
