# ADR 0019: Typography and spacing pass

**Status:** done, 2026-11-01.

## Context

Direct feedback after ADR 0018's palette pass: "I would've liked my
dashboard to be designed better and not have such small fonts and
weird spacing." A fair complaint, confirmed on inspection -- the report
had two real, concrete issues, not just a vague "make it nicer" ask.

## What was actually wrong

1. **KPI cards were mostly empty space.** `kpiCard`'s value text sat at
   24pt in a 120px-tall card with no vertical centering -- the label +
   value block filled maybe half the card's height, leaving an
   unexplained blank zone at the bottom of every card on every page.
2. **Every chart showed a redundant, tiny auto-subtitle.** Power BI
   auto-generates a gray caption under a visual's title from its field
   list (e.g. "D0 % and A14 % by Year and Month Name" under a chart
   already titled "D0 % and A14 % by Month") unless `subTitle.show` is
   explicitly set to `false`. Nothing in the generator had ever turned
   this off, so every chart carried a second, smaller, duplicate line
   of text -- real clutter, and ~20px of wasted vertical space per
   chart that compounded the first problem.
3. **Table, axis, and legend text was uniformly 9-10pt** across the
   theme -- legible but genuinely small for a report meant to be read
   at a normal viewing distance, not a phone screen.

## Fixes

- `staticTitleVCO`/`dynamicTitleVCO` (used by every chart and table)
  now set `subTitle.show: false` and bumped the title itself 14D ->
  15D -- one change, cascades everywhere since every chart/table
  routes through these two helpers.
- `kpiCard`: value 24D -> 26D, label 10D -> 11D, and `kpiRow` height
  trimmed from 120 to 100px on pages 1-4 (104px was already more than
  the content needed even before the font bump -- the box was oversized
  for its content, not undersized) -- the 20px freed per page went to
  the hero chart row (280 -> 300), not wasted. Fleet Turn Detail's KPI
  row (the drillthrough page) dropped from 240 to 160 for the same
  reason -- it was never about font size there, the box was just much
  taller than 4 cards needed.
- Theme (`HubOpsWorkbench-5a1c.json`, new cache-busting suffix):
  `tableEx`/`pivotTable` column headers and values 10 -> 11pt, bar/
  column/line chart axis labels and data labels 10 -> 11pt, legends
  9 -> 10pt, slicer header/items 10/9 -> 11/10pt. Also added a
  `lineClusteredColumnComboChart` entry -- it had no theme styling at
  all before this pass (fell back to Power BI's base Fluent theme
  defaults, inconsistent with every other chart type), so the Rebank
  Impact/Passenger Flow/Delay Root Cause combo charts were silently
  off-theme until now.
- Caption textboxes (the italic "Scope: ..." notes) went 9pt -> 10pt.

**Real bug caught during verification, not before shipping it**: Turn
Performance's and Delay Root Cause's bottom detail tables
(`detailRow.h: 196`) had just enough room for header + 3 data rows +
total at 10pt. At 11pt, the 3rd data row visually overlapped the Total
row -- caught via a cropped close-up screenshot, not visible at a
glance on the full-page render. Fixed by growing `detailRow` to 224px
on both pages (there was 40-44px of unused canvas slack on these two
pages already, confirmed by summing their row-height arrays against
the 1080px canvas, so this didn't require shrinking anything else).

## Consequences

- `rpt_hubops` redeployed via the same `updateDefinition` pattern,
  same item id. All 7 pages re-validated clean; re-rendered clean
  against live data, including a second close-up pass specifically
  checking for row-overlap after the font bump (the kind of defect a
  full-page screenshot alone doesn't surface).
- One screenshot in this pass showed Fleet Turn Detail's KPI cards
  completely blank and a caption on Passenger Flow with a garbled word
  ("seament" instead of "segment," which the source text has correctly).
  Both were confirmed transient headless-render glitches, not real
  defects, by re-rendering the same page in isolation and getting
  correct output both times. Noted here so a future session doesn't
  waste time chasing a ghost if it recurs -- the fix, if it's not a
  one-off, is to always single-page-retry anything that looks wrong
  in a batch `--all-pages` capture before treating it as real.
