# CLAUDE.md

Conventions for Claude Code sessions working on this repo. Full context, data
sources, KPI definitions, architecture and the phase-by-phase build plan live
in `planning/project-plan.md` -- read that before starting any phase. The
target job posting is `planning/job-posting-analyst-airport-performance.txt`.

## What this is

DFW Hub Performance Analytics: an end-to-end Fabric pipeline on real federal
and public aviation data (BTS, TSA, CBP, FAA, IEM weather) that tests
whether American's April 2026 DFW rebank improved departure reliability, net
of weather, and surfaces where frontline leaders at DFW are still losing
controllable minutes. Built as an independent portfolio project -- no AA
logos, no non-public data.

DOT Air Travel Consumer Report and BTS T-100 are explicitly **out of
scope** -- see [ADR 0012](docs/decisions/0012-dot-t100-scope-out.md).
Five real sources carry the whole project.

## Working agreements

- **Write the "done when" check before the code**, per the phase it belongs
  to in `planning/project-plan.md`. If a phase's "done when" can't be
  verified without cloud resources the session doesn't have, write the
  verification as a script/test now and note what it needs to run against.
- **Pure-Python logic lives in `src/hubops/`, tested in `tests/`, and must
  run with no Fabric/network dependency.** Parsing, time wrangling, turn
  pairing, weather scoring -- all of it should be unit-testable locally
  before it ever runs inside a Fabric notebook. This is also why these
  modules are written to "port to Databricks unchanged" (job posting
  requirement) -- no Fabric-only APIs in `src/hubops/`, only in `fabric/`.
- **Every source gets a schema contract in `contracts/` before it gets a
  loader.** Column names, types, and nullability. Drift against the
  contract is a load failure, not a silent coercion.
- **KPI math matches `docs/kpi_spec.md` exactly**, including the edge cases
  (cancelled legs excluded from D0, diverted counts as completed for CF,
  etc). If a notebook or DAX measure disagrees with the spec, the spec wins
  and the code gets fixed -- these numbers get reconciled against BTS's
  own raw aggregates (not DOT -- see ADR 0012) in phase 3/8.
- **Dates and times are the easiest place to be silently wrong.** Any code
  touching BTS hhmm fields, airport local time, or UTC conversion goes
  through `src/hubops/bts_times.py` and `src/hubops/airports.py` -- don't
  re-derive ad hoc offset math in a notebook.
- **No synthetic data.** Every row traces to one of the five sources in the
  plan. If a source isn't available yet, the analysis waits or is marked
  partial -- it doesn't get backfilled with made-up numbers.

## Running things locally

Python/dependency management is `uv`. From repo root:

```
uv sync            # install deps into .venv
uv run pytest      # run the test suite
```

## Repo layout

See `planning/project-plan.md` -> "Repo structure" for the full layout and
intent of each top-level folder.
