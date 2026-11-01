# ADR 0020: Phase 8 scope -- what got hardened and what's genuinely blocked

**Status:** done (for the achievable subset), 2026-11-01.

## Context

Phase 8 in `planning/project-plan.md` bundles a lot: idempotent
watermarked loads, schema contracts, data quality, reconciliation,
orchestration + alerts, CI/CD (Git integration, dev/prod deployment
pipeline, Variable Library), security/governance, performance, docs,
and a `sql/adhoc/` folder for SSMS. Several of these were already done
in earlier phases (schema contracts since Phase 1, MLV data quality
since Phase 2, reconciliation since Phase 3/ADR 0013, RLS since
Phase 5). What's genuinely new in this pass, and what's genuinely
blocked, below.

## Done this pass

- **`sql/adhoc/`**: six real T-SQL queries against the live `wh_gold`
  schema for common data-request patterns (monthly KPIs by station,
  weather-adjusted worst-day lookup, fleet turn-cliff lookup, delay-
  cause breakdown, rebank effect sizes, data-health snapshot) --
  runnable directly from SSMS, the job posting's own tool ask.
- **`dbo.load_log`** + `fabric/nb_meta_health.Notebook`: a verification
  sweep that `COUNT(*)`s every real gold table and records
  source/row_count/status/checked_at_utc, plus one fixed row carrying
  the already-confirmed full bronze-vs-gold reconciliation (ADR 0013).
  This is the practical shape "idempotent, watermarked loads" takes
  here -- see the real finding below for why it's `dbo`, not the
  `meta` schema the project plan names, and why it's a re-runnable
  snapshot rather than the plan's literal `meta.source_registry`
  watermark table (this project's ingest ran as a one-time historical
  backfill, not an incremental schedule -- building watermark tracking
  for a schedule that doesn't exist yet would be unused scaffolding;
  worth doing when/if this pipeline moves to incremental loads).
- **Data Health report page** (page 8): the project plan's own Phase 8
  "done when" names it explicitly ("page 8 shows green for all
  sources"). Built on `dbo.load_log` via a new `Load Log` Direct Lake
  table in `sm_hubops` -- KPI cards (sources tracked, checks OK,
  total rows, last sweep time), a rows-by-source bar chart, and the
  full sweep detail table.
- **Performance**: `wh_gold` is a Fabric Warehouse, which applies
  V-Order and background table maintenance automatically -- nothing to
  add there. `lh_bronze`/`lh_silver` are Lakehouses with Fabric's Spark
  runtime defaulting `vorder.enabled` to true already; no notebook in
  this project disables it. Not adding explicit `OPTIMIZE` calls to
  the ingest notebooks this pass -- the backfill is a completed,
  one-time load, not a frequently-rewritten table where file
  fragmentation would accumulate.

## Two real bugs found building this (not hypothetical)

1. **A custom Direct Lake schema makes table creation hang forever,
   not just take longer.** The first `Load Log` table attempt used
   `schemaName: "meta"` (a brand-new schema, following the plan's own
   naming). The `Create` call, and every subsequent call touching that
   table or even the model's connection, hung past 120s repeatedly --
   this looked like the already-documented OneLake-discovery delay
   pattern (ADR 0017), but waiting (even 180s, even reconnecting) never
   resolved it. Reconnecting fresh and calling `List` revealed the real
   cause: the table had never actually been created at all -- the
   original call itself had a malformed request (a nested
   `partitions`/`source` structure that doesn't match this tool's flat
   schema: `entityName`/`schemaName`/`mode`/`expressionSourceName`
   directly on the table definition, confirmed by inspecting a working
   table's `Get` output) and silently wedged the connection instead of
   erroring. Reissuing with the correct flat schema *and* a plain
   `dbo` schema (matching every other table in this project) worked
   immediately, with the normal ~1-2 minute Direct Lake framing delay.
   Genuinely uncertain whether `dbo` specifically was the fix or just
   the corrected request shape was -- not re-tested against `meta`
   given the cost of another multi-minute hang, so the honest
   conclusion is "match every other table and use `dbo`," not "`meta`
   schemas are provably broken in Direct Lake."
2. **`CALCULATE(SUM(X), Table[Col] <> Y)` silently ignores the
   visual's own filter on that same column.** The "Rows Tracked by
   Source" bar chart initially showed every single bar at the same
   grand total (55M) instead of each source's own subtotal. Cause: an
   explicit boolean filter argument on a column inside `CALCULATE`
   *replaces* any filter already in context on that column rather than
   intersecting with it -- so the chart's own per-category filter on
   `Load Log[Source]` got silently overwritten by the measure's own
   `'Load Log'[Source] <> "reconciliation"` filter, which only
   evaluates against the *unfiltered* table. Fixed by wrapping the
   filter argument in `KEEPFILTERS(...)`, which intersects instead of
   replacing. Affected (and fixed) four measures: `Sources Tracked`,
   `Checks Tracked`, `Checks OK`, `Total Rows Tracked`. Caught by
   actually looking at the rendered chart rather than just spot-
   checking one unfiltered card value -- the cards all looked correct
   (55M, 7, 18/18) precisely because none of them were ever evaluated
   in a per-source filter context, which is exactly what made this bug
   invisible until the chart rendered.

## Explicitly not attempted -- real blockers, not skipped quietly

- **Fabric Git integration to GitHub**: the GitHub Git-provider option
  is greyed out for this workspace (a tenant-level setting is off --
  confirmed in `docs/fabric_workspace.md` back in Phase 0). Needs
  either a tenant admin enabling that setting, or standing up an Azure
  DevOps org (which doesn't exist for this project). Neither is
  something this session can do.
- **Dev-to-prod deployment pipeline**: needs a second ("prod")
  workspace and, in the standard Fabric pattern, Git-connected stages
  -- which circles back to the same blocker above. Could create a bare
  second workspace via `az rest` without Git integration, but a
  deployment pipeline without stage-to-stage content promotion isn't
  meaningfully different from just redeploying by hand to two places,
  so this wasn't done speculatively.
- **Orchestration pipeline + Outlook failure alerts**: the ingest
  notebooks already ran successfully as a one-time backfill, so there's
  no live schedule this pipeline would actually be orchestrating yet.
  The alert half specifically needs an Office 365 mail connection tied
  to a real mailbox -- not something to wire up without the project
  owner's explicit say-so, since it would mean this session could send
  real email on their behalf.
- **Fabric data agent**: explicitly marked optional in the plan.

## Consequences

- `rpt_hubops` is now 8 pages (7 visible + 1 hidden drillthrough
  target), redeployed via the same `updateDefinition` pattern, same
  item id. All pages validated clean, rendered clean against live
  data -- including a second render specifically re-checking the bar
  chart after the `KEEPFILTERS` fix, not just a code-level fix taken
  on faith.
- `sm_hubops` gained one table (`Load Log`, Direct Lake over
  `dbo.load_log`) and 9 measures.
- `docs/data_dictionary.md` gained entries for `dbo.load_log` and the
  two passenger-flow profile tables from ADR 0017 that were missed at
  the time.
- Everything genuinely left in Phase 8 (Git integration, deployment
  pipeline, orchestration + alerts, Variable Library) is blocked on
  tenant/workspace/mailbox access this session doesn't have, not on
  remaining work -- see `docs/runbook.md`'s "Not yet built" section,
  updated to match.
