# ADR 0004: Create, update, and run Fabric notebooks via REST API, not the portal

**Status:** done, 2026-10-29/30.

## Context

Getting a notebook actually executing inside the Fabric workspace --
not just authored -- needed: (1) a way to create a Notebook item with real
code, (2) a way to trigger execution, (3) a way to poll for completion and
see failures, all without manual portal clicking, so the workflow is
scriptable and repeatable for the ~8 notebooks this project will
eventually need (`nb_bronze_bts`, `nb_silver_flights`,
`nb_analysis_rebank`, etc., per the repo structure in the project plan).

## Decision

Standard pattern, now proven end-to-end:

1. **Author** `notebook-content.ipynb` + `.platform` directly as files
   under `fabric/<name>.Notebook/` -- this is the actual source of truth,
   editable like any other repo file, and is exactly the format Fabric's
   Git integration would sync once that's connected (Phase 8).
2. **Build the API payload**: `scripts/fabric/push_notebook.py <dir>`
   base64-encodes both files into `.push/create_body.json` (first time)
   and `.push/update_body.json` (subsequent edits).
3. **Create or update**: `az rest POST .../notebooks` (create) or
   `az rest POST .../notebooks/{id}/updateDefinition` (update).
4. **Run**: `az rest POST .../items/{id}/jobs/RunNotebook/instances` ->
   202, `Location` header has the job instance URL (or list
   `.../jobs/instances` to find the latest one).
5. **Poll**: `az rest GET .../jobs/instances/{jobInstanceId}` until
   `status` is `Completed`/`Failed`/`Cancelled`. On `Failed`, the REST
   response's `failureReason` is a generic code
   (`System_Cancelled_Session_Statements_Failed`) -- **it does not include
   per-cell error output.** For the actual exception/traceback, check the
   notebook's run history in the Fabric portal (workspace -> notebook ->
   run history -> the failed run shows cell-level output, same as a local
   Jupyter error).

## What this caught

First real run of `nb_bronze_bts` failed at the Delta write with
`DELTA_INVALID_CHARACTERS_IN_COLUMN_NAMES` on `Operating_Airline ` -- the
trailing-space header already documented in
`contracts/bts_marketing_ontime.yaml`, but not yet turned into actual
sanitization code until this run surfaced it. Fixed with a general
column-name sanitizer (strip whitespace, swap any of
`' ,;{}()\n\t='` for `_`) rather than hardcoding the one known offender,
since BTS could introduce another one in a future month. Second run
completed; table confirmed live via the Lakehouse Tables API
(`GET /v1/workspaces/{ws}/lakehouses/{lh}/tables`).

## Consequences

- Writing to `{abfss root}/Tables/{name}` directly (rather than
  `saveAsTable`) sidesteps needing the notebook's default-lakehouse
  attachment metadata to be exactly right -- Fabric's Lakehouse table
  auto-discovery picks up any valid Delta folder under `Tables/`. Keep
  doing this for new notebooks unless there's a specific reason to use
  the catalog/`saveAsTable` path instead.
- The Lakehouse Tables API can lag the actual write by ~20 seconds --
  don't treat an empty list as failure immediately after a `Completed`
  job status.
- Debugging a failed run still requires the portal (or exporting the run
  snapshot some other way) for the real traceback -- the REST job-status
  API alone isn't enough to diagnose a failure blind.
