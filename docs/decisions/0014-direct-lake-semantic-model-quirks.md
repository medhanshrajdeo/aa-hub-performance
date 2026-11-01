# ADR 0014: Direct Lake semantic model -- real quirks hit building sm_hubops

**Status:** done, 2026-11-01.

## Context

Phase 5 needed a Direct Lake semantic model (`sm_hubops`) over `wh_gold`,
built via the `powerbi-modeling-mcp` tool (EULA reviewed and accepted by
the project owner first -- the tool refuses to self-accept). Four real
problems surfaced during the build, none hypothetical.

## 1. The actual workspace display name is `AmericanAirlines`, not `aa-hubops-dev`

`planning/project-plan.md` Phase 0 and `docs/fabric_workspace.md` both
call the workspace `aa-hubops-dev` -- that was the *plan*, but the
workspace that actually got created (`az rest GET
/v1/workspaces/{id}`) has `displayName: "AmericanAirlines"`. Nobody
caught this because every later `az rest` call already used the
workspace ID directly, not the name -- the mismatch only mattered once
`ConnectFabric`/`DeployToFabric` needed the name. Fixed by using the
real name going forward; the ID (`16b2a9c6-...`) was never wrong.

## 2. Direct Lake tables cannot carry DAX calculated columns

Tried adding a `Dep Hour Utc` calculated column (`HOUR('Flight
Leg'[Sched Dep Utc])`) to `Flight Leg` for by-hour slicing. Deploy
failed: *"Standard expression context may not be used for calculated
columns in Direct Lake tables."* Direct Lake is pure passthrough from
OneLake -- no row-by-row DAX evaluation at load time, unlike Import
mode. Deleted the column; an hour-of-day attribute for the delay-root-
cause page needs to be a real materialized column in
`fact_flight_leg` itself (gold-layer work, not semantic-model work),
not derived here.

## 3. `USERELATIONSHIP` is incompatible with RLS -- confirmed, not assumed

`docs/kpi_spec.md`'s own reference DAX for `Hub Throughput pts` uses
`USERELATIONSHIP` to flip `Flight Leg`'s Airport relationship from
origin to destination. Built exactly as specified, then tested under
the `Hub - DFW` role via `dax_query_operations`' `impersonation.roles`
-- it hard-errors: *"The UseRelationship() and CrossFilter() functions
are not available in this context."* This is a real, documented Power
BI/AS engine restriction, not a modeling mistake: relationship-function
overrides are blocked under row-level security by design.

**Fix attempted:** added a role-playing `Airport (Dest)` table (no RLS
of its own) with an *active* relationship from `Flight Leg[Dest Airport
Key]`, and rewrote `A0 %` to use `TREATAS(VALUES('Airport'[Airport
Key]), 'Airport (Dest)'[Airport Key])` instead of `USERELATIONSHIP` --
the standard substitute, and it does avoid the hard error.

**Residual, accepted limitation:** `A0 %` (and therefore `Hub
Throughput Pts`) still returns *blank* under a per-hub RLS role, not
the correct inbound-arrival figure. Root cause, confirmed by testing:
the `Hub - DFW` role's security filter lives on `Airport` (origin
side) and propagates through the *active* `Flight Leg -> Airport`
relationship -- this propagation cannot be removed even with
`REMOVEFILTERS('Airport')` (RLS filters are deliberately unremovable).
So under that role, `Flight Leg` is already implicitly restricted to
`Origin Airport Key = "DFW"`; adding a `Dest Airport Key = "DFW"`
filter on top (via `TREATAS`) asks for `Origin = DFW AND Dest = DFW`,
which is empty by construction (no leg departs and arrives at the same
station). This is RLS correctly doing its job, not a bug to route
around cheaply -- a real fix means moving the security filter onto
`Flight Leg` directly with an `OR` across both FK columns and a
disconnected "current hub" lookup table for `SELECTEDVALUE`-driven
explicit filtering, which is a bigger RLS redesign than this phase's
scope. Documented directly in both measures' descriptions (and here)
rather than silently shipping a wrong number or a hard error. `Hub
Throughput Pts` works correctly for unrestricted/admin use; for
RLS-restricted hub leaders it's a known gap, not a hidden one.

## 4. Row-by-row `pyodbc.executemany` stalls at real (not tiny) row counts

`nb_gold_build`'s established pattern for loading small analysis-output
CSVs into gold (`dim_bank`, `fact_rebank_effect`, `fact_rebank_event_study`
-- 35/3/16 rows) is plain `cursor.executemany(...)`, one round-trip per
row. Reused the same pattern for `fact_station_day_scorecard` (8,028
rows, signature analysis #4's real output -- see the "done when" note
below) and it repeatedly stalled mid-insert (observed stuck at 3,002,
then 5,157, then 1,392 rows across separate attempts) rather than
completing in a reasonable time. Fixed by setting
`cursor.fast_executemany = True` and inserting in 1,000-row chunks
(`scripts/fabric/build_gold_station_scorecard_notebook.py`) -- the
well-known pyodbc+SQL-Server-ODBC-driver fix for exactly this failure
mode. **Any future CSV load into gold with more than a few dozen rows
should use `fast_executemany` + chunking from the start**, not the
plain `executemany` pattern that was fine for 35-row tables.

Separately: the Fabric job-status REST API (`jobs/instances/{id}`)
showed `status: "InProgress"` long after a run had actually finished
(or failed) in more than one case this session -- the
`Files/diagnostics/*.json` pattern from ADR 0013 was the only reliable
way to tell what a run actually did, again.

## Consequences

- `sm_hubops` is live in the `AmericanAirlines` workspace: 8 tables
  (`Date`, `Airport`, `Airport (Dest)`, `Carrier`, `Bank`, `Flight
  Leg`, `Aircraft Turn`, `Station Hour`, `Station Day Scorecard` --
  9 tables total), 9 relationships, 23 measures, 6 per-hub RLS roles.
  TMDL exported to `fabric/sm_hubops.SemanticModel/`.
- Phase 5's "done when" (D0/A14/CF match SQL to the decimal for three
  test slices) passed: overall, DFW-Aug-2026, and AA-2024 all matched
  to 8+ significant figures -- see `fabric/nb_validate_semantic_model.Notebook`.
- `fact_station_day_scorecard` now exists in `wh_gold` (closes the
  plan's "analyses 2 and 4 write to gold" gap for analysis #4, which
  had only ever produced a local CSV).
- Report pages (1-4) are not yet built -- that's the next piece, not
  part of this ADR.
