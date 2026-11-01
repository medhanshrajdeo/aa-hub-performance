# ADR 0013: Gold layer built via pyodbc + cross-database T-SQL from inside a Spark notebook

**Status:** done, 2026-10-31.

## Context

Phase 3 needs a real star schema in `wh_gold`, populated by T-SQL (the
plan's own words: "Gold lives in a Warehouse because the posting asks
for SQL and SSMS, and stored procedures keep gold logic reviewable").
This project has no system Python locally with `pyodbc` or a modern SQL
Server ODBC driver (confirmed: only the legacy Windows "SQL Server"
driver is installed, which doesn't support Azure AD access-token auth),
and no public Fabric REST API executes arbitrary T-SQL against a
Warehouse.

Two throwaway diagnostics (not committed) resolved this before writing
any real DDL:

1. Does Fabric's default Spark notebook runtime have `pyodbc` + a modern
   ODBC driver, and does `notebookutils.credentials.getToken(...)` work?
   **Yes** -- `pyodbc.drivers()` returned `['ODBC Driver 18 for SQL
   Server']`, and a token (length 3156) was acquired successfully.
2. Do Fabric Warehouses support cross-database T-SQL queries against a
   Lakehouse's Delta tables in the same workspace (three-part names like
   `[lh_silver].[dbo].[fact_flight_leg]`)? **Yes** -- `SELECT COUNT(*)`
   across that table returned 27,787,225, matching the full silver run
   exactly.

Together, these mean the entire Gold build runs as T-SQL executed via
`pyodbc` from inside a Spark notebook (same `az rest`
create/updateDefinition/run/poll pattern as every other notebook this
project uses), with the big loads (`fact_flight_leg`, `fact_checkpoint_hour`,
`fact_cbp_wait_hour`, `dim_airport`, `dim_aircraft`) as pure server-side
`INSERT INTO ... SELECT ... FROM [lakehouse].[dbo].[table]` -- no data
moves through Python or Spark DataFrames for those.

## Decision

- Small dims (`dim_date`, `dim_time_15`, `dim_carrier`, `dim_delay_cause`,
  `dim_cancel_reason`) are generated in Python and inserted via
  `pyodbc.executemany` -- simpler and more testable than a T-SQL
  recursive CTE I can't run interactively before committing to it.
  `dim_date`'s holidays are the real US federal holiday observed dates
  (`scripts/fabric/compute_us_holidays.py`'s Nth-weekday-of-month /
  fixed-date-with-nearest-weekday-observance rules, verified locally
  before use), not a hand-typed list.
- The three locally-computed analysis outputs (`dim_bank`,
  `fact_rebank_effect`, `fact_rebank_event_study` -- from
  `analysis/dfw_bank_reconstruction.py` and
  `analysis/dfw_rebank_diffindiff.py`) are uploaded to
  `Files/analysis/*.csv` via the established PowerShell OneLake pattern
  (ADR 0006), read with Spark, and inserted via `pyodbc.executemany` --
  tiny (35/3/16 rows), no reason to invent a bulk-load path for them.
- `dim_carrier`'s 21 codes are the exact real set observed in
  `lh_silver.fact_flight_leg` (checked via a throwaway diagnostic, not
  guessed) -- see the script's `REAL_CARRIERS` comment for why
  `is_aa_family` is TRUE only for AA itself and its wholly-owned regional
  subsidiaries (Envoy/PSA/Piedmont), not independent multi-carrier
  regionals like SkyWest or Mesa that sometimes fly under AA's brand.
- **Every cell writes its result, or on failure its full
  `traceback.format_exc()`, to `Files/diagnostics/<name>.json`** (read
  back via the OneLake DFS API) instead of relying on printed cell
  output. The Fabric REST job-status API has never once surfaced a
  cell-level error this entire project (confirmed again here) -- this
  makes every future run self-diagnosable without portal access, which
  mattered directly: both real bugs below were caught and fixed in two
  round trips total, entirely autonomously, using only this mechanism.

## Two real bugs, found via the diagnostic files (not portal access)

1. **`DATETIME2` needs explicit precision in Fabric Warehouse.** Bare
   `DATETIME2 NULL` failed: `An integer precision value between 0 and 6
   must be specified.` Fixed with `DATETIME2(6)` everywhere.
2. **`checkpoint` is a reserved T-SQL keyword.** Using it as a bare
   column name in `CREATE TABLE dbo.fact_checkpoint_hour (... checkpoint
   VARCHAR(50) ...)` failed: `Incorrect syntax near the keyword
   'checkpoint'.` Fixed by bracket-quoting it (`[checkpoint]`) in the DDL
   and the subsequent `INSERT ... SELECT`.

Both were caught in the *same* run (the per-statement try/except in the
DDL cell kept going after the first failure instead of aborting on it),
diagnosed from `Files/diagnostics/gold_ddl.json`'s captured tracebacks,
fixed, and resolved on the next push -- zero portal checks needed for
either.

## Consequences

- `wh_gold` now has a real, reviewable star schema: `dim_date`,
  `dim_time_15`, `dim_airport`, `dim_carrier`, `dim_aircraft`,
  `dim_delay_cause`, `dim_cancel_reason`, `dim_bank`, `fact_flight_leg`
  (27,787,225 rows), `fact_checkpoint_hour` (1,607,461),
  `fact_cbp_wait_hour` (20,740), `fact_rebank_effect` (3),
  `fact_rebank_event_study` (16).
- Phase 3's revised "done when" (ADR 0012 -- reconciliation against BTS
  raw aggregates, not DOT) passed cleanly: max D0 diff 0.0000924, max CF
  diff 0.0000087 across all 44 months, both far under the plan's
  0.1-point target.
- `fact_aircraft_turn` (the plan's "table nobody else builds from public
  data") and `fact_station_hour` (needs a weather-severity transform)
  are not yet built -- next.
- General pattern for the rest of this project: any future Warehouse
  work reuses this pyodbc-from-a-notebook approach rather than
  reinventing a connection method, and any future notebook of any kind
  should write results to `Files/diagnostics/` from the start rather
  than relying on printed output -- the cost of adding it upfront is
  trivial next to a single avoided portal round trip.
