# Data dictionary: `wh_gold`

Every table, column, type, and source in the Gold star schema, generated
from the real DDL in `scripts/fabric/build_gold_notebook.py`,
`build_gold_turns_notebook.py`, and `build_gold_station_hour_notebook.py`
(the schema those scripts create *is* this dictionary's source of truth
-- if they drift, this file is wrong, not the other way around). See
`docs/kpi_spec.md` for KPI formulas and `planning/project-plan.md` for
the full gold data model's intent.

Row counts are from the full build (2026-10-31); see
`docs/fabric_workspace.md` for the run-by-run history and
`docs/decisions/0013-gold-warehouse-via-pyodbc-crossdb.md` for how it
was built.

## Dimensions

### `dim_date` -- 1,461 rows (2023-01-01 to 2026-12-31)

| Column | Type | Notes |
| --- | --- | --- |
| `date_key` | INT | `yyyyMMdd`, e.g. `20260406` |
| `full_date` | DATE | |
| `year`, `month`, `day` | INT | |
| `day_of_week` | INT | 1=Monday .. 7=Sunday (locale-independent, computed from `day_name`, not `DATEPART(WEEKDAY,...)` which depends on session `DATEFIRST`) |
| `day_name`, `month_name` | VARCHAR | English names |
| `quarter` | INT | 1-4 |
| `is_weekend` | BIT | Saturday or Sunday |
| `is_holiday` | BIT | Real US federal holiday observed dates, computed by `scripts/fabric/compute_us_holidays.py`'s rules (Nth-weekday-of-month, or fixed-date-with-nearest-weekday-observance), not a hand-typed list |
| `schedule_season` | VARCHAR(10) | `summer` (Apr-Oct) or `winter` (Nov-Mar) -- an approximation of IATA scheduling seasons, not official season boundaries |
| `is_post_dfw_rebank` | BIT | `full_date >= 2026-04-06` -- week 15, the *sustained-jump* week detected from the schedule data itself, per [ADR 0003](decisions/0003-rebank-diffindiff-scope.md), not the "April 2026" the Cranky Flier article names loosely |

### `dim_time_15` -- 96 rows

| Column | Type | Notes |
| --- | --- | --- |
| `time_key` | INT | 0-95 |
| `hour24` | INT | 0-23 |
| `minute15` | INT | 0, 15, 30, 45 |
| `hhmm` | VARCHAR(5) | e.g. `"08:15"` |
| `daypart` | VARCHAR(20) | Overnight / Early Morning / Morning / Midday / Afternoon / Evening / Night |

### `dim_airport` -- 2,060 rows

| Column | Type | Notes |
| --- | --- | --- |
| `airport_key` | VARCHAR(3) | IATA code, PK |
| `airport_name`, `municipality`, `iso_region` | VARCHAR | |
| `latitude`, `longitude` | FLOAT | |
| `tz` | VARCHAR(50) | IANA tz name, resolved via `hubops.airports.resolve_airport_tz` |
| `is_aa_hub` | BIT | True for DFW, CLT, MIA, ORD, PHX, PHL |

Source: `lh_bronze.dim_airport` (OurAirports + resolved timezones --
[contracts/dim_airport.yaml](../contracts/dim_airport.yaml)). Covers
every Origin/Dest code BTS reports, not just AA's hubs.

### `dim_carrier` -- 21 rows

| Column | Type | Notes |
| --- | --- | --- |
| `carrier_key` | VARCHAR(2) | IATA carrier code, PK |
| `carrier_name` | VARCHAR(100) | Real airline name |
| `is_mainline` | BIT | True for the 10 major carriers in this data (AA, DL, UA, WN, AS, B6, NK, F9, G4, HA) |
| `is_aa_family` | BIT | True **only** for AA itself and its wholly-owned regional subsidiaries flying exclusively as American Eagle: Envoy (MQ), PSA (OH), Piedmont (PT). **Not** true for independent multi-carrier regionals that sometimes fly under AA's brand (SkyWest/OO, Mesa/YV, Republic/YX, Air Wisconsin/ZW, GoJet/G7, CommutAir/C5, Endeavor/9E, Horizon/QX) -- those carriers also fly for Delta, United, or Alaska, so "AA family" isn't a fixed property of the carrier code. To find AA-branded flights operated by one of those, filter `fact_flight_leg.marketing_carrier_key = 'AA'` directly, regardless of `operating_carrier_key`. |

The 21 codes are the exact real set observed in
`lh_silver.fact_flight_leg`'s `Marketing_Airline_Network` /
`Operating_Airline` columns (checked via a throwaway diagnostic before
writing this table, not guessed).

### `dim_aircraft` -- 1,635 rows

| Column | Type | Notes |
| --- | --- | --- |
| `tail_number` | VARCHAR(10) | PK |
| `manufacturer`, `model` | VARCHAR(50) | From the FAA Releasable Aircraft Database, joined to ACFTREF -- [contracts/faa_releasable_aircraft.yaml](../contracts/faa_releasable_aircraft.yaml) |
| `fleet_family` | VARCHAR(50) | Currently aliased to `model` directly (no separate fleet-family grouping taxonomy built yet -- a real simplification, not a bug) |
| `seats`, `num_engines`, `year_mfr` | INT | |

**Only covers AA's own fleet** (1,635 tails) -- `src/hubops/faa_registry.py`
filtered the FAA registry to AA's real tail numbers before this ever
left a local machine (PII-free by construction, see
[ADR 0001](decisions/0001-bot-protected-sources-need-manual-export.md)).
Joining `fact_flight_leg` or `fact_aircraft_turn` to this table only
matches AA-operated legs; everything else's `fleet_family` is NULL by
design.

### `dim_delay_cause` -- 5 rows

| `cause_code` | `label` | `is_controllable` |
| --- | --- | --- |
| CarrierDelay | Air Carrier Delay | 1 |
| WeatherDelay | Weather Delay | 0 |
| NASDelay | National Aviation System Delay | 0 |
| SecurityDelay | Security Delay | 0 |
| LateAircraftDelay | Late Aircraft Delay | 0 |

Real BTS-defined delay-cause buckets (not fabricated). Only
`CarrierDelay` is "controllable" per the Controllable delay share KPI
(`docs/kpi_spec.md`).

### `dim_cancel_reason` -- 4 rows

| `reason_code` | `label` | `is_controllable` |
| --- | --- | --- |
| A | Carrier | 1 |
| B | Weather | 0 |
| C | National Air System | 0 |
| D | Security | 0 |

Real BTS `CancellationCode` values.

### `dim_bank` -- 35 rows

| Column | Type | Notes |
| --- | --- | --- |
| `bank_key` | INT | Surrogate, row order from the source CSV |
| `station` | VARCHAR(3) | |
| `period` | VARCHAR(30) | e.g. `"2026-03 (pre-rebank)"` |
| `direction` | VARCHAR(15) | `departure` or `arrival` |
| `bank_number` | INT | |
| `peak_time` | VARCHAR(5) | `hh:mm` of the bank's peak |
| `peak_avg_flights` | FLOAT | |
| `window_start`, `window_end` | VARCHAR(5) | |

Loaded from `docs/analysis/dim_bank_prototype.csv`, the real output of
`analysis/dfw_bank_reconstruction.py` (DFW only, March vs August 2026 --
not a full continuous bank calendar for every station/period).

## Facts

### `fact_flight_leg` -- 27,787,225 rows

One row per scheduled BTS flight leg, Jan 2023 - Aug 2026, all 14
reporting carriers (not AA-only).

| Column | Type | Notes |
| --- | --- | --- |
| `date_key` | INT | From `FlightDate` (scheduled departure, origin-local date) |
| `origin_airport_key`, `dest_airport_key` | VARCHAR(3) | FK to `dim_airport` |
| `marketing_carrier_key`, `operating_carrier_key` | VARCHAR(2) | FK to `dim_carrier` |
| `flight_number` | VARCHAR(10) | |
| `tail_number` | VARCHAR(10) | NULL for some cancelled legs (expected); Allegiant's missing-leading-"N" quirk already normalized in silver -- [ADR 0011](decisions/0011-g4-tail-number-quirk.md) |
| `sched_dep_utc`, `sched_arr_utc`, `actual_dep_utc`, `actual_arr_utc` | DATETIME2(6) | Real UTC instants, resolved from BTS's local-clock hhmm fields -- [ADR 0010](decisions/0010-silver-time-resolution-bugs.md). `actual_*_utc` are `sched_*_utc + reported_delay`, not independently re-resolved (BTS's own delay columns are the authoritative day-aware offset) |
| `dep_delay_min`, `arr_delay_min` | FLOAT | BTS `DepDelay`/`ArrDelay` (can be negative for early departures) |
| `taxi_out_min` | FLOAT | Capped at 180 min per `docs/kpi_spec.md` |
| `taxi_in_min` | FLOAT | Not capped |
| `crs_elapsed_min`, `actual_elapsed_min` | FLOAT | Scheduled/actual block time |
| `carrier_delay_min`, `weather_delay_min`, `nas_delay_min`, `security_delay_min`, `late_aircraft_delay_min` | FLOAT | NULL (not zero) unless `ArrDel15=1` -- BTS only populates these for 15+ min late arrivals |
| `is_cancelled`, `is_diverted` | BIT | |
| `is_d0`, `is_a14`, `is_b0` | BIT | NULL when not applicable (e.g. `is_d0`/`is_a14` NULL for cancelled legs) |
| `is_controllable_cancel` | BIT | Cancellation code A |
| `cancellation_code` | VARCHAR(2) | NULL unless cancelled |
| `distance_mi` | FLOAT | |

Reconciled against `lh_bronze.bts_marketing_ontime` raw aggregates:
max D0 diff 0.0000924, max CF diff 0.0000087 across all 44 months.

### `fact_aircraft_turn` -- 21,024,172 rows (893,482 at DFW)

One row per paired turn: a tail's inbound arrival at a station, paired
with its next departure from that same station. Same pairing rule as
`hubops.turns.pair_turns()` -- same station, non-negative gap, gap under
6 hours. Legs that fail pairing aren't in this table (no separate
quarantine table in gold; see `src/hubops/turns.py` for the full rule
and its own `TurnQuarantine` reason codes if that logic runs locally).

| Column | Type | Notes |
| --- | --- | --- |
| `turn_key` | BIGINT | Surrogate |
| `tail_number` | VARCHAR(10) | |
| `station` | VARCHAR(3) | |
| `inbound_arr_sched_utc`, `inbound_arr_actual_utc` | DATETIME2(6) | |
| `outbound_dep_sched_utc`, `outbound_dep_actual_utc` | DATETIME2(6) | |
| `scheduled_turn_min` | FLOAT | NULL if either scheduled time is missing |
| `actual_turn_min` | FLOAT | `outbound_dep_actual_utc - inbound_arr_actual_utc`, always 0-360 min by construction |
| `inbound_arr_delay_min`, `outbound_dep_delay_min` | FLOAT | |
| `minutes_recovered` | FLOAT | `inbound_arr_delay_min - outbound_dep_delay_min`; positive means the turn recovered time |
| `fleet_family` | VARCHAR(50) | NULL unless `tail_number` matches `dim_aircraft` (AA fleet only) |
| `turn_below_fleet_p10` | BIT | True if `actual_turn_min` is below the 10th percentile for that `fleet_family`; NULL where `fleet_family` is NULL |

### `fact_station_hour` -- 4,071,389 rows

Grain: station + **UTC** date + **UTC** hour of scheduled departure
(derived from `sched_dep_utc`'s own components, not `fact_flight_leg`'s
`date_key` -- see `scripts/fabric/build_gold_station_hour_notebook.py`'s
comments for why mixing a locally-anchored date with a UTC hour would be
wrong, same class of bug as [ADR 0010](decisions/0010-silver-time-resolution-bugs.md)).

| Column | Type | Notes |
| --- | --- | --- |
| `station` | VARCHAR(3) | Origin airport |
| `date_key` | INT | UTC date of `sched_dep_utc` |
| `hour24` | INT | UTC hour of `sched_dep_utc` |
| `scheduled_departures` | INT | |
| `actual_departures` | INT | Non-cancelled |
| `mean_dep_delay_min` | FLOAT | Non-cancelled legs only |
| `weather_severity_score` | INT | 0-4, from `weather_station_hour_severity`; **NULL for ~96.3% of rows** -- weather was only sourced for AA's 6 hub stations, not all ~370 airports this table covers |

**TSA throughput is not joined into this table** -- deliberate scope
cut, documented in `nb_gold_station_hour`'s own source: TSA's `hour` is
station-local, this table is UTC, and converting correctly needs the
same DST-aware logic `nb_silver_flights` took two real bug-fix rounds to
get right. Use `fact_checkpoint_hour` standalone if TSA throughput is
needed without this join.

### `fact_checkpoint_hour` -- 1,607,461 rows

| Column | Type | Notes |
| --- | --- | --- |
| `station` | VARCHAR(3) | |
| `checkpoint` | VARCHAR(50) | Bracket-quoted as `[checkpoint]` in T-SQL -- it's a reserved keyword, see [ADR 0013](decisions/0013-gold-warehouse-via-pyodbc-crossdb.md) |
| `date_key` | INT | |
| `hour24` | INT | Station-LOCAL hour (TSA's own convention -- not converted to UTC) |
| `week_start` | DATE | |
| `passengers_screened` | INT | |

### `fact_cbp_wait_hour` -- 20,740 rows

| Column | Type | Notes |
| --- | --- | --- |
| `station` | VARCHAR(10) | `AirportCode` |
| `terminal` | VARCHAR(50) | |
| `date_key` | INT | |
| `hour_range` | VARCHAR(20) | CBP's own hour-bucket label, not a single integer hour |
| `avg_wait_us`, `max_wait_us` | FLOAT | US passengers |
| `avg_wait_non_us`, `max_wait_non_us` | FLOAT | Non-US passengers |
| `booths_open` | INT | |
| `flight_count`, `total_passengers` | INT | |

DFW Terminal D only -- CBP's site has no automatable export, this is
the real manually-exported data (not a sourcing limitation this project
can fix locally).

### `fact_rebank_effect` -- 3 rows

| Column | Type | Notes |
| --- | --- | --- |
| `hub`, `metric`, `model` | VARCHAR | |
| `estimate`, `se`, `ci_low`, `ci_high`, `p_value` | FLOAT | |
| `n` | BIGINT | |
| `controls` | VARCHAR(500) | |

Loaded from `docs/analysis/fact_rebank_effect_prototype.csv`, the real
output of `analysis/dfw_rebank_diffindiff.py` -- see
[ADR 0003](decisions/0003-rebank-diffindiff-scope.md) for scope and the
README's Findings section for the plain-English readout (D0 -6.4pp,
A14 -8.1pp at DFW vs ORD post-rebank, both p<0.01).

### `fact_rebank_event_study` -- 16 rows

| Column | Type | Notes |
| --- | --- | --- |
| `week_relative` | INT | Weeks relative to the detected rebank week (2026-W15) |
| `coef`, `ci_low`, `ci_high` | FLOAT | |

Loaded from `docs/analysis/event_study_d0.csv` -- the event-study check
that the rebank week wasn't a confound with a pre-existing trend.

### `fact_station_day_scorecard` -- 8,028 rows

Real output of signature analysis #4 (weather/volume-adjusted expected-D0
model), loaded 2026-11-01 -- closes the plan's "analyses 2 and 4 write to
gold" gap for analysis #4 (`fact_rebank_effect` already covered analysis
#2). See [ADR 0014](decisions/0014-direct-lake-semantic-model-quirks.md)
for the loading notebook's `fast_executemany` fix.

| Column | Type | Notes |
| --- | --- | --- |
| `station` | VARCHAR(3) | 6 AA hub stations only |
| `date_key` | INT | `yyyyMMdd` |
| `scheduled_departures`, `operated_departures`, `d0_count` | INT | |
| `max_severity_score` | INT | NULL-able; day's single worst hourly weather severity, 0-4 |
| `d0_rate`, `expected_d0_rate` | FLOAT | Actual vs the weighted-OLS-predicted D0 rate (weather severity + log volume + station/month fixed effects) |
| `d0_vs_expected_pp` | FLOAT | Actual minus expected, in points -- the weather-adjusted D0 gap. Negative means underperforming given that day's weather/volume |

Loaded from `docs/analysis/station_day_scorecard.csv`, the real output
of `analysis/dfw_weather_scorecard.py`.

## Semantic model

`sm_hubops` (Direct Lake over `wh_gold`) is documented separately in
[`docs/fabric_workspace.md`](fabric_workspace.md#semantic-model) --
its 23 measures match this file's KPI math exactly (`docs/kpi_spec.md`
is the formula source of truth for both the Warehouse and the semantic
model).
