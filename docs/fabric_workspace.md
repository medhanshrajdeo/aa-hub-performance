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

- `nb_bronze_weather` (id `017ac6bb-751d-47f7-a9eb-84634092d075`) -- downloads
  hourly METAR directly from IEM's ASOS API (no bot-protection, unlike FAA/DOT)
  for AA's six primary hubs (DFW, CLT, MIA, ORD, PHX, PHL), Jan 2023 - Aug 2026,
  lands raw per-station CSVs to Files/, writes `lh_bronze.weather_metar_raw`
  as Delta (partitioned by station). Bronze stays raw text -- severity scoring
  (`hubops.metar`) is a silver-layer transform, not done here. Ran clean on
  the first try.

- `nb_bronze_cbp` (id `2a3e06b1-2008-4221-bc68-f8609c77ee12`) -- reads the
  manually-exported `Files/cbp/awt_dfw_terminal_d.csv` (see
  `contracts/cbp_airport_wait_times.yaml`; CBP's site is a JS dashboard with
  no automatable export, genuinely manual rather than bot-protected) and
  writes `lh_bronze.cbp_wait_hour` as Delta. Unusually clean source -- real
  CSV header, no PDF/merged-cell wrangling, just type casts. Ran clean on the
  first try. 20,740 real hourly rows, May 2023 - Oct 2026.

- `nb_bronze_tsa` (id `15f0642f-5034-4df4-9734-938410524a5a`) -- the one
  genuinely hard source, and the one with the most real debugging along the
  way. Final shape: reads one pre-combined CSV (`Files/tsa/
  tsa_checkpoint_hour_all.csv`, all 35 weeks) and writes
  `lh_bronze.tsa_checkpoint_hour` as Delta, partitioned by `week_start` --
  no PDF parsing happens in Fabric at all. Four real obstacles, in order:
  1. `%pip install pdfplumber` restarts the notebook kernel, wiping variables
     assigned before it (ADR 0007).
  2. Fabric's outbound request got `403 Forbidden` from tsa.gov even though
     plain curl (and every local download in this project) worked fine with
     no special headers -- fixed with an honest, self-identifying User-Agent.
  3. **`%pip install` is disabled entirely for batch `RunNotebook` jobs**
     (confirmed via `MagicUsageError: %pip magic command is disabled`, and
     Microsoft's library-management docs). Rather than build a custom
     Environment for one library, pivoted (ADR 0008) to parsing all 35 weeks
     locally -- `pdfplumber` already worked there, proven repeatedly, and the
     parsing itself is single-threaded driver-side work regardless of where
     it runs, so Fabric would only have added per-job Spark session overhead,
     not parallelism. The background parse job (resumable -- it was killed
     once for host memory pressure partway through and picked back up
     cleanly from the 22 weeks already done) produced a combined 140MB/
     1.6M-row CSV, uploaded via the same PowerShell method as FAA.
  4. The simplified notebook's write still failed, now with a real Delta
     error: `DELTA_FAILED_TO_MERGE_FIELDS` on `date`, because the table
     already existed from the earlier per-week prototype with `date` as a
     plain string, and the final version casts it to a real `DateType`.
     Fixed with `overwriteSchema=true` (ADR 0009), which also surfaced that
     `updateDefinition`/`getDefinition` are async (202 + poll-the-operation,
     same as item creation) -- an earlier push-then-run attempt had
     silently run against stale pre-fix content because of this.
  All 35 `week_start` partitions confirmed live (verified via the OneLake
  DFS API, not just job status). See `docs/fabric_backfill_tsa_weeks.txt`
  for the source list and ADRs 0007-0009 for the full debugging trail.
