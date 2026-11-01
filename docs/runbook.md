# Runbook

Operational reference for re-running, extending, or troubleshooting this
pipeline. Written from real debugging history, not a generic template --
every pitfall below actually happened once this project. See
`docs/decisions/` for the full story behind each one.

## Prerequisites

- `az login` with access to the `aa-hubops-dev` workspace
  (`16b2a9c6-6418-485f-865f-c47442b8b6c6`). Every Fabric call in this
  project goes through `az rest`, not the `fab` CLI or any SDK --
  [ADR 0002](decisions/0002-fabric-items-via-az-rest.md).
- `uv sync` from repo root for local Python (test suite, build scripts).
- No local SQL Server ODBC driver or `pyodbc` is needed, or used, on
  your own machine -- all T-SQL against `wh_gold` runs *through a Fabric
  Spark notebook*, not from a local connection. See
  [ADR 0013](decisions/0013-gold-warehouse-via-pyodbc-crossdb.md) for
  why, and don't try to set up a local SQL connection to replace this.

## How to re-run a notebook

Every `fabric/*.Notebook/` directory is built by a matching
`scripts/fabric/build_*_notebook.py` script. The cycle is always the
same four steps:

```bash
# 1. Edit the build script, then regenerate the notebook source
uv run python scripts/fabric/build_<name>_notebook.py --out fabric/nb_<name>.Notebook

# 2. Build the create/update request bodies
uv run python scripts/fabric/push_notebook.py fabric/nb_<name>.Notebook

# 3. Push the definition (use the item's id from docs/fabric_workspace.md)
az rest --method POST \
  --url "https://api.fabric.microsoft.com/v1/workspaces/16b2a9c6-6418-485f-865f-c47442b8b6c6/notebooks/<ITEM_ID>/updateDefinition" \
  --resource "https://api.fabric.microsoft.com" \
  --headers "Content-Type=application/json" \
  --body @"fabric/nb_<name>.Notebook/.push/update_body.json"

# Poll the returned Operation-Location/x-ms-operation-id until "Succeeded"
# BEFORE running -- updateDefinition is async (202 Accepted). An
# immediate run-after-push can execute stale pre-fix content.

# 4. Run it
az rest --method POST \
  --url "https://api.fabric.microsoft.com/v1/workspaces/16b2a9c6-6418-485f-865f-c47442b8b6c6/items/<ITEM_ID>/jobs/instances?jobType=RunNotebook" \
  --resource "https://api.fabric.microsoft.com"

# Poll the job instance (from the Location/x-ms-job-id header) until
# "Completed" or "Failed".
```

Item ids for every notebook are in `docs/fabric_workspace.md`.

## How to check what a run actually did

**The Fabric REST job-status API never surfaces cell-level output or
errors** -- confirmed repeatedly across this entire project, no
exceptions found. A `GET .../jobs/instances/{id}` response only ever
tells you `Completed` / `Failed` / `InProgress`, plus (on failure) a
generic `failureReason` like `System_Cancelled_Session_Statements_Failed`
with no further detail.

Every notebook built since `nb_gold_build` (2026-10-31 onward) writes
its real results, and on failure its full `traceback.format_exc()`, to
`Files/diagnostics/<name>.json` in `lh_bronze` -- read it back with:

```bash
az rest --method GET \
  --url "https://onelake.dfs.fabric.microsoft.com/16b2a9c6-6418-485f-865f-c47442b8b6c6/09a8b1f5-f33c-4eed-912d-40b9cbdc9b06/Files/diagnostics/<name>.json" \
  --resource "https://storage.azure.com"
```

This needs no portal access and works for both success and failure.
**If you're adding a new notebook, give it this pattern from the start**
-- see `_wrap_cell` in `scripts/fabric/build_gold_notebook.py` for the
reusable wrapper, or `write_diag` for the plain write-on-success call.
Notebooks built *before* this pattern existed (everything through
`nb_silver_flights`) still need a portal check (open the notebook in the
workspace, check its run history) to see cell output -- there's no way
around that for those specific items short of rebuilding them with the
diagnostics pattern too.

## Known pitfalls (all hit for real, not hypothetical)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `An integer precision value between 0 and 6 must be specified` creating a table | Bare `DATETIME2` isn't valid in Fabric Warehouse | Always write `DATETIME2(6)` |
| `Incorrect syntax near the keyword 'checkpoint'` (or any T-SQL reserved word used as a column name) | Fabric Warehouse enforces reserved words same as SQL Server | Bracket-quote it: `[checkpoint]` |
| `updateDefinition`/`getDefinition` appear to have "already happened" but a run uses old content | Both are async (202 + Operations polling URL) | Poll the operation to `Succeeded` before the next step; `run_bts_backfill.sh` already has a defensive `sleep 10` for scripted loops |
| `%pip install X` wipes variables defined earlier in the same notebook | `%pip install` restarts the kernel | Put `%pip install` in the very first cell, or avoid it entirely (see next row) |
| `MagicUsageError: %pip magic command is disabled` | `%pip install` is disabled entirely for batch `RunNotebook` jobs (works in interactive sessions only) | Either build a custom Fabric Environment with the library pre-installed, or do the work locally and upload a clean result (this project's choice for TSA PDF parsing -- [ADR 0008](decisions/0008-tsa-pivot-to-local-parsing.md)) |
| OneLake binary upload returns 202/exit-0 but `contentLength: 0` on the landed file | `az rest --body @file` silently drops binary bytes | Use PowerShell `Invoke-WebRequest` with `[System.IO.File]::ReadAllBytes(...)` instead -- [ADR 0006](decisions/0006-onelake-binary-upload-needs-powershell.md). Always verify with a HEAD request after uploading, never trust the 200 alone |
| PowerShell `"$url?query=1"` throws `Invalid URI: The hostname could not be parsed` | PowerShell string interpolation needs `${url}` (braces), not `$url`, when a variable is immediately followed by non-identifier text like `?` | Use `"${url}?query=1"` |
| A resolved "actual" timestamp is off by ~24 hours for a handful of rows | Anchoring `DepTime`/`ArrTime` to `FlightDate` (the *scheduled* date) breaks when a delay pushes the actual time past local midnight | Compute `actual_*_utc` as `sched_*_utc + reported_delay`, not independently from the raw hhmm fields -- [ADR 0010](decisions/0010-silver-time-resolution-bugs.md) |
| `pd.Timedelta(minutes=d)` raises `cannot convert float NaN to integer` despite an `is not None` guard | `.apply()` returning a mix of `None`/float silently coerces the whole pandas Series to `float64`, turning every `None` into `NaN` -- `NaN is not None` is `True` in Python | Use `pd.notna()`/`pd.isna()`, never `is (not) None`, on any value that passed through a pandas `.apply()` |
| `array_remove(array, NULL)` doesn't strip null entries | SQL `NULL = NULL` is unknown, not `TRUE` | Use the higher-order `filter(array, lambda x: x.isNotNull())` instead |
| `mapInPandas` fails with `AttributeError: 'map' object has no attribute 'apply'` | The function must take an iterator of batches and yield an iterator back, not accept/return a single DataFrame | `def f(iterator): for pdf in iterator: yield transform(pdf)` |
| `df.schema.add(...)` corrupts the original DataFrame's columns | `StructType.add()` mutates in place in this Spark version | Build a new `StructType(list(df.schema.fields) + [...])` |
| A quarantine reason is concentrated almost entirely in one value of some other column (e.g. one carrier) | Likely a source-specific formatting quirk, not actually bad data | Check before assuming -- this project found Allegiant (G4) tails missing their leading "N" this way ([ADR 0011](decisions/0011-g4-tail-number-quirk.md)) |
| A validation check passes at ~100% but the result is still wrong | The check only tested internal consistency (does the output agree with itself), not an independent ground truth | Validate against something the code under test never touched -- e.g. BTS's own reported delay, not a value the same resolver also produced |

## Re-running the full pipeline from scratch

Not recommended casually -- the full BTS backfill alone took real wall
time (44 months) and the silver/gold full runs took 35 and a few
minutes respectively. If genuinely starting over:

1. Bronze: `scripts/fabric/run_bts_backfill.sh` (BTS), then
   `nb_bronze_faa`, `nb_bronze_weather`, `nb_bronze_cbp`,
   `nb_bronze_airports` (each a single run), then
   `scripts/fabric/download_and_parse_tsa_weeks.py` locally followed by
   `nb_bronze_tsa` (TSA -- see [ADR 0008](decisions/0008-tsa-pivot-to-local-parsing.md)
   for why TSA parses locally, not in Fabric).
2. Silver: `nb_silver_flights` with `TEST_YEAR=None` (full run), then
   `nb_silver_weather`.
3. Gold: `nb_gold_build`, then `nb_gold_turns` and
   `nb_gold_station_hour` (both depend on `nb_gold_build` having run
   first).
4. Verify: read back `Files/diagnostics/gold_summary.json` and
   `gold_reconciliation.json` per the "How to check what a run actually
   did" section above.

Committed notebooks are left on a small test config (`TEST_YEAR="2026"`,
`TEST_MONTH="8"` in `nb_silver_flights`) after a full run, matching this
project's convention -- don't commit a full-scale config as the
default.

## Not yet built

- **Phase 5 (Direct Lake semantic model + Power BI report)**: blocked
  on a licensing step only a human can complete -- the Power BI
  Authoring MCP tool's EULA must be reviewed and accepted by a person,
  never by an agent automatically. Needs the project owner's explicit
  go-ahead before this phase can start.
- Production hardening (deployment pipeline, Variable Library, alerts) --
  Phase 8, not started.
