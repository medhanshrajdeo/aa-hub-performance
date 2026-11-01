# Fabric workspace

Dev workspace for this project. Phase 0 of `planning/project-plan.md`
planned to call it `aa-hubops-dev`; the workspace that actually got
created is named **`AmericanAirlines`** (`az rest GET /v1/workspaces/{id}`
confirmed this 2026-11-01, while building the semantic model and
needing the name, not just the ID, for the first time -- see
[ADR 0014](decisions/0014-direct-lake-semantic-model-quirks.md)). Use
the real name for anything name-based (`ConnectFabric`,
`DeployToFabric`); every `az rest` call in this project already used
the ID, which was never wrong.

- URL: https://app.fabric.microsoft.com/groups/16b2a9c6-6418-485f-865f-c47442b8b6c6/list?experience=fabric-developer
- Workspace ID: `16b2a9c6-6418-485f-865f-c47442b8b6c6`
- Workspace display name: `AmericanAirlines`
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

- `nb_silver_weather` (id `d72d09da-795c-4fc8-a209-b55a8fcd370b`) --
  Phase 4/6: parses `lh_bronze.weather_metar_raw`'s raw METAR text into
  the four severity signals (thunderstorm, ceiling <1000ft, visibility
  <3mi, gusts >30kt), same scoring as the tested `hubops.metar` module,
  reimplemented inline via `mapInPandas` (METAR token parsing is
  sequential/stateful, doesn't vectorize into Spark SQL). Buckets to
  station + UTC date + UTC hour, writes
  `lh_silver.weather_station_hour_severity`. Ran clean: 192,485 raw
  observations parsed, 192,462 station-hours written across the 6 AA
  hubs (~32,000 hours each), severity distribution sane (181,026 at
  score 0, max severity 4 observed at DFW and PHX).

- `nb_gold_station_hour` (id `c51d3127-651f-4d0d-9e8c-3426104e7949`) --
  `fact_station_hour`, "the join point for weather and passenger flow"
  per the plan. Grain is station + UTC date + UTC hour, deliberately
  derived from `sched_dep_utc`'s own UTC components rather than
  `fact_flight_leg`'s existing `date_key` column -- that column is
  anchored to `FlightDate` (the scheduled departure's ORIGIN-LOCAL date
  per ADR 0010), and pairing a locally-anchored date with a UTC hour
  would misalign the two by the origin's UTC offset, the same class of
  bug ADR 0010 already caught once. Must run after `nb_gold_build` and
  `nb_silver_weather`. **Scope cut, documented rather than silently
  skipped**: TSA throughput is NOT joined into this table -- TSA's
  `hour` column is station-LOCAL, while this table is UTC, and doing
  that conversion correctly needs the same DST-aware local/UTC logic
  `nb_silver_flights` already took two real bug-fix rounds to get right;
  not repeating that blind. `fact_checkpoint_hour` remains available
  standalone for anything that doesn't need it joined here. Ran clean:
  4,071,389 station-hour rows across ~370 origin airports; only 3.68%
  (149,829 rows) got a weather match, which is expected, not a bug --
  weather was only ever sourced for AA's 6 hub stations (`nb_bronze_weather`'s
  scope), so the other ~364 airports' station-hours legitimately have no
  weather data. Spot-checked DFW specifically: all 10 sampled rows have
  a real (non-null) severity score.

- `nb_analysis_export` (id `2b13f8a9-379a-4e0e-8d5f-f937fafa1d99`) --
  not part of the medallion pipeline itself; pulls two small,
  pre-aggregated CSVs out of `wh_gold` for signature analyses #3 and #4
  (`docs/analysis/turn_cliff_agg.csv`, `docs/analysis/station_day_d0_weather.csv`),
  so the aggregation over `fact_aircraft_turn`'s 21M rows and
  `fact_flight_leg`'s 27.8M rows happens in T-SQL where the data already
  lives, and only the small analysis-ready result leaves Fabric (same
  reasoning as analysis #1/#2's local prototypes, just sourced from the
  now-validated gold tables instead of re-downloading raw BTS). Must run
  after `nb_gold_build`, `nb_gold_turns`, `nb_silver_weather`. Ran clean:
  1,086 fleet x turn-bucket rows (headline: DFW turns with an on-time
  inbound still only hit 70.3% outbound D0, vs 35.0% for delayed-inbound
  turns), 8,040 station-days (99.9% weather coverage). Both CSVs
  downloaded via the OneLake DFS API and analyzed locally --
  `analysis/dfw_turn_cliff.py` and `analysis/dfw_weather_scorecard.py` --
  see the README's Findings #3 and #4. Later extended with two more
  export cells for signature analyses #5 and #6 (D0-vs-A14 tradeoff,
  passenger flow) -- see the README's Findings #5 and #6.

- `nb_gold_station_scorecard` (id `ff10c882-8519-487c-8c51-3acb2c5f3f8f`)
  -- loads `fact_station_day_scorecard` from analysis #4's real output
  (`docs/analysis/station_day_scorecard.csv`, uploaded to
  `Files/analysis/` via the ADR 0006 pattern), closing the plan's
  "analyses 2 and 4 write to gold" gap for analysis #4 (analysis #2's
  `fact_rebank_effect` already did this; #4 never had). Hit a real
  pyodbc performance bug fixed with `fast_executemany` + chunked
  inserts -- see [ADR 0014](decisions/0014-direct-lake-semantic-model-quirks.md#4-row-by-row-pyodbcexecutemany-stalls-at-real-not-tiny-row-counts).
  Ran clean: 8,028 rows.

- `nb_validate_semantic_model` (id `3b0cf071-21a8-42b2-b2a1-7e92aa4c77bf`)
  -- throwaway, not part of the pipeline: computes D0/A14/CF via T-SQL
  for 3 test slices (overall, DFW-Aug-2026, AA-2024) to cross-check
  against `sm_hubops`'s DAX measures for Phase 5's "done when". All
  three matched to 8+ significant figures.

## Semantic model

- `sm_hubops` (id `9fc47519-9010-455f-977f-d201c18f94e3`) -- Direct
  Lake over `wh_gold`, built via the `powerbi-modeling-mcp` MCP tool
  (EULA reviewed and accepted by the project owner first). 9 tables
  (`Date`, `Airport`, `Airport (Dest)`, `Carrier`, `Bank`, `Flight Leg`,
  `Aircraft Turn`, `Station Hour`, `Station Day Scorecard`), 9
  relationships, 25 measures matching `docs/kpi_spec.md` exactly (23
  from the initial build plus `Outbound D0 %` and `Late Aircraft Delay
  Share`, added for the report's Turn Performance and Delay Root Cause
  pages), 6 per-hub RLS roles (`Hub - DFW/CLT/MIA/ORD/PHX/PHL`, filtering
  `Airport`; role *membership* -- which real users belong to which role
  -- is a Portal action, not done here or by this tool). TMDL exported
  to `fabric/sm_hubops.SemanticModel/`. Three real Direct Lake/RLS
  quirks hit and resolved (or honestly documented as a known
  limitation) building it -- see
  [ADR 0014](decisions/0014-direct-lake-semantic-model-quirks.md).

## Report

- `rpt_hubops` (id `d8404b97-b98a-4228-97cf-3b86824de65c`) -- 7 pages,
  6 visible (Hub Scorecard, Bank Performance, Turn Performance, Delay
  Root Cause, Rebank Impact, Passenger Flow) + 1 hidden drillthrough
  target (Fleet Turn Detail, off Turn Performance's fleet charts) on
  `sm_hubops`, built with the `powerbi-report-authoring` CLI
  (`@microsoft/powerbi-report-authoring-cli`) via a deterministic Node
  generator (`scripts/fabric/build_report.js`) rather than hand-edited
  PBIR, per the skill's own guidance for full-report builds. Verified
  against live data with `powerbi-report-author preview --host service`
  and, once the user opened the pbip in Power BI Desktop, with the
  `powerbi-desktop` bridge CLI directly -- all 7 pages render clean,
  screenshots reviewed in both. `definition.pbir` binds via
  `byConnection` (a `byPath` reference to the co-located
  `sm_hubops.SemanticModel` folder validates locally but fails Fabric's
  import with *"Fabric REST API only supports byConnection
  references"* -- real error, not assumed) with the connection string's
  `semanticModelId` parameter set to `sm_hubops`'s item GUID (also
  required -- a bare workspace/dataset-name connection string fails
  import with `InvalidConnectionInformation`). Deployed via `az rest
  POST /v1/workspaces/{id}/reports` using the CLI's `pack --mode
  create` payload, the same create/poll pattern as every other item in
  this project.
- Two scope simplifications versus the plan's literal page descriptions,
  both because Direct Lake tables can't carry a calculated column (ADR
  0014) and `dim_bank` isn't joined to `fact_flight_leg` (a two-month
  prototype, not a continuous calendar) -- documented on the pages
  themselves via a textbox, not silently dropped: Bank Performance
  shows bank *volume* waves + a DFW pre/post-rebank D0/A14 comparison
  rather than delay-by-bank; Delay Root Cause shows cause-minutes/shares
  by month rather than by hour and bank.
- **Design pass (2026-11-01):** redesigned from a default-theme/static-
  title build to a real identity -- "Industrial Dense / Analyst
  Workbench" tone (custom theme `HubOpsWorkbench-a1f3.json`, steel-blue/
  burnt-orange palette, FHD 1920x1080, tabular-numeral KPI cards with
  YoY-status-conditional accent bars, FillRule gradient bars, data-bar
  tables), dynamic measure-bound page titles, and two real field
  parameters (`Hub KPI`, `Turn Metric`) exposed as tile-slicer pickers
  for analyst self-service in Desktop. Hit a genuinely dangerous bug
  along the way -- `SELECTEDVALUE` on a field parameter's display column
  hangs (not errors) when evaluated outside a proper visual binding,
  confirmed via a 120s+ stuck DAX query -- see
  [ADR 0016](decisions/0016-report-design-pass-field-parameter-bug.md)
  for the full bisection and fix. Redeployed via `updateDefinition` to
  the same report item; all 4 pages re-validated and re-rendered clean.
- **Pages 5-6 + drillthrough + a11y/UX pass (2026-11-01):** added Rebank
  Impact (event-study diff-in-diff vs ORD control) and Passenger Flow
  (TSA/CBP hourly profiles), consolidated three same-shape/different-
  measure bar-chart trios on pages 2-4 into single multi-series or dual-
  axis (`lineClusteredColumnComboChart`) visuals, and added a real
  drillthrough page (Fleet Turn Detail) instead of a fourth near-
  identical "by Fleet Family" bar chart. Fixed three real defects found
  by actually opening the pbip in Power BI Desktop and screenshot-
  reviewing it, not just schema-validating: a theme gold (`#C9A227`)
  that failed WCAG non-text contrast (2.4:1, needs 3:1) on white,
  darkened to `#8A6D14` (4.9:1); two field-parameter tile-slicer pickers
  that clipped/truncated once they had more than ~2 options, replaced
  with dropdown slicers; and a monthly-grain supporting-panel line chart
  whose axis labels collided into unreadable overlapping text in a
  ~600px-wide panel, changed to yearly grain. See
  [ADR 0017](decisions/0017-drillthrough-and-accessibility-pass.md).
- **AA-inspired palette, removed non-functional metric pickers
  (2026-11-01):** the `Hero Chart Metric`/`Chart Metric` dropdowns never
  actually swapped a chart's plotted measure for a report *viewer* --
  field parameters can only be dragged onto an axis in Desktop's edit
  mode, and pre-wiring that into the shipped PBIR is the confirmed
  `InvalidUnconstrainedJoin` dead end from ADR 0016. Removed both
  dropdowns rather than ship a control that visibly did nothing; the
  `Hub KPI`/`Turn Metric` field parameter tables stay in `sm_hubops` for
  anyone who wants to use them in Desktop directly. Shifted the theme's
  two primary tokens to American Airlines' public brand colors (navy
  `#003865`, red `#C8102E` -- no logo/wordmark, per CLAUDE.md's "no AA
  logos" rule), gave every page title a matching navy color + a short
  red accent rule, and renamed the theme file (cache-busting) to
  `HubOpsWorkbench-2f91.json`. See
  [ADR 0018](decisions/0018-aa-palette-and-dead-slicer-removal.md).
- **Typography and spacing pass (2026-11-01):** KPI cards were mostly
  dead space (24pt value in a 120px card, no vertical centering) and
  every chart carried a redundant, tiny Power BI auto-subtitle under
  its real title -- `subTitle.show` was never explicitly set to
  `false` anywhere. Fixed both, bumped table/axis/legend text 1pt
  across the theme (new file `HubOpsWorkbench-5a1c.json`), and added
  theme styling for `lineClusteredColumnComboChart` (it had none before
  this pass, so the combo charts were silently off-theme). Caught and
  fixed a real row-overlap regression the bigger table font caused on
  two pages' detail tables before deploying -- see
  [ADR 0019](decisions/0019-typography-and-spacing-pass.md).
