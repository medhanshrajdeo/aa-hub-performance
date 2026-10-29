# ADR 0003: Scope cuts for the rebank diff-in-differences prototype

**Status:** done, 2026-10-29.

## Context

The plan's signature analysis #2 calls for: treated = AA at DFW, controls =
AA at hubs with no April structural change *plus* DFW year-over-year;
covariates including both origin *and* destination weather severity.
Building the full version requires weather for every airport AA/DFW
touches and at least two control arms. For an interview-ready local
prototype, that's more data acquisition than the result needs to be real
and defensible.

## Decision

Scoped the prototype (`analysis/dfw_rebank_diffindiff.py`) down to:

- **One control hub (ORD)**, not ORD + CLT + DFW-YoY. ORD is also
  America/Chicago, which additionally let the weather-join timezone logic
  stay simple (see ADR consequence below).
- **Origin weather severity only**, not origin + destination. D0 is
  fundamentally an origin-side metric (gate departure); destination
  weather matters more for arrival-side metrics. Dropping it avoids
  needing weather data for dozens of destination airports for a
  covariate that would matter less for D0/TXO than for A14 specifically.
- **The rebank week is detected from the data**, not assumed from the
  Cranky Flier article's "April 2026." See the correlation-jump method in
  `find_rebank_week()`. This turned out to matter: a naive
  "first week that leans post-rebank" rule lands on week 8 (mid-February),
  a real but smaller and non-sustained schedule tweak, not the rebank. The
  "sustained jump" rule correctly finds week 15 (April 6-12).

## Consequences

- The result (D0 -6.4pp, A14 -8.1pp, both p<0.01; TXO null) is real and
  worth reporting, but should be framed as "a real, sizeable,
  weather-adjusted decline that likely reflects the rebank" rather than an
  airtight causal claim -- single-control-hub DiD is more exposed to
  hub-specific confounds (an ORD-specific good summer, unrelated DFW
  issues) than a multi-control design would be. Documented in the README
  findings section rather than overstated.
- Because DFW and ORD share a time zone, `load_weather_severity()`'s
  UTC-to-local bucketing was written against a single hardcoded `HUB_TZ`.
  Adding CLT (Eastern) as a third control later means parameterizing that
  per station -- straightforward (the logic is already date/hour-bucket
  based, not DFW-specific), just not done yet.
- Destination weather, DFW year-over-year, and a second control hub are
  the natural next increments if this moves from prototype to the real
  Phase 6 Fabric notebook.
