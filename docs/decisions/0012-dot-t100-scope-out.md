# ADR 0012: DOT Consumer Report and T-100 are out of scope

**Status:** done, 2026-10-29 (decided in conversation; formalized here
after the fact -- this ADR closes the gap between that decision and the
repo, which still read as if DOT/T-100 were in scope).

## Context

The original plan (`planning/project-plan.md`) lists seven sources,
including the DOT Air Travel Consumer Report (mishandled bags, complaints,
denied boardings -- feeds `fact_carrier_month_customer` and the Phase 3
reconciliation target) and BTS T-100 Domestic Segment (seats/passengers,
feeds `fact_route_month`, load factor next to reliability).

Mid-build, evaluating whether to automate the TSA FOIA pull, the project
owner raised: *"is dot really necessary for this project? I want the
project to standout ofcourse but also come across as realistic and not
far-fetched."* Discussion landed on dropping DOT (and, by the same
reasoning, T-100): both require manual PDF/export handling for data that
mainly adds customer-complaint and load-factor context, not reliability
signal central to the rebank question this project is actually answering.
Five real sources (BTS, TSA, CBP, FAA, IEM weather) are enough to carry a
defensible, non-overreaching scope -- adding two more manual-download
sources for tangential metrics was assessed as more likely to look
padded than impressive.

This decision was acted on immediately (TSA was scoped to the diff-in-diff
window rather than full history, on the same reasoning) but never written
down. `CLAUDE.md`, `planning/project-plan.md`'s "Data sources"/"Gold data
model"/"KPI definitions" sections, and Phase 3's "done when" check all
still read as if DOT and T-100 were in scope -- a real documentation gap
that would confuse anyone (including a future Claude Code session)
picking this repo up expecting those sources to exist.

## Decision

- **DOT Air Travel Consumer Report and BTS T-100 Domestic Segment are out
  of scope.** Not deferred, not "later if time" -- dropped.
- `fact_carrier_month_customer` and `fact_route_month` are dropped from
  the Gold data model. Nothing downstream depends on them.
- The "second DOT-basis A14" edge case in the KPI spec is dropped --
  there's only one A14, computed on BTS basis.
- Phase 3's "done when" (*AA's DOT-basis on-time rate within 0.1 point of
  Consumer Report Table 1*) is replaced with an internal reconciliation
  check that needs no DOT data: **gold-layer D0/A14/CF, aggregated by
  month, must match the same metrics computed directly from
  `lh_bronze.bts_marketing_ontime` (the untransformed source) to the
  decimal.** This still catches real bugs (a broken join, a dropped
  partition, a miscounted cancellation) -- it just can't catch the one
  class of bug DOT reconciliation would (BTS and AA's internal systems
  disagreeing) because this project was never going to have AA's internal
  numbers to compare against anyway.
- Report page 8's "reconciliation to DOT published figures" becomes
  "reconciliation to BTS raw aggregates" -- same idea, real data on both
  sides, just without the source that was cut.

## Consequences

- Five sources carry the whole project: BTS, TSA, CBP, FAA, IEM weather.
  That's still three genuinely messy wrangling problems (BTS's hhmm/2400/
  overnight rollover, TSA's PDFs, raw METAR) -- the "three sources messy
  on purpose" framing from the plan's intro still holds with five, not
  seven.
- `CLAUDE.md` and `planning/project-plan.md` are updated alongside this
  ADR to stop referencing DOT/T-100 as in-scope, so this gap doesn't
  resurface for a future session reading those files cold.
- Any later interview conversation about "why not DOT/T-100" has a real
  answer: scope discipline, not an oversight -- five real sources
  answering one real question beats seven sources half-used.
