# DFW Hub Performance Analytics on Fabric: Project Plan

Oct 29, 2026 · @Medhansh Rajdeo

## The pitch

**Build:** DFW Hub Performance Analytics, an end-to-end Fabric pipeline on real federal data that answers one question: **did the April 2026 DFW rebank improve departure reliability, and where are frontline leaders still losing minutes they can control?**

American moved DFW to a new bank structure in April 2026. Public tracking showed DFW D0 trending down after the change, while American pointed to record June weather ([Cranky Flier, Jun 29 2026](https://crankyflier.com/2026/06/29/pinpointing-americans-biggest-operational-issues/)). That is a testable claim: separate weather from structure, then show which banks, fleets and aircraft turns lose time at the station.

Every row is real: BTS flight-level on-time records, hourly airport weather, TSA hourly checkpoint throughput, CBP international arrival wait times, and the FAA aircraft registry. Nothing synthetic.

**Scope:** all US reporting carriers loaded (about 25M flight rows, Jan 2023 to the latest BTS month) so AA can be benchmarked against Delta and United. Gold focuses on flights marketed as American (mainline, Envoy, PSA and other Eagle operators) at AA hubs, with DFW as the deep dive.

Brand it as an independent analysis on public data. No AA logos.

## How it maps to the job

Each line of the posting gets a piece of the build you can point to.

| Posting says | What you show |
| --- | --- |
| Frontline leaders get data to drive reliability, accountability, profitability | Station scorecard with D0, A14, CF and controllable delay, filtered by hub via row-level security |
| Complex quantitative analysis to support policy decisions | Weather-adjusted difference-in-differences test of the DFW rebank |
| Data wrangling in the data lake to report on operational performance | Medallion lakehouse: zipped CSVs, weekly PDFs and METAR text cleaned into Delta tables |
| Owner of airport tech data subject areas (ConnectMe, Automated Volunteer Processing, Biometric Screening) | Three subject areas built from public proxies: D0 discipline (ConnectMe pushes gate crews toward D0), denied boardings (volunteer processing), CBP arrival waits (biometrics) |
| Main contact for data requests and metadata | Published data dictionary, lineage view and KPI definition sheet |
| Liaison between Data Engineering and Analytics; new data sources | Source onboarding pattern: control table, watermark, schema contract per source |
| Define KPIs for new policies and products | KPI spec page with formula, grain, owner and edge cases for each metric |
| SQL, relational DBs, SSMS, Databricks | Gold layer in a Fabric Warehouse queried from SSMS; PySpark notebooks that port to Databricks unchanged |
| Python, Power BI | PySpark and statsmodels notebooks; Direct Lake semantic model and report |

The ConnectMe link is public: American uses it to push flight attendants toward exact on-time departures with a countdown at the gate ([View from the Wing](https://viewfromthewing.com/on-time-at-what-cost-american-airlines-tech-pressure-on-employees-and-customers/)).

## Data sources

**Five** real sources (DOT Air Travel Consumer Report and BTS T-100 were
scoped out -- see [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md):
both need manual PDF/export handling for customer-complaint and
load-factor context that's tangential to the rebank question this project
actually answers; five sources answering one real question beats seven
half-used), picked so that three of them are messy on purpose. Clean CSVs
impress nobody; parsing weekly TSA PDFs and raw METAR strings is the
wrangling this team does daily.

| Source | Grain | What it adds | How it lands | Wrangling pain |
| --- | --- | --- | --- | --- |
| [BTS Marketing Carrier On-Time Performance](https://www.transtats.bts.gov/) | Flight leg, monthly files | Tail number, scheduled vs actual gate and wheels times, taxi, cancel and divert codes, five delay cause buckets, marketing vs operating carrier | Monthly zip by HTTP in a parameterized pipeline | Local hhmm times, 2400 as midnight, overnight arrivals with no date, no time zone |
| [Iowa Environmental Mesonet ASOS](https://mesonet.agron.iastate.edu/request/download.phtml) | Station, hourly | Visibility, ceiling, gusts, thunderstorm codes per hub | HTTP CSV per station and month | Raw METAR codes, specials between hourly obs, missing hours |
| [TSA FOIA throughput](https://www.tsa.gov/foia/readingroom) | Airport, checkpoint, hour | Passengers screened per checkpoint at DFW and other hubs | Weekly PDFs, scraped list then download | PDF tables with carried-forward date and hour cells, checkpoint names that drift |
| [CBP Airport Wait Times](https://awt.cbp.gov/) | Airport, terminal, hour | Average and max wait for US and non-US travelers, booths open | Manual Excel export landed in Files (documented as a manual source) | Wide layout, terminal codes |
| [FAA Releasable Aircraft Database](https://registry.faa.gov/database/ReleasableAircraft.zip) | Tail number | Aircraft model, seats, engine type for fleet dimension | Manual zip download landed in Files -- see [ADR 0001](../docs/decisions/0001-bot-protected-sources-need-manual-export.md), site returns 403 to automated requests | Drop owner name and address columns on ingest |

~~DOT Air Travel Consumer Report~~ and ~~BTS T-100 Domestic Segment~~:
scoped out, see [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md).

All 14 reporting carriers for 2026, including American, Envoy and PSA, are in the BTS file ([BTS Directive 40](https://www.bts.gov/explore-topics-and-geography/modes/aviation/number-40-technical-directive-reporting-time)). Airport time zones come from airport coordinates via the Python `timezonefinder` package.

~~Open question for Phase 1: confirm the exact BTS zip URL pattern for the marketing carrier table and the latest month published.~~ **Resolved 2026-10-29:** `https://transtats.bts.gov/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{yyyy}_{m}.zip` (no leading zero on month). Latest published month is 2026-08 (2026-09 not yet out). Verified by downloading and validating the real file -- see `contracts/bts_marketing_ontime.yaml` and the `resolve_leg_times` validation run (13,332 real AA/DFW legs, 0 mismatches, correctly resolved 5 real HNL-DFW redeyes crossing the date line).

## Fabric architecture

Two lakehouses, one warehouse, one Direct Lake model, all in one Git-synced workspace per environment.

&#91;embedded content: Fabric architecture: sources, medallion layers, consumers, ops\]

Gold lives in a Warehouse because the posting asks for SQL and SSMS, and stored procedures keep gold logic reviewable. Silver uses materialized lake views for declarative transforms with built-in quality rules. Direct Lake means no import refresh and one copy of the data.

## Gold data model

A star schema in the Fabric Warehouse, with `fact_aircraft_turn` as the table nobody else builds from public data.

| Table | Grain | Key columns and measures |
| --- | --- | --- |
| `fact_flight_leg` | One scheduled leg | Date, origin, dest, marketing and operating carrier, tail, bank; dep and arr delay, taxi out and in, scheduled vs actual block, five delay cause minutes; D0, A14, B0, cancel and divert flags. All times stored local and UTC |
| `fact_aircraft_turn` | One tail's inbound arrival paired with its next departure from the same station | Scheduled turn, actual turn, inbound arr delay, outbound dep delay, minutes recovered (inbound delay minus outbound delay), turn below fleet p10 flag |
| `fact_station_hour` | Station, hour | Scheduled and actual departures, mean delay, weather severity score, TSA throughput. The join point for weather and passenger flow |
| `fact_checkpoint_hour` | Airport, checkpoint, hour | Passengers screened |
| `fact_cbp_wait_hour` | Airport, terminal, hour | Avg and max wait, US and non-US, booths open |
| `fact_rebank_effect` | Hub, metric, model | Estimate, 95% CI, p-value, controls used. Written by the analysis notebook |
| `dim_date` | Operating day | Holidays, schedule season, pre or post rebank flag |
| `dim_time_15` | 15-minute slot | Hour, daypart |
| `dim_airport` | IATA code | Hub flag, time zone, lat and long |
| `dim_carrier` | Carrier code | Mainline vs regional, AA family flag |
| `dim_aircraft` | Tail number | FAA model, seats, fleet family (A321, 737-800, E175, CRJ900) |
| `dim_bank` | Station, bank, effective period | Bank number, window start and end. Derived from the schedule itself (see analyses) |
| `dim_delay_cause`, `dim_cancel_reason` | Code | Label, controllable flag |

Turn pairing rule: same tail, same station, arrival before departure, gap under 6 hours, next departure by actual out time. Legs that fail pairing go to a quarantine table with a reason code.

## KPI definitions

Use the airline's own vocabulary. D0, A14 and CF are the three numbers AA's operations leadership ties employee payouts to ([View from the Wing](https://viewfromthewing.com/american-will-pay-employees-for-meeting-operational-goals-in-january/)), so get these exactly right.

| KPI | Definition | Edge case to handle |
| --- | --- | --- |
| D0 | Operated departures leaving the gate at or before schedule, over operated departures | Cancelled legs excluded from both sides |
| A14 | Operated arrivals within 14 minutes of schedule | BTS basis only -- see [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md) for why there's no second DOT-basis version |
| CF | 1 minus cancelled over scheduled | Diverted counts as completed |
| Controllable CF | 1 minus carrier-caused (code A) cancels over scheduled | Weather, NAS, security cancels excluded |
| B0 | Actual gate-to-gate time at or under scheduled block | Diverted excluded |
| TXO | Mean taxi-out minutes | Cap outliers above 180 min, log them |
| Hub throughput | D0 at the hub minus A0 into the hub, same day | Positive means the hub puts flights back on time |
| Controllable delay share | Carrier delay minutes over all delay minutes | Only flights with 15+ min arrival delay carry cause minutes in BTS |
| Minutes recovered per turn | Inbound arrival delay minus outbound departure delay | Paired turns only |

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

The hub throughput idea comes straight from the Cranky Flier thread, where the author's June 2026 figures put DFW at about minus 12 points and CLT at about minus 13. Those came from Anuvu data, not BTS, so landing within a couple of points is a fair sanity check, not an exact match.

## Signature analyses

Six analyses, ranked by interview impact. The first two are the headline; build them first.

1. **Rebuild the DFW bank structure from the schedule.** Count scheduled AA departures and arrivals per 15-minute slot, typical weekday, before and after April 2026. Detect peaks with `scipy.signal.find_peaks` and write the windows to `dim_bank`. You walk in showing their own schedule change, derived from public data.
2. **Test whether the rebank worked, net of weather.** Difference-in-differences on flight-level D0, A14 and TXO. Treated: AA departures at DFW. Controls: AA at hubs with no April structural change, plus DFW year over year. Covariates: hub, month, hour, origin and destination weather severity, scheduled departures in that hour. Standard errors clustered by day. Add an event-study plot by week to check pre-trends. A null or negative result is fine; the method is the story.
3. **Split station-caused from inbound-caused delay.** On paired turns, an outbound delay after an on-time inbound is station-originated. Cut by bank, fleet and scheduled turn length, and find the scheduled turn length where D0 falls off a cliff for each fleet. This is the accountability view: what a DFW leader can actually fix.
4. **Weather-adjusted station scorecard.** Score each hour's weather from METAR (thunderstorm, ceiling under 1,000 ft, visibility under 3 mi, gusts over 30 kt). Model expected D0 given weather and volume, then report D0 above or below expected per station per day. Leaders are not graded on thunderstorms.
5. **Does chasing D0 buy A14?** Compare A14 for flights pushed at 0 vs 1 to 5 minutes late, controlling for route and weather. Pair it with the hub throughput metric. Handle this one carefully in the interview: it questions a metric they pay on, so frame it as finding where D0 pays off most.
6. **Passenger flow.** TSA checkpoint peaks at DFW vs departure bank peaks (lag in minutes, before and after rebank). CBP max wait at DFW by hour vs international arrival waves (biometrics subject area). ~~AA denied boardings per 10k vs Delta and United by quarter (volunteer processing subject area), plus bag and wheelchair rates~~ -- dropped, those metrics live in the DOT Air Travel Consumer Report, scoped out by [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md).

Analyses 2 and 4 write results to gold tables so the report reads modeled numbers, not notebook screenshots.

## Power BI report

Eight pages on one Direct Lake semantic model. Page 1 is what a DFW station leader opens at 7 a.m.; page 8 is what you open when someone asks whether to trust page 1.

| Page | For | Shows |
| --- | --- | --- |
| 1. Hub scorecard | Station leaders | D0, A14, CF, controllable CF, TXO, hub throughput vs last year; weather-adjusted D0 gap; RLS limits each leader to their hub |
| 2. Bank performance | DFW hub ops | Departure and arrival waves before vs after rebank; D0 and station-originated delay by bank |
| 3. Turn performance | Ramp and gate leads | D0 by scheduled turn length per fleet, minutes recovered distribution, worst routes into DFW |
| 4. Delay root cause | Ops analysts | Cause minutes by hour and bank, late-aircraft share, controllable share trend |
| 5. Rebank impact | Leadership | Event-study chart and effect sizes with 95% CI from `fact_rebank_effect` |
| 6. Passenger flow | Customer ops | TSA throughput by checkpoint and hour vs departure waves; CBP waits at DFW by terminal |
| 7. Customer outcomes and peers | Customer care | Bags, wheelchairs, denied boardings, complaints; A14 and CF vs Delta and United |
| 8. Data health | Analysts | Freshness per source, row counts, DQ pass rates, reconciliation to BTS raw aggregates |

Design rules: one question per page title, goal lines on every KPI, drill-through from any hub to page 3, tooltips that show the KPI definition.

## Production hardening

This is what separates it from a portfolio dashboard. Each item is small; together they read as someone who has run a pipeline, not just built one.

- **Idempotent, watermarked loads.** `meta.source_registry` holds URL pattern, cadence and last loaded period per source; `meta.load_log` holds rows, file hash, duration and status per run. Re-running a month overwrites that partition with Delta `replaceWhere`, never duplicates.
- **Schema contracts.** A YAML contract per source lists expected columns and types. Drift fails the load and logs the diff.
- **Data quality in silver.** Materialized lake view constraints (GA since March 2026) drop or fail on: duplicate leg keys, arrival before departure on the same day, delay not equal to actual minus scheduled within 1 minute, cause minutes not summing to arrival delay, malformed tail numbers. Dropped rows go to quarantine with a reason.
- **Reconciliation to raw aggregates.** Monthly D0/A14/CF computed from gold vs the same metrics computed directly from bronze BTS, target exact match (not DOT -- see [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md)). Flight counts vs BTS totals. Shown on report page 8.
- **Orchestration and alerts.** One parent pipeline: ingest, silver, gold stored procedures, then Direct Lake reframe. BTS monthly, TSA weekly, weather daily. Failures send an Outlook alert from the pipeline.
- **CI/CD.** Workspace Git integration to GitHub, dev and prod workspaces on a deployment pipeline, a Variable Library for per-environment values. Pytest for parsing functions runs in GitHub Actions.
- **Security and governance.** RLS by hub, workspace roles, FAA owner fields dropped at bronze so no personal data lands.
- **Performance.** Delta partitioned by year-month, V-Order on, OPTIMIZE after loads, and a check that Direct Lake never falls back to DirectQuery.
- **Documentation.** Data dictionary generated from warehouse metadata plus YAML descriptions, KPI spec, a runbook (late file, BTS restatement, PDF layout change), and short decision records (why Warehouse for gold, why MLVs for silver).
- **SSMS.** Connect SSMS to the Warehouse SQL endpoint and keep a `sql/adhoc/` folder of the queries you would answer data requests with. The posting names SSMS; show it.
- **Optional:** a Fabric data agent over gold so a leader can ask "which DFW bank lost the most D0 last week" in plain English.

## Build phases for Claude Code

About 15 working days end to end. If the interview comes sooner, the **interview-ready cut** is phases 0 to 3, 5 and analyses 1 and 2 from phase 6: about 8 days. Run one phase per Claude Code session, give it this doc plus the phase, and have it write the "done when" checks before the code.

0. **Setup (half day).** Personal-tenant Fabric trial or F2 (not the TAMU tenant). Workspace ~~`aa-hubops-dev`~~ (the workspace that actually got created is named `AmericanAirlines` -- see [ADR 0014](../docs/decisions/0014-direct-lake-semantic-model-quirks.md)), lakehouses `lh_bronze` and `lh_silver`, warehouse `wh_gold`, GitHub repo connected. *Done when:* empty items show up in Git.
1. **Ingest BTS (1 to 2 days).** Pipeline with a ForEach over months, Copy activity pulls each zip to `Files/bts/yyyy/mm/`, notebook unzips to bronze Delta with all columns as strings plus `_source_file` and `_loaded_at`. *Done when:* every month Jan 2023 onward is loaded, `load_log` counts match the CSV line counts, and a re-run changes nothing.
2. **Silver flights (2 days).** Cast types, parse hhmm and 2400, roll overnight arrivals to the next date, convert to UTC, build turn pairs, apply MLV constraints. *Done when:* recomputed dep and arr delay match BTS within 1 minute on 99.9% of rows, quarantine under 0.5%, 20 hand-checked turns at DFW pair correctly.
3. **Gold and reconciliation (1 to 2 days).** Star schema in `wh_gold` via T-SQL stored procedures. *Done when:* gold-layer D0/A14/CF, aggregated by month, match the same metrics computed directly from `lh_bronze.bts_marketing_ontime` to the decimal, for every month checked (DOT reconciliation dropped -- see [ADR 0012](../docs/decisions/0012-dot-t100-scope-out.md)).
4. **Weather and reference dims (1 day).** IEM hourly METAR for all AA hubs, severity score, FAA registry to `dim_aircraft`, airport time zones. *Done when:* every AA tail in the last 12 months maps to a fleet family, and weather covers 98%+ of hub hours.
5. **Semantic model and pages 1 to 4 (2 days).** Direct Lake on `wh_gold`, measures from the KPI section, RLS by hub. *Done when:* D0, A14 and CF match a SQL query from SSMS to the decimal for three test slices.
6. **Analyses (2 to 3 days).** Bank reconstruction, rebank difference-in-differences with event study, turn split, weather-adjusted scorecard, D0 vs A14. *Done when:* `fact_rebank_effect` and `dim_bank` are written by notebook, and each result has a one-paragraph plain-English readout in the README.
7. **Messy sources (2 days, mostly done).** TSA PDF parser (pdfplumber), CBP export loader -- both built in Phase 1/4 rather than deferred here. *Done when:* parsed TSA totals for one week match the PDF's DFW total by hand.
8. **Pages 5 to 8 and hardening (2 days).** Prod workspace, deployment pipeline, Variable Library, alerts, data dictionary, runbook. *Done when:* a dev-to-prod deploy runs clean and page 8 shows green for all sources.
9. **Story (1 day).** README with three findings, architecture diagram, 3-minute screen-recorded walkthrough. *Done when:* someone outside airlines can follow the README.

If Claude Code has the Fabric skills installed, point it at the medallion, Spark, Warehouse and semantic model skills for phases 1 to 5.

## Repo structure

Fabric items sync from Git; everything else is plain code Claude Code can test locally.

```text
aa-hub-performance/
  README.md                 findings, architecture, how to run
  CLAUDE.md                 conventions for Claude Code sessions
  docs/
    kpi_spec.md             definitions, grain, edge cases
    data_dictionary.md      generated from warehouse + yaml
    runbook.md
    decisions/              short ADRs
  contracts/                one yaml schema per source
  src/hubops/               pure Python, unit tested
    bts_times.py            hhmm, 2400, overnight, UTC
    turns.py                tail pairing logic
    metar.py                weather severity score
    tsa_pdf.py              pdfplumber parser
    atcr_pdf.py
  tests/                    pytest with real sample files
  fabric/                   Git-synced workspace items
    pl_ingest_master.DataPipeline/
    nb_bronze_bts.Notebook/
    nb_silver_flights.Notebook/
    nb_analysis_rebank.Notebook/
    wh_gold.Warehouse/
    sm_hubops.SemanticModel/
    rpt_hubops.Report/
  sql/
    gold/                   DDL and stored procedures
    adhoc/                  SSMS queries for data requests
  .github/workflows/ci.yml
```

## Interview story and honest limits

**Talk track, three beats.**

1. Rivalytics at Coca-Cola: you have already built the first centralized view for a team that had none, and seen how leaders use it.
2. This project: "I wanted to know if the DFW rebank worked, so I rebuilt it from public data." Show the bank waves, the weather-adjusted effect, and the turn-length cliff.
3. The bridge: "With Mosaic access I'd swap three public proxies for the real feeds." ConnectMe door-close events instead of BTS gate-out, Automated Volunteer Processing counts per flight instead of quarterly denied boardings, biometric boarding throughput instead of CBP waits.

**Limits to name before they do.**

- BTS delay causes are coded by the carriers and only exist for arrivals 15+ minutes late.
- BTS has gate-out time, not door close or brake release, so its D0 may differ slightly from AA's internal D0.
- TSA and CBP data are airport-level, not airline-level. They describe the building, not AA's customers.
- No passenger, gate or crew data, so misconnects and crew legality are out of scope.
- The rebank test is observational. Weather and parallel ATC changes (ORD capacity limits, for example) can confound it; the event study is there to show you checked.

## Sources

- [Cranky Flier: Pinpointing American's Biggest Operational Issues (Jun 29, 2026)](https://crankyflier.com/2026/06/29/pinpointing-americans-biggest-operational-issues/)
- [BTS Technical Reporting Directive 40, reporting carriers for 2026](https://www.bts.gov/explore-topics-and-geography/modes/aviation/number-40-technical-directive-reporting-time)
- [TSA FOIA weekly hourly throughput by airport and checkpoint (data.gov)](https://catalog.data.gov/dataset/tsa-foia-reading-room-weekly-passenger-throughput-data)
- [View from the Wing: ConnectMe and D0 at American](https://viewfromthewing.com/on-time-at-what-cost-american-airlines-tech-pressure-on-employees-and-customers/)
- [View from the Wing: American pays employees on D0, A14, turns and CF](https://viewfromthewing.com/american-will-pay-employees-for-meeting-operational-goals-in-january/)
- [AirlineGeeks: D0, A14 and CF defined](https://airlinegeeks.com/2023/11/23/diving-into-airline-operational-kpis/)
- [Data Mozart: Materialized lake views GA at FabCon Atlanta, March 2026](https://data-mozart.com/?p=9415)
- [Fabric June 2026 feature summary](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Fabric-June-2026-Feature-Summary/ba-p/5190690)
