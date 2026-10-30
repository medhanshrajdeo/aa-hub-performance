# ADR 0005: Bake backfill parameters into the pushed notebook, don't rely on runtime injection

**Status:** done, 2026-10-30.

## Context

Backfilling BTS data for Phase 1 means running `nb_bronze_bts` once per
month from 2023-01 through the latest published month. Fabric's Job
Scheduler API documents a `parameters` field on `RunOnDemandItemJobRequest`
(an array of `{name, value, type}`, e.g. `{"name": "YEAR", "value": 2023,
"type": "Integer"}`) meant for exactly this: override a notebook's
"parameters"-tagged cell per run without touching its stored definition.

Tested it against the real workspace: triggered a `RunNotebook` job with
`parameters: [{"name":"YEAR","value":2023,...}, {"name":"RESET","value":
true,...}]` against a notebook whose committed defaults were
`YEAR=2026, MONTH=8, RESET=False`. The run reported `Completed`, but
inspecting the Delta table's actual file layout afterward (via the OneLake
DFS API, `GET .../Tables/bts_marketing_ontime?resource=filesystem`) showed
no `Year=`/`Month=` partition folders -- meaning the `RESET` branch (which
would create a partitioned table) never ran. That's only explainable if
the run used the notebook's stored defaults, not the parameters I passed.

## Decision

Don't depend on runtime parameter injection for this backfill. Instead,
`scripts/fabric/build_bts_month_notebook.py` regenerates
`notebook-content.ipynb` with the actual year/month/reset values baked
directly into the parameters cell's source, and
`scripts/fabric/run_bts_backfill.sh` pushes a fresh definition
(`updateDefinition`) before every single month's run.

This is slower per-iteration (an extra `updateDefinition` call) but
removes the ambiguity entirely: the definition pushed for a given month
*is* the record of what that run executed, verifiable by reading it back
from the item at any time, rather than trusting an API parameter that
couldn't be confirmed to take effect.

## Consequences

- After the backfill finishes, the live notebook's definition reflects
  whatever month ran last, not a generic template. The committed repo
  copy (`fabric/nb_bronze_bts.Notebook/`) should be reset to generic
  defaults (`YEAR=2026, MONTH=8, RESET=False`) and re-pushed once the
  backfill completes, so the Git-tracked source and the live item agree
  again.
- If Fabric's parameter injection turns out to work after all (e.g. the
  issue was something else about that one test, like endpoint choice --
  `notebook-public-api.md` shows a *different* path,
  `notebooks/{id}/jobs/execute/instances?jobType=RunNotebook`, than the
  generic `items/{id}/jobs/RunNotebook/instances` used here and in the
  first working runs), it would simplify this considerably. Worth
  revisiting before building the *real* Phase 1 Data Pipeline (ForEach
  over months) as reusing a single pushed notebook with per-iteration
  parameters is the standard pattern there -- but only once confirmed
  working, not assumed.
