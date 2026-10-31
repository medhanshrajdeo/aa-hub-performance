# ADR 0010: Four real bugs building `nb_silver_flights`'s time resolution

**Status:** done, 2026-10-31.

## Context

Resolving BTS's local-clock, no-timezone hhmm fields into real UTC
instants (`hubops.bts_times`-equivalent logic, reimplemented inline per
ADR 0008's pattern) took four real rounds of debugging against the live
workspace, not counting ADR 0011's data-quality finding.

1. **`StructType.add()` mutates in place.** Building the `mapInPandas`
   output schema as `bts.schema.add(StructField(...))` corrupted `bts`'s
   own schema object, producing `UNRESOLVED_COLUMN` errors for columns
   that hadn't even been added yet. Fixed by constructing a genuinely new
   `StructType(list(bts.schema.fields) + [...])`.

2. **Wrong `mapInPandas` function signature.** `mapInPandas` calls its
   function with an *iterator* of pandas DataFrame batches and expects an
   iterator back -- not a single DataFrame in, DataFrame out. The
   original single-DataFrame function failed with
   `AttributeError: 'map' object has no attribute 'apply'` because Spark
   was handing it a `map` object, not a DataFrame. Fixed by wrapping the
   real logic in `def resolve_batch(iterator): for pdf in iterator: yield
   _resolve_one_batch(pdf)`.

3. **`array_remove(array, NULL)` doesn't strip nulls.** SQL's `NULL =
   NULL` is `NULL` (unknown), not `TRUE`, so `array_remove` never matched
   the null reason-code entries it was meant to filter out of
   `quarantine_reasons`. Per-column diagnostic counts showed real hits
   (5,042 / 9,980 / 29) while the combined array's size was always 0 --
   that mismatch is what surfaced it. Fixed with the higher-order
   `filter(array, lambda x: x.isNotNull())` function instead, which uses
   an explicit predicate.

4. **Actual departure/arrival dates can roll past midnight; `FlightDate`
   doesn't tell you that.** `FlightDate` is BTS's *scheduled* departure
   date -- a safe anchor for `CRSDepTime`. There's no equivalent
   authoritative column for the *actual* departure date: a long delay can
   push `DepTime` past local midnight into the next calendar day, and
   anchoring it to `FlightDate` regardless produces an actual timestamp
   that's off by up to ~24 hours. Caught via validation: "dep delay match
   within 1 min" sat at 99.232%, with mismatches around -1,360 minutes
   (not noise -- a systematic ~23 hour error), not the ~100% the
   construction should have produced if resolution were correct.
   Confirmed with a real example: `CRSDepTime=2305`, `DepTime=0023`,
   `DepDelay=78.00` -- the flight really did depart 78 minutes late,
   rolling into the next day; anchoring `0023` to the scheduled day
   instead produced a timestamp ~23 hours too early.

   **This is also a methodology lesson worth keeping:** the original
   validation checked *internal consistency* (recomputed elapsed time
   matches `CRSElapsedTime`) rather than an *independent ground truth*.
   A candidate-date-search algorithm can compensate an error on one end
   with an error on the other and still look internally consistent while
   being absolutely wrong. The fix needed a check against something the
   resolver didn't get to touch: BTS's own reported `DepDelay`/`ArrDelay`.

## Decision

- Schema construction, `mapInPandas` signature, and null-filtering are
  now fixed as described above.
- **Redefined `actual_dep_utc`/`actual_arr_utc` as `sched_dep_utc +
  DepDelay_minutes` / `sched_arr_utc + ArrDelay_minutes`**, instead of
  independently re-resolving them from `DepTime`/`ArrTime` + `FlightDate`.
  BTS's own delay columns are already the authoritative, day-aware offset
  from a timestamp (`sched_*_utc`) that *is* correctly resolved -- reusing
  them sidesteps the rollover ambiguity entirely rather than trying to
  re-derive it.
- Replaced the now-tautological "dep delay match" validation (comparing
  a value to the exact formula that defined it) with a genuinely
  independent check: does `actual_arr_utc - actual_dep_utc` match BTS's
  own reported `ActualElapsedTime`? That only holds if `sched_dep_utc`
  and `sched_arr_utc` were both resolved correctly in the first place.
- A fifth bug surfaced implementing the above: `pd.Timedelta(minutes=d)`
  raised `ValueError: cannot convert float NaN to integer` despite a
  `_to_float()` helper and `d is not None` guards. Root cause: when
  `.apply()` returns a mix of `None` and `float` values, pandas silently
  coerces the resulting Series to `float64`, turning every `None` into
  `NaN` -- and `NaN is not None` is `True` in Python, so the guard never
  caught it. Fixed by checking `pd.notna(...)` instead of `is not None`
  everywhere a value crossing this boundary is tested (also applies to
  `sched_dt`, which can similarly surface as `NaT`, not `None`).

## Consequences

- On the Aug-2026 test partition (673,065 rows): elapsed-time match
  99.999% (656,477/656,482; 5 remaining mismatches are a genuine BTS-side
  anomaly, not a resolver bug -- see sample rows in the notebook's
  `CELL_3_VALIDATE` output, including a same-tail same-day roundtrip with
  mismatches on both legs, consistent with an irregular-ops day rather
  than a parsing error).
- General lesson for any future pandas/Spark boundary code in this repo:
  prefer `pd.notna()`/`pd.isna()` over `is None`/`is not None` whenever a
  value has passed through a pandas `Series.apply()` that can return
  `None` -- the dtype coercion risk is real, not hypothetical.
