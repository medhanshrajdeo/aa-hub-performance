# ADR 0016: Report design pass -- field parameters, dynamic titles, a real pathological bug

**Status:** done, 2026-11-01.

## Context

The first `rpt_hubops` build (ADR 0015) was functionally correct but
visually basic -- default theme, static titles, no field parameters. The
project owner asked for a real design pass: a professional visual
identity, dynamic/context-aware titles, field-parameter-driven metric
switching, and denser, more insightful layouts. This ADR covers that
pass and one genuinely dangerous bug it surfaced.

## Design identity

Followed the `powerbi-report-design` skill's workflow: tone **Industrial
Dense (Analyst Workbench)** -- off-white canvas, charcoal type, steel-
blue/burnt-orange two-color accent pair (not AA's own livery colors,
consistent with the project's no-AA-branding rule), tabular-numeral KPI
cards, 8px modular grid. Archetype **Analytical Canvas, Variant B
(Inline-Slicers)** for all 4 pages (1-3 slicers each, so a vertical
filter rail wasn't justified). Canvas upgraded from the original
1280x720 to FHD 1920x1080 for the density this tone calls for.
Implemented as a registered custom theme
(`fabric/rpt_hubops.Report/StaticResources/RegisteredResources/HubOpsWorkbench-a1f3.json`),
adapted from the design skill's own `assets/base.json` baseline
(preserving its per-type safeguards: zero-padding cards, borderless
textboxes, grow-to-fit tables) rather than written from scratch.

Signature: tabular numerals + YoY-status-conditional KPI card accent
bars (green/amber/crimson via `accentBar.color`'s `Conditional.Cases`,
driven by the existing `D0 vs Same Period LY Pts` / `A14 vs Same Period
LY Pts` measures -- a real, data-grounded threshold, not an arbitrary
external target) + FillRule value-gradient bars on every single-measure
chart + data-bar tables.

## Field parameters

Two real Power BI field parameters built via `table_operations
CreateFieldParameter` on the MCP tool: `Hub KPI` (5 Flight Leg measures,
Hub Scorecard page) and `Turn Metric` (2 Aircraft Turn measures, Turn
Performance page). Both are genuine model-level field parameters,
correctly built via the official API, confirmed via `EVALUATE 'Hub
KPI'` returning the expected 3-column parameter-table shape.

**What didn't work, and why:** the natural next step -- binding a field
parameter's `<Name> Fields` column into a chart's Y-axis so the analyst
can swap the plotted measure from a slicer -- fails. A plain `Column`
projection in a Measure-kind role (`Y`, `Data`) is rejected by
`powerbi-report-author validate` (`PBIR_ROLE_KIND_MISMATCH`), and
forcing it through anyway fails live render too:
`InvalidUnconstrainedJoin` / *"Can't determine relationships between the
fields."* Confirmed on both a `lineChart` Y role and a bare `cardVisual`
Data role -- not a per-visual quirk. Desktop's UI generates a different,
undocumented query encoding when you drag a field parameter into a
well (public PBIR schemas don't cover it), and that encoding isn't
reverse-engineerable from validator/render error messages alone.
**Resolution:** kept the field parameter tables as real, working model
objects, exposed via a `advancedSlicerVisual` tile picker bound to the
`<Name>` display column (a plain Grouping-kind role -- no conflict) for
analyst self-service in Desktop. Charts themselves use ordinary
multi-measure bindings. Honest, not a hack: the parameters are real and
usable, just not pre-wired by the report author into a locked chart.

## A genuinely dangerous bug: SELECTEDVALUE on a field-parameter column hangs

Building a dynamic page title for Turn Performance
(`"Turn Performance — DFW · " & SELECTEDVALUE('Turn Metric'[Turn Metric])`)
produced a measure that **hangs** -- not an error, a hang. A direct
`EVALUATE ROW ( "T", [Turn Performance Title] )` DAX query against it
ran past 120 seconds before being killed. Bound into a page via a
textbox, the entire page failed to render with an unhelpful
`QueryUserError` / *"Could not render a report visual titled:
undefined."*

This took real, methodical bisection to isolate -- removing half the
page's visuals at a time (hero chart, then supporting charts, then KPI
cards, then the tile slicer) still failed with every other visual
stripped out, down to just the title textbox and a static caveat
textbox. Testing the measure directly via DAX query (not through a
visual at all) confirmed it: the hang is in the measure itself, not in
how a visual binds to it. The other three dynamic titles (`Hub
Scorecard Title`, `Bank Performance Title`, `Delay Root Cause Title`)
all use `SELECTEDVALUE` against ordinary connected tables (`Airport`,
`Bank`, `Date`) and work fine -- confirming the trigger is specifically
`SELECTEDVALUE` against a **field parameter's display column**, not
`SELECTEDVALUE` in general.

**Fix:** rewrote `Turn Performance Title` to a plain string literal (no
field-parameter reference). Also fixed `Hub KPI Trend Title` the same
way pre-emptively -- it shared the exact same pattern and was unused by
any visual at the time, but would have hit the identical hang the
moment anyone wired it up.

**Why this matters beyond this one measure:** a field parameter's
`<Name>` display column is not an ordinary column -- like its `<Name>
Fields` sibling, it carries special resolution semantics tied to how
Desktop's query engine handles field parameters, and those semantics
don't degrade gracefully outside a proper visual-authored binding. Any
future measure, calculated column, or DAX expression anywhere in this
model that calls `SELECTEDVALUE`, `VALUES`, or similar on `'Hub
KPI'[Hub KPI]` or `'Turn Metric'[Turn Metric]` (or their `Fields`
siblings) should be treated as suspect and tested in isolation with a
timeout before being trusted.

## Other real bugs fixed this pass

- Three Turn Performance KPI cards (`Outbound D0 %`, `Minutes Recovered
  per Turn`, `Turn Count`, `Hub Throughput Pts`) were unfiltered despite
  labels claiming "DFW" -- added the missing per-visual `filterConfig`.
- `topNFilter`'s `OrderBy` must reference an actual **column** via
  `Aggregation` -- passing a DAX **measure** name (`Turn Count`) fails
  render with `CouldNotResolveModelReferencesInSemanticQuery`. Fixed by
  ranking fleet-level bar charts on `Count` of a real column
  (`Scheduled Turn Min`) instead, ranking Fleet Family by estimated turn
  volume -- cleaned up 23-category bar charts that were illegibly dense
  (`Top 10 by Volume`) using the same TopN mechanism already built for
  "worst 8" station-days.

## Consequences

- `rpt_hubops` redeployed via `az rest POST
  .../reports/{id}/updateDefinition` (`pack --mode update`) -- same item
  (`d8404b97-b98a-4228-97cf-3b86824de65c`), new design. All 4 pages
  validate clean and render clean against live data (screenshot-
  reviewed, not just schema-validated).
- `sm_hubops` gained 2 field parameter tables and re-exported TMDL
  reflects them plus the two corrected title measures.
- `scripts/fabric/build_report.js` is the one place all of this logic
  lives -- theme tokens, card/chart/table/slicer builders, the TopN and
  dynamic-title helpers. Any Phase 8 page (5-8) should extend it, not
  hand-author new PBIR.
