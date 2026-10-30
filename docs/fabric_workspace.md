# Fabric workspace

Dev workspace for this project (phase 0 of `planning/project-plan.md`
calls it `aa-hubops-dev`):

- URL: https://app.fabric.microsoft.com/groups/16b2a9c6-6418-485f-865f-c47442b8b6c6/list?experience=fabric-developer
- Workspace ID: `16b2a9c6-6418-485f-865f-c47442b8b6c6`
- Capacity: Fabric Trial
- Workspace settings: large semantic model storage format

Created 2026-10-29 via `az rest` against the Fabric REST API (see
`docs/decisions/0002-fabric-items-via-az-rest.md`):

- [x] Lakehouse `lh_bronze` (id `09a8b1f5-f33c-4eed-912d-40b9cbdc9b06`) -- auto-created a `lh_bronze` SQL Endpoint item too
- [x] Lakehouse `lh_silver` (id `852a5b52-5eee-4ca5-8380-3f9a970fa0a0`) -- auto-created a `lh_silver` SQL Endpoint item too
- [x] Warehouse `wh_gold` (id `9fa99a49-fc04-45d7-ac4a-598c8b0ad25e`)
- [ ] Connect Git integration -- **deferred to Phase 8 hardening**, not a blocker for Phases 1-7. GitHub is greyed out as a Git provider for this workspace (tenant setting off); Azure DevOps works but needs an org, which doesn't exist yet. Revisit then: either enable the GitHub tenant setting (Admin Portal, if this is a tenant medhansh.rajdeo@tamu.edu administers) or stand up an ADO org.

## Notebooks

- `nb_bronze_bts` (id `0352fa28-34f1-4ef7-b669-6909bff248f2`), created and run via `az rest`
  (ADR 0002 pattern) -- downloads one BTS Marketing Carrier month, lands the raw CSV to
  `lh_bronze/Files/bts/{yyyy}/{mm}/`, writes `lh_bronze.bts_marketing_ontime` as Delta.
  First run (2026-08 data) **failed** on the Delta write:
  `DELTA_INVALID_CHARACTERS_IN_COLUMN_NAMES` on `Operating_Airline ` (the trailing-space
  header quirk already documented in `contracts/bts_marketing_ontime.yaml`, but not yet
  translated into actual sanitization code until this run caught it). Fixed by stripping
  whitespace and swapping any Delta-invalid character for `_` before the write; second run
  **completed**, table confirmed live via the Lakehouse Tables API.
  **Full Jan 2023 - Aug 2026 backfill completed 2026-10-30** (44 months, all `Completed`,
  see `docs/fabric_backfill_log.csv`) via `scripts/fabric/run_bts_backfill.sh`. `lh_bronze.
  bts_marketing_ontime` is now partitioned by `Year`/`Month` with idempotent per-month
  writes (Delta `replaceWhere`). See ADR 0005 for why this bakes year/month/reset directly
  into the pushed notebook definition per month rather than using Fabric's runtime
  parameter-injection API (tested, didn't reliably take effect) -- and for a real incident:
  a bug in the first version of the orchestration script (reset incorrectly inferred from
  loop position instead of an explicit flag) wiped the Jan 2023 partition mid-backfill. Caught
  immediately, root-caused, fixed, and the backfill restarted cleanly from a true reset.
  The committed notebook (`fabric/nb_bronze_bts.Notebook/`) has been reset to generic
  defaults (YEAR=2026, MONTH=8, RESET=False) matching the live item, post-backfill.

- `nb_bronze_faa` (id `9ea33798-d026-4b96-9cd9-8db204a7dca3`) -- reads
  `Files/faa/dim_aircraft_aa.csv` (already clean, already PII-free -- see
  `contracts/faa_releasable_aircraft.yaml` and ADR 0006) and writes
  `lh_bronze.dim_aircraft` as Delta. Unlike every other notebook here, this one
  doesn't download anything -- the FAA registry is bot-protected (ADR 0001), so
  `src/hubops/faa_registry.py` ran locally against the manually-downloaded zip,
  joining/filtering/dropping-PII *before* anything touched the cloud, and only
  the small clean CSV (92KB, 1,635 rows) was uploaded (via PowerShell, see ADR
  0006 -- `az rest`'s binary body handling silently drops bytes). Ran clean on
  the first try. 100% of AA's real tail numbers (from all 8 locally-downloaded
  2026 BTS months) matched the registry.
