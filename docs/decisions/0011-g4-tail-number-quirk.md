# ADR 0011: Allegiant (G4) tail numbers are missing their leading "N"

**Status:** done, 2026-10-31.

## Context

`nb_silver_flights`'s malformed-tail quality check (`^N[0-9A-Z]{1,5}$`,
only applied to non-blank values -- blank is expected for some cancelled
legs, not malformed) quarantined 9,980 of 673,065 rows (1.488%) on the
Aug-2026 test partition, blowing past the plan's <0.5% quarantine target
and concentrated almost entirely in one reason code.

A throwaway diagnostic notebook (`nb_diag_tail`, built and run the same
way as every other notebook this project -- see ADR 0002 -- but never
committed, since it's not part of the pipeline) isolated the failing
values: **100% of the 9,980 rows belonged to `Operating_Airline == "G4"`
(Allegiant Air)**, and the values themselves (`816NV`, `822NV`, `338NV`,
...) are real Allegiant tail numbers with the leading `N` missing.
Allegiant themes its Las Vegas-based fleet's registrations as `N###NV`;
this BTS extract drops the `N` for this carrier specifically. This is a
deterministic, carrier-specific export quirk in the government's own
data -- not malformed data, and not something to fabricate a value for
(see `CLAUDE.md`'s "no synthetic data" rule: this is reconstruction of a
known, provable truncation, not invention).

## Decision

Normalize at ingestion, before any quality check runs: if
`Operating_Airline == "G4"` and `Tail_Number` matches `^[0-9]{2,4}NV$`,
prepend `"N"` to restore the real tail. Applied directly to the
`Tail_Number` column (not just the validation check) so the corrected
value flows through to `fact_flight_leg` and is available later for
turn-pairing, which keys on `Tail_Number`.

Left quarantined: 83 rows that still fail the tail-number regex after
this fix (not G4, or don't match the `NV`-suffix pattern) -- these are
rare enough (0.012% of all rows) to treat as genuine anomalies rather
than chase down a second carrier-specific pattern.

A related bug surfaced writing this fix's own diagnostic print: the
"rows fixed" count was computed by filtering the *already-corrected*
`bts` DataFrame against the same predicate used to find the broken rows
-- by that point every fixed tail already has its `N` restored, so the
filter always matched 0 rows, even though the real fix worked (confirmed
independently by the quarantine count dropping from 9,980 to 83). Fixed
by computing the count before reassigning `bts`.

## Consequences

- Quarantine rate on the test partition dropped from 1.488% to 0.017%
  (117/673,065), comfortably under the plan's <0.5% target.
- 9,897 Allegiant legs get correct `Tail_Number` values and are no
  longer excluded from `fact_flight_leg` -- relevant for any later
  hub-throughput KPI that counts all carriers' operations, and for
  turn-pairing if Allegiant ever needs to be included in that analysis.
- General lesson: a quarantine reason code concentrated almost entirely
  in one value of some other column (here, `Operating_Airline`) is a
  strong signal to check whether it's a source-specific formatting quirk
  before assuming the rows are actually bad.
