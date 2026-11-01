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
  - `fact_flight_leg` / `fact_flight_leg_quarantine` written by `nb_silver_flights` (see below).
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

- `nb_bronze_airports` (id `bf718898-33ef-481d-b492-48a531893296`) -- reads
  `Files/airports/dim_airport.csv` (OurAirports + resolved timezones, see
  `contracts/dim_airport.yaml`) and writes `lh_bronze.dim_airport` as Delta.
  Not one of the plan's original 7 sources -- built to unblock Phase 2
  silver, which needs each airport's timezone to resolve BTS's local-clock
  flight times (`hubops.bts_times.resolve_leg_times`) for all ~370 distinct
  airports BTS covers, not just the handful hardcoded for the two completed
  analyses. 2,060 airports, 100% coverage validated against real BTS
  Origin/Dest codes. Ran clean on the first try. Found a real cross-source
  quirk building it: Palm Beach Intl's IATA code changed to `DJT` in
  OurAirports' current data, but BTS still files flights as `PBI`.

- `nb_silver_flights` (id `bd79b8be-c2b6-4d52-9349-d3f7e1e8dbe2`) --
  Phase 2: resolves BTS's local-clock, no-timezone hhmm fields into real
  UTC instants, computes per-leg KPI flags (`docs/kpi_spec.md`), applies
  data quality constraints, writes `lh_silver.fact_flight_leg` (+
  `fact_flight_leg_quarantine`). By far the hardest notebook in this
  project after TSA -- five real bugs across four debugging rounds
  before it validated clean, documented in full in ADRs 0010 and 0011:
  1. `StructType.add()` mutates in place, corrupting the input schema.
  2. Wrong `mapInPandas` function signature (iterator-of-batches in/out,
     not a single DataFrame).
  3. `array_remove(array, NULL)` doesn't strip nulls (SQL `NULL = NULL`
     is unknown, not `TRUE`) -- used the higher-order `filter` function
     instead.
  4. **The real logic bug**: anchoring the *actual* departure/arrival
     time to `FlightDate` the same way as the *scheduled* time, which
     breaks when a delay pushes the actual time past local midnight.
     Caught because validation checked against an *independent* ground
     truth (BTS's own reported delay minutes) rather than just internal
     consistency -- redefined `actual_dep_utc`/`actual_arr_utc` as
     `sched_utc + reported_delay` instead of re-deriving them.
  5. A pandas gotcha surfaced by that fix: `.apply()` returning a mix of
     `None`/float silently coerces the whole Series to `float64`,
     turning every `None` into `NaN` -- `is not None` doesn't catch
     `NaN`. Fixed with `pd.notna()` throughout.
  Validation also surfaced a genuine data quirk, not a bug (ADR 0011):
  Allegiant (G4) tail numbers are missing their leading "N" in BTS's
  export (`816NV` instead of `N816NV`) -- 9,980 of 673,065 rows on the
  Aug-2026 test partition were quarantined over this alone. Normalized
  by reconstructing the real tail (deterministic, carrier-specific,
  confirmed via a throwaway `nb_diag_tail` diagnostic, not committed)
  rather than quarantining a cosmetic export quirk.
  Final validated numbers on the Aug-2026 test partition (673,065 rows):
  elapsed-time match 99.999% (656,477/656,482 -- the plan's target was
  99.9%), quarantine rate 0.017% (117/673,065 -- the plan's target was
  <0.5%), 9,897 G4 tails normalized. The 5 remaining elapsed-time
  mismatches and 83 remaining malformed tails look like genuine BTS-side
  data anomalies (e.g. a same-tail same-day roundtrip with mismatches on
  both legs, consistent with an irregular-ops day), not resolver bugs.
  **Full 44-month/~25M-row run completed 2026-10-31** (`TEST_YEAR=None`,
  35m16s runtime, job status `Completed`, no failure). The committed
  notebook has been reset to the small test config (`TEST_YEAR="2026"`,
  `TEST_MONTH="8"`) post-run, matching this project's established
  convention (see `nb_bronze_bts` above) -- `lh_silver.fact_flight_leg`
  and `fact_flight_leg_quarantine` now hold the full Jan 2023-Aug 2026
  dataset.

- `nb_gold_build` (id `f4397c03-39d3-4e87-976d-1341fd46adc7`) -- Phase 3:
  creates `wh_gold`'s star schema and loads it, entirely via T-SQL
  executed through `pyodbc` + an AAD access token from inside this Spark
  notebook (confirmed available in Fabric's default runtime -- see ADR
  0013). The big loads (`fact_flight_leg`, `fact_checkpoint_hour`,
  `fact_cbp_wait_hour`, `dim_airport`, `dim_aircraft`) are pure
  server-side `INSERT INTO ... SELECT` cross-database queries straight
  from `lh_bronze`/`lh_silver` -- confirmed Fabric Warehouses in the same
  workspace can query a Lakehouse's Delta tables directly by name, no
  Spark data movement needed. Small dims and the locally-computed
  analysis outputs (`dim_bank`, `fact_rebank_effect`,
  `fact_rebank_event_study`) are generated/read in Python and inserted
  via `executemany`. Two real bugs, both caught via this notebook's own
  diagnostic-file writes (not portal access) and fixed in one round
  trip: `DATETIME2` needs explicit `(6)` precision in Fabric Warehouse,
  and `checkpoint` is a reserved T-SQL keyword needing bracket-quoting.
  See ADR 0013 for the full story. Final row counts: `fact_flight_leg`
  27,787,225, `fact_checkpoint_hour` 1,607,461, `fact_cbp_wait_hour`
  20,740, `dim_airport` 2,060, `dim_aircraft` 1,635, plus all dims/small
  facts. Phase 3's revised "done when" (reconciliation against BTS raw
  aggregates, not DOT -- ADR 0012) passed across all 44 months: max D0
  diff 0.0000924, max CF diff 0.0000087.

- `nb_gold_turns` (id `bef4f11b-3dba-4b69-a1bf-57f0e09b649a`) -- Phase
  6's `fact_aircraft_turn`, "the table nobody else builds from public
  data" per the plan. Pairs each tail's inbound arrival with its next
  departure from the same station, same rule as the tested
  `hubops.turns.pair_turns()` (same station, non-negative gap, gap under
  6 hours), reimplemented as a set-based T-SQL window-function query
  (`LEAD()` per tail partition) instead of row-by-row Python, since it
  runs over `fact_flight_leg`'s ~27.8M rows. Must run after
  `nb_gold_build`. Ran clean: 21,024,172 total turns (893,482 at DFW),
  fleet-p10 flagging via `PERCENTILE_CONT(0.1) WITHIN GROUP (...) OVER
  (PARTITION BY fleet_family)` worked on the first try (5,098,813 rows
  flagged -- only AA's own fleet has a `dim_aircraft` match, so non-AA
  tails are left unflagged, expected). Spot-checked the first sample row
  by hand: a 2026-08-31 DFW arrival scheduled 23:30, actually landing
  04:09 the next day (279 min late), paired with an outbound scheduled
  23:53 but not actually departing until 10:02 (609 min late) --
  `minutes_recovered = 279 - 609 = -330`, exactly matching the stored
  value. This also closes Phase 2's remaining "done when" item (20
  hand-checked DFW turns pairing correctly).
