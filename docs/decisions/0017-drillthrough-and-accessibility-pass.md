# ADR 0017: Pages 5-6, drillthrough over repetition, and an accessibility pass

**Status:** done, 2026-11-01.

## Context

Two real datasets existed but weren't on the report yet: the rebank
event-study/effect-size tables (signature analysis #2) and the TSA/CBP
hourly passenger-flow profiles (signature analysis #6). The project
owner asked for two more pages built only around that real data, then
raised two further points after reviewing the result: the existing
pages repeated the same chart shape with only the measure swapped
(three "by Fleet Family" bar charts on Turn Performance, three "pre vs
post" bar charts on Bank Performance, two "by Station" bar charts on
Delay Root Cause) and asked for drill-downs or page tooltips instead of
flat repetition; and, after opening the `.pbip` in Power BI Desktop
themselves, that a field-parameter picker wasn't fully displaying and
that accessibility and UX mattered as much as the story.

## Pages 5-6

- **Rebank Impact**: effect-size cards (D0/A14/TXO vs ORD control, from
  `Rebank Effect`), an event-study line chart (`Rebank Event Study`'s
  `Coef`/`Ci Low`/`Ci High` by `Week Relative`, reference line at 0),
  and a pre-trend-check callout naming the week -8 caveat rather than
  smoothing it away.
- **Passenger Flow**: TSA checkpoint throughput vs. scheduled
  departures by local hour, CBP wait time vs. CBP's own passenger
  volume by hour, and a cross-check of BTS-proxy international arrivals
  against CBP's actual passenger count -- all `lineClusteredColumnComboChart`
  (dual-axis: `Y` columns on the primary axis, `Y2` lines on the
  secondary), needed because passenger counts and flight/wait counts
  are different orders of magnitude on the same hour axis.
- New gold tables loaded via `nb_gold_passenger_flow`
  (`fact_tsa_departure_hour_profile`, `fact_cbp_hour_profile`, 24 rows
  each). `RefreshWithXMLA` failed twice right after table creation with
  *"source tables either do not exist or access was denied"* even
  though `sys.tables` confirmed both existed with correct names and row
  counts -- a longer-latency Direct Lake OneLake-discovery delay,
  distinct from the same-session SQL-visibility lag in ADR 0014. A 90s
  wait resolved it; both tables queried clean afterward.

## Repetitive charts -> consolidation + a real drillthrough

Field-parameter-driven axis swapping (the obvious fix per the research
below) was already a confirmed dead end from ADR 0016 --
`InvalidUnconstrainedJoin` on a raw `Column` in a Measure-kind role, not
retried here. Instead:

- **Bank Performance**: the "Scheduled Departures Pre vs Post Rebank"
  and "Mean Taxi-Out Min Pre vs Post Rebank" single-measure bar charts
  (each just 2 bars) collapsed into one `pivotTable` (rows = Is Post
  Rebank, values = Flight Count + Mean Taxi-Out Min). The D0%/A14% pair
  stayed a chart since it's a real 2-series comparison, not a repeat.
- **Turn Performance**: "Minutes Recovered by Fleet," "Turn Volume by
  Fleet," and "Avg Scheduled Turn Length by Fleet" (three bar charts,
  same category, same TopN filter, different measure) collapsed into
  one `comboChart` (Turn Count columns + Minutes Recovered line) plus a
  **new drillthrough page, Fleet Turn Detail**, carrying the scheduled-
  turn-length metric. `type: "Drillthrough"`, `visibility:
  "HiddenInViewMode"`, a page-level filter on `Aircraft Turn[Fleet
  Family]` with `howCreated: "Drillthrough"`, and a matching
  `pageBinding.parameters[].boundFilter` -- confirmed via the schema at
  `.../report/2.1.0/schema.json` (`curl`'d directly; `PageBinding` and
  `visibility` aren't documented in the authoring skill's own
  `authoring.md`, which only shows the filter/pageBinding half). The
  drillthrough title uses `SELECTEDVALUE('Aircraft Turn'[Fleet Family],
  "...")` -- tested in isolation first given ADR 0016's hang, and
  confirmed fine; the hang there was specific to a field-parameter
  display column, not `SELECTEDVALUE` on an ordinary column.
  `validate` flagged `PBIR_DRILLTHROUGH_BACK_BUTTON_MISSING` until an
  `actionButton` with `visualLink.type: "Back"` was added -- its icon
  didn't render until `icon.show: true` was set explicitly (the `show`
  property defaults false and is easy to miss since every other
  property on that object implies visibility) and its `icon`/`fill`/
  `outline` objects got `selector: { id: "default" }` the way
  `cardVisual`'s per-type objects already needed in ADR 0016.
- **Delay Root Cause**: "Carrier Delay Minutes by Station" and
  "Controllable Delay Share by Station" (two single-measure bar charts,
  same category) collapsed into one `comboChart`, which actually
  answers a real joint question a reader had to eyeball across two
  panels before: do the stations with the most carrier-caused minutes
  also have the highest controllable share?

Grounded in a short industry check first (see citations in the session,
not reproduced here): real airline/aviation Power BI builds lean on
drillthrough for "move detail off the summary page" and field
parameters for "replace near-identical charts with one toggle" -- field
parameters being the dead end above, drillthrough and chart
consolidation covered the rest.

## Accessibility / UX fixes (found by actually opening Desktop)

All three were found only because the report was opened in Power BI
Desktop and screenshot-reviewed, not caught by `validate` or the
service-host headless render:

1. **Gold contrast failure.** The theme's gold (`#C9A227`), used for
   several KPI card values/accent bars and one chart line, is 2.4:1 on
   white -- fails even the 3:1 non-text floor, let alone 4.5:1 body
   text. Darkened to `#8A6D14` (4.9:1 on white, 4.7:1 on the page's
   `#FAFAFA`), still reads as the same amber family. Changed in both
   `build_report.js`'s `COLOR.gold` token and the theme JSON's
   `dataColors`/`neutral` entries. Per the theme's own cache-busting
   convention (`theming.md`), the file was renamed
   `HubOpsWorkbench-a1f3.json` -> `HubOpsWorkbench-7c2e.json` (keeping
   the base name, rotating the suffix) rather than edited in place, so
   Desktop didn't need a restart to pick it up.
2. **Field-parameter tile pickers clipped.** `Hub KPI` (5 values) and
   `Turn Metric` (2 values) were exposed as `advancedSlicerVisual` tile
   rows at fixed widths; once a value's label was long enough (or there
   were more than ~2-3 values), the row ran out of horizontal space and
   either truncated text or showed a scrollbar instead of all options
   at once -- this is what the project owner saw as "not fully
   displayed." Replaced both with plain dropdown slicers (reusing the
   existing `dropdownSlicer` builder against the field parameter's
   display column) -- no truncation regardless of option count or
   label length, and standard combobox keyboard/screen-reader semantics
   that the custom tile buttons didn't have. Removed the now-unused
   `tileSlicer` function rather than leave dead code.
3. **Axis-label collision on a cramped panel.** "Weather-Adjusted D0
   Gap Trend" plotted Year+Month (44 categories) in a ~600px-wide
   supporting-row panel (a third of the hero chart's width, which shows
   the same grain comfortably at full width). The y-axis max label and
   the `y1AxisReferenceLine`'s own label landed on nearly the same
   pixel row and rendered as unreadable overlapping text, with the line
   itself compressed flat beneath them. Changed to yearly grain (Year
   only, no month breakout) for this panel specifically -- still
   answers "is the gap closing over time" and renders cleanly at this
   width.

Also added `visualContainerObjects.general.altText` (WCAG 1.1.1/4.1.2)
to every `kpiCard`, `barChart`, `lineChart`, and `comboChart` via a
`withAltText` helper, defaulting to the visual's existing `label`/
`title` text when no richer override is given -- a real improvement at
zero marginal cost per call site, short of the skill's gold-standard
DAX-driven dynamic alt text, which wasn't worth bespoke measures across
~40 visuals in this pass. Ran the theme palette's text colors through
the WCAG contrast formula directly (not just gold) -- `steelBlue`,
`forest`, `crimson`, `slate`, `plum`, `ink`, and `muted` all clear 4.5:1
on white/`#FAFAFA`; `burntOrange` and `teal` clear only the 3:1 large-
text/non-text floor, which is what they're used for (24pt card values,
chart lines), so both were left as-is.

## Consequences

- `rpt_hubops` is now 7 pages (6 visible + 1 hidden drillthrough
  target), redeployed via the same `updateDefinition` pattern as every
  prior pass, same item id. All pages validated clean, rendered clean
  against live data via both the service-host headless preview and,
  this time, an actual Power BI Desktop session (opened via the
  `powerbi-desktop` bridge CLI, `PBI_DESKTOP_PATH` pointed at the Store
  app's `WindowsApps\...\bin\PBIDesktop.exe` since it isn't on any of
  the CLI's default search paths) -- screenshots reviewed on every
  page, not just the new ones.
- `sm_hubops` gained 6 tables (`Rebank Effect`, `Rebank Event Study`,
  `Checkpoint Hour`, `CBP Wait Hour`, `TSA Departure Hour Profile`, `CBP
  Hour Profile`), 3 relationships, and ~20 measures; TMDL re-export to
  `fabric/sm_hubops.SemanticModel/` is still pending as a follow-up
  (the modeling MCP's `ExportTMDL` returns one combined blob rather
  than per-table files on disk -- needs a small script to split it,
  not done in this pass).
- `scripts/fabric/build_report.js` gained `comboChart`,
  `drillthroughFilter`, `backButton`, and `withAltText`; `writePage`
  gained `pageType`/`visibility`/`pageBinding` params. Any future page
  needing drillthrough or a dual-axis pairing should reuse these rather
  than hand-author new PBIR.
