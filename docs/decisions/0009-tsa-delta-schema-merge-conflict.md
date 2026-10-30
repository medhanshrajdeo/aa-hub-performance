# ADR 0009: Delta schema-merge conflict from the earlier per-week prototype table

**Status:** done, 2026-10-30.

## Context

After pivoting to local parsing (ADR 0008) and uploading the combined
35-week CSV, `nb_bronze_tsa`'s write still failed:

```
AnalysisException: [DELTA_FAILED_TO_MERGE_FIELDS] Failed to merge fields 'date' and 'date'
```

Cause: `lh_bronze.tsa_checkpoint_hour` already existed from the very
first successful single-week test (run interactively, back when
`nb_bronze_tsa` was still the per-week PDF-parsing prototype -- see ADR
0007). That early version wrote `date` as a plain string (it came
straight from `carry.date.isoformat()` with no cast). The finalized
notebook explicitly casts it with `to_date(col("date"))`, producing a
real `DateType`. Delta refuses to silently reconcile two different types
under the same column name on a plain `mode("overwrite")` -- it has no
way to know whether that's an intentional schema change or a mistake.

This diagnosis needed the real per-cell traceback, which (same as ADR
0004's and 0007's findings) the REST job-status API could not provide --
confirmed again, in this same debugging session, that `getDefinition`
and the Livy sessions API also don't surface statement-level errors.
The portal's run history remains the only reliable source for this.

A related but separate issue surfaced while chasing this: **both
`updateDefinition` and `getDefinition` are asynchronous** (202 Accepted
with an `Operations` polling URL), the same pattern as item creation.
An earlier push-then-immediately-run attempt picked up stale (pre-fix)
notebook content because the update operation hadn't actually finished
propagating yet -- confirmed by fetching the live definition back via
`getDefinition` and finding it unchanged. `run_bts_backfill.sh` already
had a defensive `sleep 10` for this; the one-off manual TSA commands
didn't, and paid for it.

## Decision

Added `.option("overwriteSchema", "true")` to the write, since this load
*is* an intentional full replace of the prototype table's single test
week with the real 35-week dataset -- not an accidental type drift to
paper over.

For future one-off notebook definition pushes (not just the scripted
backfills that already had this): **poll the `updateDefinition`
operation to `Succeeded`, and ideally verify via `getDefinition`, before
triggering a run.** Don't assume a 202 means the content is live yet.

## Consequences

- `lh_bronze.tsa_checkpoint_hour` is now the full, correct 35-week table
  (confirmed: 35 `week_start` partitions, matching the full Jan-Aug 2026
  window).
- This closes out Bronze for all five sources this project uses
  automated or semi-automated ingestion for (BTS, FAA, weather, CBP,
  TSA) -- DOT and T-100 remain explicitly out of scope per earlier
  scoping decisions.
