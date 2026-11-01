# ADR 0018: American-inspired palette, removing the non-functional metric pickers

**Status:** done, 2026-11-01.

## Context

Two pieces of direct feedback after ADR 0017's pages/drillthrough/a11y
pass: the "Hero Chart Metric" / "Chart Metric" dropdowns on Hub
Scorecard and Turn Performance didn't do anything when changed, and
with "what is it even supposed to achieve" -- a fair question; and a
request to theme the dashboard as American Airlines and make it more
visually appealing.

## The metric pickers were never wired to anything

They exposed the `Hub KPI` / `Turn Metric` field parameters (ADR 0016)
as dropdowns so an analyst could, in Power BI **Desktop's edit mode**,
manually drag the parameter onto a chart's axis to swap the plotted
metric -- a real, standard field-parameter workflow, but one that only
works for someone editing the file, not a report viewer. ADR 0016
already established that pre-wiring the swap into the shipped PBIR
(so a *viewer* selecting a dropdown value would see the chart change)
hits a confirmed Power BI limitation --
`InvalidUnconstrainedJoin`/`PBIR_ROLE_KIND_MISMATCH` binding a field
parameter's display column into a Measure-kind role, tested on both a
line chart's Y-axis and a card's Data role. That wasn't retried here;
it's a closed question. For a shipped, viewer-facing report the
dropdown was therefore just a control that visibly did nothing --
exactly the "doesn't work" the project owner flagged. Removed both
`dropdownSlicer` calls from `build_report.js`; removed the now-dead
`tileSlicer` helper was already done in ADR 0017, this removes its
dropdown successor too. The `Hub KPI` / `Turn Metric` field parameter
tables are left in `sm_hubops` -- harmless, and still available in
Desktop's Fields pane for anyone who wants to manually drag one onto
an axis, which was always the actual intended use.

## American Airlines-inspired palette

`CLAUDE.md` has an explicit rule for this project: "no AA logos, no
non-public data." Interpreted that as scoping the constraint to
trademarked assets (logo, wordmark, flight symbol) -- not to American's
publicly known brand hues, which many aviation-industry color
references cite without implying endorsement. Shifted the two most
load-bearing tokens in `build_report.js`'s `COLOR` object (kept the
variable names `steelBlue`/`crimson` to minimize code churn -- they
cascade through every `kpiCard`/`barChart`/`lineChart`/`comboChart`
call automatically, no per-call-site edits needed):

- `steelBlue`: `#3B6E91` -> `#003865` (a deep American-blue navy,
  11.98:1 contrast on white)
- `crimson`: `#B23A48` -> `#C8102E` (American red, 5.88:1 on white)

Both still comfortably clear WCAG AA body text (the contrast work from
ADR 0017 wasn't just a one-time fix -- recomputed for every palette
change going forward). Red already carried the "bad status" semantic
meaning (KPI card status-conditional accent, `bad` theme sentiment
color) before this pass; unifying it with the brand red means the
Rebank Impact page's negative effect-size cards now read as both
"this is bad news" and "this is an American Airlines tool" from the
same color, rather than two different reds competing. Page titles
changed from near-black (`#262626`) to the new navy, and every page
gained a short red accent rule (a `shape` visual, `tileShape:
"rectangle"`) directly under the title -- a small but real brand
signal repeated consistently across all 7 pages.

**Real bug found building the accent rule**: the theme's wildcard
`visualStyles["*"]["*"]` gives every visual an 8px-padded, bordered
white container by default. A 4px-tall shape dropped straight in
rendered as a blank white rounded box -- the container chrome
completely swallowed the thin colored rectangle inside it. Fixed by
explicitly overriding `visualContainerObjects.background/border/padding`
to `show: false` / zero padding for this one visual type, the same
category of fix `kpiCard` and `textbox` already needed against this
theme's wildcard defaults.

Theme file renamed `HubOpsWorkbench-7c2e.json` ->
`HubOpsWorkbench-2f91.json` per the established cache-busting
convention (rotate the GUID suffix, keep the base name, update both
`report.json` references) so Desktop picks up the new colors on
reload without a restart.

## Consequences

- `rpt_hubops` redeployed via the same `updateDefinition` pattern,
  same item id. All 7 pages re-validated clean and re-rendered clean
  against live data via the service-host headless preview.
- Hit a real Power BI Desktop bridge bug while verifying in Desktop
  directly: after reloading a PBIP whose page directories had all been
  deleted and recreated (new page IDs) twice in one session, the
  bridge's `status` RPC kept reporting the correct, current page list,
  but its `screenshot`/`screenshot-all` RPCs failed every page with
  `Unknown pageId`, even immediately after a fresh `reload` and after
  relaunching `open` against the same file (which attached to the same
  stale process rather than starting clean). Could not recover without
  killing the stale `PBIDesktop.exe` process, which this session's
  permissions don't allow. Fell back to the already-proven service-host
  headless preview for final verification instead -- same rendering
  pipeline as the rest of this project's verification, just not inside
  Desktop's own process. Not filed as a project ADR-level blocker since
  it's a CLI/bridge issue, not a report-authoring one, but worth
  knowing: a Desktop session that's survived several page-directory
  wipes-and-regens in a row is a reasonable thing to suspect first if
  `screenshot` ever disagrees with `status` about which pages exist.
