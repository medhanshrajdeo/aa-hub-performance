# KPI Spec

Formula, grain, owner table and edge cases for every metric in this project.
Code and DAX must match this spec exactly -- if they disagree, this doc wins
and the code gets fixed. See `planning/project-plan.md` for how each KPI
maps to the job posting and the Cranky Flier / View from the Wing sourcing.

Use the airline's own vocabulary: D0, A14 and CF are the three numbers AA
operations leadership ties employee payouts to, so precision here matters
more than anywhere else in the project.

| KPI | Grain | Owner (this project) | Definition | Edge case |
| --- | --- | --- | --- | --- |
| D0 | Flight leg | hubops | Operated departures leaving the gate at or before schedule, over operated departures | Cancelled legs excluded from both numerator and denominator |
| A14 | Flight leg | hubops | Operated arrivals within 14 minutes of schedule | BTS basis only -- see [ADR 0012](decisions/0012-dot-t100-scope-out.md) for why there's no second DOT-basis version |
| CF | Flight leg | hubops | 1 minus cancelled over scheduled | Diverted counts as completed, not cancelled |
| Controllable CF | Flight leg | hubops | 1 minus carrier-caused (cancel code A) cancels over scheduled | Weather, NAS, security cancels excluded from the numerator |
| B0 | Flight leg | hubops | Actual gate-to-gate time at or under scheduled block time | Diverted legs excluded |
| TXO | Flight leg | hubops | Mean taxi-out minutes | Cap outliers above 180 min; log capped rows, don't drop silently |
| Hub throughput | Station, day | hubops | D0 at the hub minus A0 into the hub, same day | Positive means the hub puts flights back on time net of what it received |
| Controllable delay share | Flight leg (15+ min late arrivals only) | hubops | Carrier delay minutes over all delay minutes | BTS only populates cause-minute buckets for arrivals delayed 15+ minutes; flights below that threshold don't carry cause data and are excluded from this ratio, not treated as zero |
| Minutes recovered per turn | Aircraft turn | hubops | Inbound arrival delay minus outbound departure delay | Paired turns only (see turn pairing rule below) |

## Turn pairing rule

Same tail number, same station, arrival strictly before departure, gap
under 6 hours, matched to the *next* departure by actual out time. Legs
that fail pairing (no match, ambiguous match, gap >= 6h) go to a quarantine
table with a reason code rather than being silently dropped or guessed at.

## Reference DAX

```dax
D0 % =
VAR Operated =
    CALCULATE ( COUNTROWS ( fact_flight_leg ), fact_flight_leg[is_cancelled] = 0 )
VAR OnTime =
    CALCULATE (
        COUNTROWS ( fact_flight_leg ),
        fact_flight_leg[is_cancelled] = 0,
        fact_flight_leg[dep_delay_min] <= 0
    )
RETURN DIVIDE ( OnTime, Operated )

Hub Throughput pts =
VAR D0 = [D0 %]
VAR A0 =
    CALCULATE ( [A0 %], USERELATIONSHIP ( fact_flight_leg[dest_airport_key], dim_airport[airport_key] ) )
RETURN ( D0 - A0 ) * 100

D0 vs Same Period LY pts =
VAR LY = CALCULATE ( [D0 %], SAMEPERIODLASTYEAR ( dim_date[date] ) )
RETURN IF ( NOT ISBLANK ( LY ), ( [D0 %] - LY ) * 100 )
```

## Reconciliation target

DOT Consumer Report reconciliation was dropped along with DOT itself --
see [ADR 0012](decisions/0012-dot-t100-scope-out.md). Instead: monthly
D0/A14/CF computed from `wh_gold` must match the same metrics computed
directly from `lh_bronze.bts_marketing_ontime` (the untransformed
source) to the decimal, for every month checked, and flight counts must
match BTS totals. Tracked on report page 8 ("Data health"). Confirmed on
the full build (`nb_gold_build`, 2026-10-31): max D0 diff 0.0000924, max
CF diff 0.0000087 across all 44 months -- see ADR 0013. If a month
misses target going forward, the cause goes in `docs/decisions/` as a
short ADR, not a silent tolerance widening.
