# Ad-hoc queries

Real T-SQL against `wh_gold`, written for SSMS (connect to the
Warehouse's SQL endpoint -- see `docs/fabric_workspace.md` for the
connection string). This is the "main contact for data requests"
folder from the job posting: the kind of query a DFW ops analyst would
actually ask for, kept here instead of answered once and forgotten.

Every query is plain, runnable T-SQL against the real gold schema
documented in `docs/data_dictionary.md` -- no notebook, no pyodbc, no
`az rest` needed to run these. Edit the `DECLARE` block at the top of
each file to change the station/date range/fleet, then run.

| File | Answers |
| --- | --- |
| `monthly_kpi_by_station.sql` | "What's D0/A14/CF for \<station\> over the last N months?" |
| `worst_station_days_weather_adjusted.sql` | "Which station-days most underperformed their weather/volume-adjusted expected D0?" |
| `fleet_turn_cliff_lookup.sql` | "At what scheduled turn length does \<fleet\>'s D0 fall off at \<station\>?" |
| `delay_cause_breakdown.sql` | "Break down cause-minutes by month/station for \<carrier family\>." |
| `rebank_effect_summary.sql` | "What are the rebank's effect sizes, with CIs?" |
| `data_health_snapshot.sql` | "Is the data fresh and does it reconcile?" -- same query `nb_meta_health` runs; see `docs/decisions/0020-phase-8-hardening-scope.md` |
