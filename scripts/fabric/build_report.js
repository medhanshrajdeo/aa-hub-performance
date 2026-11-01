// Generates rpt_hubops's 7 report pages (6 visible + 1 hidden drillthrough
// target) (PBIR) on top of sm_hubops, per the
// "Industrial Dense / Analyst Workbench" design identity (see
// fabric/rpt_hubops.Report/StaticResources/RegisteredResources/HubOpsWorkbench-*.json):
// off-white canvas, navy/red accent pair inspired by American Airlines'
// public brand colors (no logo/wordmark/trademarked imagery -- see the
// "no AA logos" rule in CLAUDE.md), navy page titles with a red accent
// rule, tabular-numeral KPI cards with YoY-status accent bars, FillRule-
// gradient single-measure bars, and dynamic (measure-bound) titles.
// Analytical Canvas archetype, Variant B (Inline-Slicers, 1-3 slicers/page),
// FHD 1920x1080. Run with: node scripts/fabric/build_report.js
//
// Two scope simplifications versus the plan's literal page descriptions
// remain from the first pass (see ADR 0014/0015): Bank Performance shows
// bank volume waves + pre/post D0 instead of delay-by-bank (no bank join on
// fact_flight_leg); Delay Root Cause is by month instead of by hour/bank
// (Direct Lake can't carry a calculated hour column).

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const REPORT_DIR = path.join(__dirname, "..", "..", "fabric", "rpt_hubops.Report");
const PAGES_DIR = path.join(REPORT_DIR, "definition", "pages");

const hex = (n) => crypto.randomBytes(n).toString("hex");
const visualId = () => hex(10);
const pageId = () => hex(10);
const filterId = () => "Filter" + hex(12);

const SCHEMA_VISUAL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json";
const SCHEMA_PAGE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json";

const CANVAS_W = 1920;
const CANVAS_H = 1080;
const MARGIN = 32;
const GUTTER = 24;
const CONTENT_W = CANVAS_W - 2 * MARGIN;

// ---- design tokens (mirrors HubOpsWorkbench-7c2e.json dataColors) ----
// Palette shifted toward American Airlines' public brand colors (deep
// navy blue + red, no logo/wordmark/trademarked imagery used anywhere --
// see the project's "no AA logos" rule in CLAUDE.md). steelBlue/crimson
// keep their original token names (minimize code churn) but now carry
// AA Navy / AA Red hex values; both clear WCAG AA body-text contrast on
// white (11.98:1 and 5.88:1 respectively).
const COLOR = {
  steelBlue: "#003865",
  steelBlueTint: "#D6E2EB",
  burntOrange: "#C8742A",
  burntOrangeTint: "#F3DFC9",
  forest: "#1C5E47",
  forestTint: "#CFE3DB",
  crimson: "#C8102E",
  crimsonTint: "#F5D7DB",
  slate: "#64748B",
  slateTint: "#DEE3E9",
  // #C9A227 (the theme's original gold) fails WCAG non-text contrast on
  // white (2.4:1, needs 3:1) and body text (needs 4.5:1) -- this darker
  // shade holds the same amber identity at 4.9:1 on white / 4.7:1 on
  // #FAFAFA, passing AA body text everywhere it's used as card/line color.
  gold: "#8A6D14",
  goldTint: "#F2E6C2",
  teal: "#2A9D8F",
  tealTint: "#CFEAE7",
  plum: "#6B4C7A",
  ink: "#262626",
  muted: "#605E5C",
  hairline: "#E0E0E0",
};

// ---- expression helpers ----
const col = (table, property) => ({ Column: { Expression: { SourceRef: { Entity: table } }, Property: property } });
const meas = (table, property) => ({ Measure: { Expression: { SourceRef: { Entity: table } }, Property: property } });

const colProj = (table, property, opts = {}) => ({
  field: col(table, property),
  queryRef: `${table}.${property}`,
  nativeQueryRef: property,
  ...opts,
});
const measProj = (table, property) => ({
  field: meas(table, property),
  queryRef: `${table}.${property}`,
  nativeQueryRef: property,
});

const litExpr = (value) => ({ expr: { Literal: { Value: value } } });
const solidLit = (hex9) => ({ solid: { color: { expr: { Literal: { Value: `'${hex9}'` } } } } });

// ---- layout stacker: vertical stack of rows within the content width ----
function layout(startY, rows) {
  // rows: [{ h }] -> returns [{ y, h }] with GUTTER between, snapped to 8px
  let y = startY;
  const out = [];
  for (const r of rows) {
    out.push({ y, h: r.h });
    y += r.h + GUTTER;
  }
  return out;
}
function cols(n, totalW = CONTENT_W, startX = MARGIN) {
  // even split of totalW into n columns with GUTTER between -> [{x,w}]
  const w = Math.round((totalW - (n - 1) * GUTTER) / n);
  const out = [];
  for (let i = 0; i < n; i++) out.push({ x: startX + i * (w + GUTTER), w });
  return out;
}

// ---- visual container ----
function visualContainer({ name, x, y, z, w, h, visual }) {
  return {
    $schema: SCHEMA_VISUAL,
    name,
    position: { x, y, z, height: h, width: w, tabOrder: z },
    visual,
  };
}

// Accessibility: WCAG 1.1.1 / 4.1.2 need every visual to have an
// accessible name/description for screen readers. Merges onto whatever
// VCO (title-bearing or not) the caller already built.
function withAltText(vco, text) {
  if (!text) return vco;
  const safe = text.replace(/'/g, "");
  const base = vco || {};
  return { ...base, general: [{ properties: { altText: litExpr(`'${safe}'`) } }] };
}

// subTitle:false kills Power BI's auto-generated "field list" caption
// under the custom title (e.g. "D0 % and A14 % by Year and Month Name")
// -- pure clutter once there's already a real, descriptive title, and it
// was eating ~20px of dead vertical space under every chart.
function staticTitleVCO(text, size = "15D") {
  return {
    title: [{ properties: {
      show: litExpr("true"),
      text: litExpr(`'${text}'`),
      fontSize: litExpr(size),
      alignment: litExpr("'left'"),
    } }],
    subTitle: [{ properties: { show: litExpr("false") } }],
  };
}

// Dynamic title bound to a DAX measure instead of a literal string.
function dynamicTitleVCO(table, measureName, size = "15D") {
  return {
    title: [{ properties: {
      show: litExpr("true"),
      text: { expr: meas(table, measureName) },
      fontSize: litExpr(size),
      alignment: litExpr("'left'"),
    } }],
    subTitle: [{ properties: { show: litExpr("false") } }],
  };
}

// ============================================================
// Cards
// ============================================================
// measure: { table, name, label, accent, accentTint, referenceMeasure? (table,name) }
function kpiCard({ name, x, y, z, w, h, table, measureName, label, accent, statusConditional, filters, altText }) {
  const accentColorExpr = statusConditional
    ? { solid: { color: { expr: {
        Conditional: {
          Cases: [
            { Condition: { Comparison: { ComparisonKind: 2, Left: meas(statusConditional.table, statusConditional.measure), Right: { Literal: { Value: "0D" } } } }, Value: { Literal: { Value: `'${COLOR.forest}'` } } },
            { Condition: { Comparison: { ComparisonKind: 2, Left: meas(statusConditional.table, statusConditional.measure), Right: { Literal: { Value: "-2D" } } } }, Value: { Literal: { Value: `'${COLOR.gold}'` } } },
          ],
          DefaultValue: { Literal: { Value: `'${COLOR.crimson}'` } },
        },
      } } } }
    : solidLit(accent);

  const projections = [measProj(table, measureName)];

  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "cardVisual",
      query: { queryState: { Data: { projections } } },
      objects: {
        outline: [{ properties: { show: litExpr("false") }, selector: { id: "default" } }],
        accentBar: [{ properties: { show: litExpr("true"), position: litExpr("'Left'"), width: litExpr("4D"), color: accentColorExpr }, selector: { id: "default" } }],
        layout: [{ properties: {
          topOuterMargin: litExpr("0L"), bottomOuterMargin: litExpr("0L"),
          leftOuterMargin: litExpr("0L"), rightOuterMargin: litExpr("0L"),
          paddingUniform: litExpr("0L"),
        }, selector: { id: "default" } }],
        padding: [{ properties: {
          topMargin: litExpr("0L"), bottomMargin: litExpr("0L"),
          leftMargin: litExpr("12L"), rightMargin: litExpr("8L"),
        }, selector: { id: "default" } }],
        value: [{ properties: { fontSize: litExpr("26D"), fontColor: solidLit(accent) }, selector: { id: "default" } }],
        label: [{ properties: { text: litExpr(`'${label}'`), fontSize: litExpr("11D"), fontColor: solidLit(COLOR.muted) }, selector: { id: "default" } }],
      },
      visualContainerObjects: withAltText({
        background: [{ properties: { show: litExpr("true"), color: solidLit("#FFFFFF"), transparency: litExpr("0D") } }],
        border: [{ properties: { show: litExpr("true"), color: solidLit(COLOR.hairline), radius: litExpr("8D") } }],
        padding: [{ properties: { top: litExpr("0D"), bottom: litExpr("0D"), left: litExpr("0D"), right: litExpr("0D") } }],
        visualHeader: [{ properties: { show: litExpr("false") } }],
      }, altText || label),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Bar / column charts, with FillRule gradient for single-measure charts
// ============================================================
const AGG_NAME = { 0: "Sum", 1: "Average", 2: "Count", 3: "Min", 4: "Max", 5: "CountNonNull", 6: "Median", 7: "StandardDeviation", 8: "Variance" };

function yField(y) {
  if (y.agg !== undefined) {
    return {
      field: { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Entity: y.table } }, Property: y.property } }, Function: y.agg } },
      queryRef: `${AGG_NAME[y.agg]}(${y.table}.${y.property})`,
      nativeQueryRef: `${AGG_NAME[y.agg]} of ${y.property}`,
    };
  }
  return measProj(y.table, y.property);
}

function barChart({ name, x, y, z, w, h, type = "clusteredColumnChart", title, dynamicTitle, table, category, categoryExtra = [], series, yMeasures, sort, filters, gradient, invertAxis, altText }) {
  const categoryProjections = [colProj(table, category, { active: true }), ...categoryExtra.map((c) => colProj(c.table, c.property, { active: true }))];
  const queryState = {
    Category: { projections: categoryProjections },
    Y: { projections: yMeasures.map(yField) },
  };
  if (series) queryState.Series = { projections: [colProj(series.table, series.property)] };
  const query = { queryState };
  if (sort) {
    query.sortDefinition = { sort: [{ field: sort.agg !== undefined ? { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Entity: sort.table } }, Property: sort.property } }, Function: sort.agg } } : meas(sort.table, sort.property), direction: sort.direction }], isDefaultSort: true };
  }

  const objects = {
    legend: yMeasures.length > 1 || series ? [{ properties: { position: litExpr("'Top'") } }] : undefined,
  };
  if (gradient) {
    const yq = yField(yMeasures[0]).queryRef;
    objects.dataPoint = [{
      properties: {
        fill: { solid: { color: { expr: { FillRule: {
          Input: yMeasures[0].agg !== undefined
            ? { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Entity: yMeasures[0].table } }, Property: yMeasures[0].property } }, Function: yMeasures[0].agg } }
            : meas(yMeasures[0].table, yMeasures[0].property),
          FillRule: { linearGradient2: {
            min: { color: { Literal: { Value: `'${gradient.min}'` } } },
            max: { color: { Literal: { Value: `'${gradient.max}'` } } },
            nullColoringStrategy: { strategy: { Literal: { Value: "'noColor'" } } },
          } },
        } } } } },
      },
      selector: { data: [{ dataViewWildcard: { matchingOption: 0 } }] },
    }];
  }
  if (invertAxis) {
    objects.categoryAxis = [{ properties: { invertAxis: litExpr("true") } }];
    objects.valueAxis = [{ properties: { invertAxis: litExpr("true") } }];
  }

  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: type,
      query,
      objects,
      visualContainerObjects: withAltText(dynamicTitle ? dynamicTitleVCO(dynamicTitle.table, dynamicTitle.measure) : (title ? staticTitleVCO(title) : undefined), altText || title),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Line chart, optionally bound to a field-parameter "Fields" column
// ============================================================
function lineChart({ name, x, y, z, w, h, title, dynamicTitle, table, category, categoryExtra = [], yMeasures, yFieldParam, referenceLine, filters, altText }) {
  const categoryProjections = [colProj(table, category, { active: true }), ...categoryExtra.map((c) => colProj(c.table, c.property, { active: true }))];
  let yProjections;
  if (yFieldParam) {
    yProjections = [colProj(yFieldParam.table, yFieldParam.fieldsColumn)];
  } else {
    yProjections = yMeasures.map((m) => measProj(m.table, m.property));
  }
  const objects = {
    legend: [{ properties: { position: litExpr("'Top'") } }],
    dataPoint: yMeasures && yMeasures.length === 1 ? [{ properties: { defaultColor: solidLit(yMeasures[0].color || COLOR.steelBlue) } }] : undefined,
  };
  if (referenceLine !== undefined) {
    objects.y1AxisReferenceLine = [{ properties: {
      show: litExpr("true"),
      value: litExpr(`${referenceLine}D`),
      lineColor: solidLit(COLOR.slate),
      style: litExpr("'dashed'"),
      transparency: litExpr("10D"),
    } }];
  }
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "lineChart",
      query: { queryState: {
        Category: { projections: categoryProjections },
        Y: { projections: yProjections },
      } },
      objects,
      visualContainerObjects: withAltText(dynamicTitle ? dynamicTitleVCO(dynamicTitle.table, dynamicTitle.measure) : (title ? staticTitleVCO(title) : undefined), altText || title),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Dual-axis combo chart (columns on Y / primary axis, lines on Y2 /
// secondary axis) -- for pairing series with very different magnitudes
// (e.g. passenger counts vs. flight counts) on one timeline.
// ============================================================
function comboChart({ name, x, y, z, w, h, title, table, category, yColumns, y2Columns, referenceLine, filters, sort, altText }) {
  const query = { queryState: {
    Category: { projections: [colProj(table, category, { active: true })] },
    Y: { projections: yColumns.map(yField) },
    Y2: { projections: y2Columns.map(yField) },
  } };
  if (sort) {
    query.sortDefinition = { sort: [{ field: sort.agg !== undefined ? { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Entity: sort.table } }, Property: sort.property } }, Function: sort.agg } } : meas(sort.table, sort.property), direction: sort.direction }], isDefaultSort: true };
  }
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "lineClusteredColumnComboChart",
      query,
      objects: {
        legend: [{ properties: { position: litExpr("'Top'") } }],
        ...(referenceLine !== undefined ? { y1AxisReferenceLine: [{ properties: {
          show: litExpr("true"), value: litExpr(`${referenceLine}D`),
          lineColor: solidLit(COLOR.slate), style: litExpr("'dashed'"), transparency: litExpr("10D"),
        } }] } : {}),
      },
      visualContainerObjects: withAltText(title ? staticTitleVCO(title) : undefined, altText || title),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Tables / matrices
// ============================================================
function pivotTable({ name, x, y, z, w, h, title, rows, values, filters, dataBarOn }) {
  const valueProjs = values.map((v) => measProj(v.table, v.property));
  const objects = {
    columnHeaders: [{ properties: {
      columnAdjustment: litExpr("'growToFit'"),
      autoSizeColumnWidth: litExpr("true"),
    } }],
  };
  if (dataBarOn) {
    const qr = `${dataBarOn.table}.${dataBarOn.property}`;
    objects.columnFormatting = [{
      properties: { dataBars: {
        positiveColor: solidLit(COLOR.steelBlue),
        negativeColor: solidLit(COLOR.crimson),
        axisColor: solidLit(COLOR.slate),
        reverseDirection: litExpr("false"),
        hideText: litExpr("false"),
      } },
      selector: { metadata: qr },
    }];
  }
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "pivotTable",
      query: { queryState: {
        Rows: { projections: rows.map((r) => colProj(r.table, r.property)) },
        Values: { projections: valueProjs },
      } },
      objects,
      visualContainerObjects: title ? staticTitleVCO(title) : undefined,
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

function tableExVisual({ name, x, y, z, w, h, title, dynamicTitle, columns, filters, sort, dataBarOn }) {
  const query = { queryState: { Values: { projections: columns.map((c) => (c.measure ? measProj(c.table, c.property) : colProj(c.table, c.property))) } } };
  if (sort) query.sortDefinition = { sort: [{ field: sort.measure ? meas(sort.table, sort.property) : col(sort.table, sort.property), direction: sort.direction }], isDefaultSort: true };
  const objects = {
    columnHeaders: [{ properties: {
      columnAdjustment: litExpr("'growToFit'"),
      autoSizeColumnWidth: litExpr("true"),
    } }],
  };
  if (dataBarOn) {
    const qr = `${dataBarOn.table}.${dataBarOn.property}`;
    objects.columnFormatting = [{
      properties: { dataBars: {
        positiveColor: solidLit(COLOR.steelBlue),
        negativeColor: solidLit(COLOR.crimson),
        axisColor: solidLit(COLOR.slate),
        reverseDirection: litExpr("false"),
        hideText: litExpr("false"),
      } },
      selector: { metadata: qr },
    }];
  }
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "tableEx",
      query,
      objects,
      visualContainerObjects: dynamicTitle ? dynamicTitleVCO(dynamicTitle.table, dynamicTitle.measure) : (title ? staticTitleVCO(title) : undefined),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Slicers
// ============================================================
function dropdownSlicer({ name, x, y, z, w = 180, h = 80, table, property, header }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "slicer",
      query: { queryState: { Values: { projections: [colProj(table, property)] } } },
      objects: {
        data: [{ properties: { mode: litExpr("'Dropdown'") } }],
        header: [{ properties: { show: litExpr("true"), text: litExpr(`'${header}'`) } }],
      },
      visualContainerObjects: {
        padding: [{ properties: { top: litExpr("8D"), bottom: litExpr("8D"), left: litExpr("8D"), right: litExpr("8D") } }],
      },
    },
  });
}

// Back-navigation button, required on drillthrough pages.
function backButton({ name, x, y, z, w = 48, h = 48 }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "actionButton",
      objects: {
        icon: [{ properties: { show: litExpr("true"), shapeType: litExpr("'back'"), lineColor: solidLit(COLOR.steelBlue), lineWeight: litExpr("3L"), iconSize: litExpr("24D") }, selector: { id: "default" } }],
        fill: [{ properties: { show: litExpr("true"), fillColor: solidLit("#FFFFFF"), transparency: litExpr("0D") }, selector: { id: "default" } }],
        outline: [{ properties: { show: litExpr("true"), lineColor: solidLit(COLOR.hairline), weight: litExpr("1D") }, selector: { id: "default" } }],
      },
      visualContainerObjects: {
        visualLink: [{ properties: { show: litExpr("true"), type: litExpr("'Back'") } }],
      },
    },
  });
}

// Thin brand-accent rule (AA red) under each page title.
function accentRule({ name, x, y, z, w, h = 4 }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "shape",
      objects: {
        shape: [{ properties: { tileShape: litExpr("'rectangle'") }, selector: { id: "default" } }],
        fill: [{ properties: { show: litExpr("true"), fillColor: solidLit(COLOR.crimson) }, selector: { id: "default" } }],
        outline: [{ properties: { show: litExpr("false") }, selector: { id: "default" } }],
      },
      visualContainerObjects: {
        // The theme's wildcard visualStyles["*"]["*"] gives every visual an
        // 8px padded, bordered white container by default -- fine for
        // charts/cards, but it swallows a 4px-tall accent bar inside a
        // bigger blank rounded box. Strip it for this one visual type.
        background: [{ properties: { show: litExpr("false") } }],
        border: [{ properties: { show: litExpr("false") } }],
        padding: [{ properties: { top: litExpr("0D"), bottom: litExpr("0D"), left: litExpr("0D"), right: litExpr("0D") } }],
        visualHeader: [{ properties: { show: litExpr("false") } }],
      },
    },
  });
}

function textbox({ name, x, y, z, w, h, text, size = "10pt", italic = true, color = COLOR.muted }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "textbox",
      objects: {
        general: [{ properties: {
          paragraphs: [{ textRuns: [{ value: text, textStyle: { fontSize: size, color, fontStyle: italic ? "italic" : "normal" } }] }],
        } }],
      },
    },
  });
}

// TopN filter: bottom/top N distinct values of a (near-unique, continuous)
// column, ranked by Min of itself -- used to show "worst/best N rows" on a
// fact-grain table without a natural category column to group by.
// Ranks `property` (the visual's category column) by an Aggregation of
// `byProperty` (defaults to `property` itself -- the original use case:
// ranking a near-unique continuous column by Min of itself). Function 3=Min, 4=Max.
function topNFilter(table, property, n, direction, byProperty = property, byFunction = 3) {
  return {
    name: filterId(),
    field: col(table, property),
    type: "TopN",
    filter: {
      Version: 2,
      From: [
        {
          Name: "subquery",
          Expression: { Subquery: { Query: {
            Version: 2,
            From: [{ Name: "t", Entity: table, Type: 0 }],
            Select: [{ Column: { Expression: { SourceRef: { Source: "t" } }, Property: property }, Name: "field" }],
            OrderBy: [{ Direction: direction, Expression: { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Source: "t" } }, Property: byProperty } }, Function: byFunction } } }],
            Top: n,
          } } },
          Type: 2,
        },
        { Name: "t2", Entity: table, Type: 0 },
      ],
      Where: [{ Condition: { In: {
        Expressions: [{ Column: { Expression: { SourceRef: { Source: "t2" } }, Property: property } }],
        Table: { SourceRef: { Source: "subquery" } },
      } } }],
    },
    howCreated: "User",
  };
}

// Visual-level categorical filter: "<table>[<property>] = <literalValue>"
function categoricalFilter(table, property, literalValue) {
  return {
    name: filterId(),
    field: col(table, property),
    type: "Categorical",
    filter: {
      Version: 2,
      From: [{ Name: "t", Entity: table, Type: 0 }],
      Where: [{ Condition: { In: {
        Expressions: [{ Column: { Expression: { SourceRef: { Source: "t" } }, Property: property } }],
        Values: [[{ Literal: { Value: literalValue } }]],
      } } }],
    },
    howCreated: "User",
  };
}

// A drillthrough target page needs: a page-level filter with
// howCreated: "Drillthrough" for the field being drilled on, plus a
// pageBinding.parameters entry pointing back at that filter by name.
function drillthroughFilter(table, property) {
  const name = filterId();
  return { name, field: col(table, property), type: "Categorical", howCreated: "Drillthrough" };
}

function writePage({ displayName, filters, pageType, visibility, pageBinding, build }) {
  const id = pageId();
  const dir = path.join(PAGES_DIR, id);
  fs.mkdirSync(path.join(dir, "visuals"), { recursive: true });

  const page = {
    $schema: SCHEMA_PAGE,
    name: id,
    displayName,
    displayOption: "FitToPage",
    height: CANVAS_H,
    width: CANVAS_W,
    objects: { background: [{ properties: { color: solidLit("#FAFAFA"), transparency: litExpr("0D") } }] },
  };
  if (filters) page.filterConfig = { filters };
  if (pageType) page.type = pageType;
  if (visibility) page.visibility = visibility;
  if (pageBinding) page.pageBinding = pageBinding;
  fs.writeFileSync(path.join(dir, "page.json"), JSON.stringify(page, null, 2));

  const visuals = build();
  for (const v of visuals) {
    const vDir = path.join(dir, "visuals", v.name);
    fs.mkdirSync(vDir, { recursive: true });
    fs.writeFileSync(path.join(vDir, "visual.json"), JSON.stringify(v, null, 2));
  }
  return id;
}

// ============================================================
// Page 1: Hub Scorecard
// ============================================================
const page1Id = writePage({
  displayName: "Hub Scorecard",
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 100 }, { h: 300 }, { h: 200 }, { h: 240 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    // Title band: dynamic title textbox (navy, AA-red accent rule) + slicers
    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 820, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Hub Scorecard Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 876, y: titleRow.y, z: 1001, w: 200, h: titleRow.h, table: "Airport", property: "Airport Key", header: "Station" }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1092, y: titleRow.y, z: 1002, w: 140, h: titleRow.h, table: "Date", property: "Year", header: "Year" }));

    // KPI strip -- 5 cards with reference-label context (vs-LY for D0/A14), YoY status accent
    const kc = cols(5, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Flight Leg", measureName: "D0 %", label: "D0 % · vs LY accent", accent: COLOR.steelBlue, statusConditional: { table: "Flight Leg", measure: "D0 vs Same Period LY Pts" } }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Flight Leg", measureName: "A14 %", label: "A14 % · vs LY accent", accent: COLOR.burntOrange, statusConditional: { table: "Flight Leg", measure: "A14 vs Same Period LY Pts" } }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Flight Leg", measureName: "CF %", label: "Completion Factor", accent: COLOR.forest }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Flight Leg", measureName: "Controllable CF %", label: "Controllable CF %", accent: COLOR.teal }));
    visuals.push(kpiCard({ name: visualId(), x: kc[4].x, y: kpiRow.y, z: 1014, w: kc[4].w, h: kpiRow.h, table: "Station Day Scorecard", measureName: "Avg D0 vs Expected (pp)", label: "Weather-Adjusted D0 Gap", accent: COLOR.gold }));

    // Hero: D0% / A14% trend.
    visuals.push(lineChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      title: "D0 % and A14 % by Month",
      table: "Date", category: "Year", categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [{ table: "Flight Leg", property: "D0 %", color: COLOR.steelBlue }, { table: "Flight Leg", property: "A14 %", color: COLOR.burntOrange }],
    }));

    // Supporting row (3-up)
    const sc = cols(3, CONTENT_W, MARGIN);
    visuals.push(pivotTable({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      title: "D0 / A14 / CF by AA Hub Station",
      rows: [{ table: "Airport", property: "Airport Key" }],
      values: [{ table: "Flight Leg", property: "D0 %" }, { table: "Flight Leg", property: "A14 %" }, { table: "Flight Leg", property: "CF %" }, { table: "Flight Leg", property: "Flight Count" }],
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
      dataBarOn: { table: "Flight Leg", property: "Flight Count" },
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      type: "barChart", title: "Hub Throughput by Station (pts)",
      table: "Airport", category: "Airport Key",
      yMeasures: [{ table: "Flight Leg", property: "Hub Throughput Pts" }],
      sort: { table: "Flight Leg", property: "Hub Throughput Pts", direction: "Descending" },
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
      gradient: { min: COLOR.steelBlueTint, max: COLOR.steelBlue },
    }));
    // Yearly grain, not monthly: this panel is ~600px wide, a third the
    // hero chart's width, which already shows D0%/A14% at monthly grain
    // above. 44 month labels crammed into a narrow panel collided with the
    // y-axis tick labels and rendered as an unreadable flat line pinned
    // under overlapping text. Year-level still answers "is the gap
    // closing over time" at a glance, and reads cleanly at this size.
    visuals.push(lineChart({
      name: visualId(), x: sc[2].x, y: suppRow.y, z: 1032, w: sc[2].w, h: suppRow.h,
      title: "Weather-Adjusted D0 Gap Trend (6 Hubs)",
      table: "Date", category: "Year",
      yMeasures: [{ table: "Station Day Scorecard", property: "Avg D0 vs Expected (pp)", color: COLOR.gold }],
      referenceLine: 0,
    }));

    // Detail: worst/best weather-adjusted station-days
    visuals.push(tableExVisual({
      name: visualId(), x: MARGIN, y: detailRow.y, z: 1040, w: CONTENT_W, h: detailRow.h,
      title: "Station-Days Furthest Below Weather/Volume-Expected D0 (Worst 8)",
      columns: [
        { table: "Station Day Scorecard", property: "Station" },
        { table: "Date", property: "Full Date" },
        { table: "Station Day Scorecard", property: "D0 Rate", measure: false },
        { table: "Station Day Scorecard", property: "Expected D0 Rate", measure: false },
        { table: "Station Day Scorecard", property: "D0 Vs Expected Pp", measure: false },
        { table: "Station Day Scorecard", property: "Max Severity Score", measure: false },
      ],
      sort: { table: "Station Day Scorecard", property: "D0 Vs Expected Pp", direction: "Ascending" },
      dataBarOn: { table: "Station Day Scorecard", property: "D0 Vs Expected Pp" },
      filters: [topNFilter("Station Day Scorecard", "D0 Vs Expected Pp", 8, 1)],
    }));

    return visuals;
  },
});

// ============================================================
// Page 2: Bank Performance (DFW)
// ============================================================
const page2Id = writePage({
  displayName: "Bank Performance",
  filters: [categoricalFilter("Airport", "Airport Key", "'DFW'")],
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 100 }, { h: 300 }, { h: 200 }, { h: 240 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1240, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Bank Performance Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1296, y: titleRow.y, z: 1001, w: 230, h: titleRow.h, table: "Bank", property: "Period", header: "Bank Chart Period" }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1550, y: titleRow.y, z: 1002, w: 170, h: titleRow.h, table: "Bank", property: "Direction", header: "Direction" }));

    const kc = cols(4, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Flight Leg", measureName: "D0 %", label: "DFW D0 %", accent: COLOR.steelBlue }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Flight Leg", measureName: "A14 %", label: "DFW A14 %", accent: COLOR.burntOrange }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Flight Leg", measureName: "Mean Taxi-Out Min", label: "DFW Mean Taxi-Out (min)", accent: COLOR.teal }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Flight Leg", measureName: "Flight Count", label: "DFW Scheduled Departures", accent: COLOR.slate }));

    visuals.push(barChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      type: "clusteredColumnChart",
      title: "DFW Bank Waves: Peak Avg Flights by Time, Pre vs Post Rebank",
      table: "Bank", category: "Peak Time", series: { table: "Bank", property: "Direction" },
      yMeasures: [{ table: "Bank", property: "Peak Avg Flights", agg: 1 }],
    }));

    // Two visuals instead of three: the D0/A14 pair is a real 2-series
    // comparison (kept as a chart), but Departures and Taxi-Out were each
    // a flat single-measure "2 bars" chart -- the same shape repeated with
    // only the color and measure swapped. Collapsed into one comparison
    // table instead of a 3rd near-identical bar chart.
    const sc = cols(2, CONTENT_W, MARGIN);
    visuals.push(barChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      type: "clusteredColumnChart", title: "D0 % / A14 % Pre vs Post Rebank",
      table: "Date", category: "Is Post Rebank",
      yMeasures: [{ table: "Flight Leg", property: "D0 %", color: COLOR.steelBlue }, { table: "Flight Leg", property: "A14 %", color: COLOR.burntOrange }],
    }));
    visuals.push(pivotTable({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      title: "Departures & Taxi-Out, Pre vs Post Rebank",
      rows: [{ table: "Date", property: "Is Post Rebank" }],
      values: [{ table: "Flight Leg", property: "Flight Count" }, { table: "Flight Leg", property: "Mean Taxi-Out Min" }],
      dataBarOn: { table: "Flight Leg", property: "Flight Count" },
    }));

    visuals.push(tableExVisual({
      name: visualId(), x: MARGIN, y: detailRow.y, z: 1040, w: CONTENT_W, h: detailRow.h,
      title: "DFW Bank Windows (Reconstructed from the Schedule)",
      columns: [
        { table: "Bank", property: "Period" }, { table: "Bank", property: "Direction" },
        { table: "Bank", property: "Bank Number" }, { table: "Bank", property: "Peak Time" },
        { table: "Bank", property: "Peak Avg Flights" }, { table: "Bank", property: "Window Start" },
        { table: "Bank", property: "Window End" },
      ],
      sort: { table: "Bank", property: "Direction", direction: "Ascending" },
      dataBarOn: { table: "Bank", property: "Peak Avg Flights" },
    }));

    return visuals;
  },
});

// ============================================================
// Page 3: Turn Performance (DFW)
// ============================================================
const page3Id = writePage({
  displayName: "Turn Performance",
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 100 }, { h: 300 }, { h: 200 }, { h: 224 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;
    const dfwTurnFilter = () => categoricalFilter("Airport", "Airport Key", "'DFW'");

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1240, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Turn Performance Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));

    // Headline finding: station-caused vs inbound-caused delay (README #3)
    const kc = cols(4, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Outbound D0 %", label: "Outbound D0 %, All DFW Turns", accent: COLOR.steelBlue, filters: [dfwTurnFilter()] }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Minutes Recovered per Turn", label: "Avg Minutes Recovered/Turn, DFW", accent: COLOR.teal, filters: [dfwTurnFilter()] }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Turn Count", label: "Paired Turns, DFW", accent: COLOR.slate, filters: [dfwTurnFilter()] }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Flight Leg", measureName: "Hub Throughput Pts", label: "DFW Hub Throughput (pts)", accent: COLOR.gold, filters: [dfwTurnFilter()] }));

    visuals.push(barChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      title: "Outbound D0 % by Fleet (On-Time-Inbound Turns, DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Outbound D0 %" }],
      sort: { table: "Aircraft Turn", property: "Outbound D0 %", direction: "Descending" },
      filters: [dfwTurnFilter(), topNFilter("Aircraft Turn", "Fleet Family", 10, 2, "Scheduled Turn Min", 2)],
      gradient: { min: COLOR.steelBlueTint, max: COLOR.steelBlue },
      invertAxis: false,
    }));

    // One dual-axis chart instead of three flat single-measure bar charts
    // (Minutes Recovered, Turn Volume, Scheduled Turn Length, all by the
    // same Fleet Family category -- the same shape repeated three times
    // with only the measure swapped). Volume (columns) and recovery
    // (line) share the fleet ranking and are the two genuinely paired
    // questions -- "which fleets fly the most turns, and do high-volume
    // fleets recover more or less time." Scheduled turn length moves to
    // the Fleet Turn Detail drillthrough page (right-click any fleet bar)
    // instead of sitting here as a third near-identical chart.
    visuals.push(comboChart({
      name: visualId(), x: MARGIN, y: suppRow.y, z: 1030, w: CONTENT_W, h: suppRow.h,
      title: "Turn Volume vs. Minutes Recovered by Fleet (Top 10 by Volume, DFW) -- Right-Click a Fleet to Drill Through",
      table: "Aircraft Turn", category: "Fleet Family",
      yColumns: [{ table: "Aircraft Turn", property: "Turn Count" }],
      y2Columns: [{ table: "Aircraft Turn", property: "Minutes Recovered per Turn" }],
      sort: { table: "Aircraft Turn", property: "Turn Count", direction: "Descending" },
      filters: [dfwTurnFilter(), topNFilter("Aircraft Turn", "Fleet Family", 10, 2, "Scheduled Turn Min", 2)],
    }));

    visuals.push(tableExVisual({
      name: visualId(), x: MARGIN, y: detailRow.y, z: 1040, w: CONTENT_W, h: detailRow.h,
      title: "Worst Routes Into DFW -- Detail (Origin Station)",
      columns: [
        { table: "Airport", property: "Airport Key" },
        { table: "Flight Leg", property: "A14 %", measure: true },
        { table: "Flight Leg", property: "D0 %", measure: true },
        { table: "Flight Leg", property: "Flight Count", measure: true },
      ],
      filters: [categoricalFilter("Airport (Dest)", "Airport Key", "'DFW'")],
      sort: { table: "Flight Leg", property: "A14 %", direction: "Ascending", measure: true },
      dataBarOn: { table: "Flight Leg", property: "Flight Count" },
    }));
    visuals.push(textbox({
      name: visualId(), x: MARGIN, y: detailRow.y + detailRow.h + 4, z: 1041, w: CONTENT_W, h: 36,
      text: "Scope: DFW only. Turn-length-cliff detail (the scheduled-turn-length minute where D0 falls off per fleet) is in docs/analysis/dfw_turn_cliff_by_fleet.png -- Direct Lake tables can't carry a calculated turn-length-bucket column (ADR 0014), so that cut isn't live here.",
    }));

    return visuals;
  },
});

// ============================================================
// Page 4: Delay Root Cause
// ============================================================
const page4Id = writePage({
  displayName: "Delay Root Cause",
  filters: [categoricalFilter("Carrier", "AA Family", "true")],
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 100 }, { h: 300 }, { h: 200 }, { h: 224 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1400, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Delay Root Cause Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1456, y: titleRow.y, z: 1001, w: 140, h: titleRow.h, table: "Date", property: "Year", header: "Year" }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1612, y: titleRow.y, z: 1002, w: 200, h: titleRow.h, table: "Airport", property: "Airport Key", header: "Station" }));

    const kc = cols(4, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Flight Leg", measureName: "Controllable Delay Share", label: "Controllable Delay Share", accent: COLOR.steelBlue }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Flight Leg", measureName: "Late Aircraft Delay Share", label: "Late-Aircraft Delay Share", accent: COLOR.plum }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Flight Leg", measureName: "Carrier Delay Minutes", label: "Carrier Delay Minutes", accent: COLOR.burntOrange }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Flight Leg", measureName: "Total Cause Delay Minutes", label: "Total Cause Delay Minutes", accent: COLOR.slate }));

    visuals.push(barChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      type: "columnChart", title: "Delay-Cause Minutes by Month (15+ min Arrival Delays Only)",
      table: "Date", category: "Year", categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [
        { table: "Flight Leg", property: "Carrier Delay Minutes" },
        { table: "Flight Leg", property: "Weather Delay Minutes" },
        { table: "Flight Leg", property: "NAS Delay Minutes" },
        { table: "Flight Leg", property: "Security Delay Minutes" },
        { table: "Flight Leg", property: "Late Aircraft Delay Minutes" },
      ],
    }));

    // Two visuals instead of three: the two by-station bar charts (Carrier
    // Delay Minutes, Controllable Delay Share) were the same chart type and
    // category with only the measure swapped. Paired into one dual-axis
    // combo instead -- it answers a real joint question ("do the stations
    // with the most carrier-caused minutes also have the highest
    // controllable share, or is their delay mostly weather/NAS-driven?")
    // that two separate bar charts left the reader to eyeball across panels.
    const sc = cols(2, CONTENT_W, MARGIN);
    visuals.push(lineChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      title: "Controllable & Late-Aircraft Share by Month",
      table: "Date", category: "Year", categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [{ table: "Flight Leg", property: "Controllable Delay Share", color: COLOR.steelBlue }, { table: "Flight Leg", property: "Late Aircraft Delay Share", color: COLOR.plum }],
    }));
    visuals.push(comboChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      title: "Carrier Delay Minutes vs. Controllable Delay Share by Station",
      table: "Airport", category: "Airport Key",
      yColumns: [{ table: "Flight Leg", property: "Carrier Delay Minutes" }],
      y2Columns: [{ table: "Flight Leg", property: "Controllable Delay Share" }],
      sort: { table: "Flight Leg", property: "Carrier Delay Minutes", direction: "Descending" },
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
    }));

    visuals.push(pivotTable({
      name: visualId(), x: MARGIN, y: detailRow.y, z: 1040, w: CONTENT_W, h: detailRow.h,
      title: "Delay-Cause Minutes by Station",
      rows: [{ table: "Airport", property: "Airport Key" }],
      values: [
        { table: "Flight Leg", property: "Carrier Delay Minutes" },
        { table: "Flight Leg", property: "Weather Delay Minutes" },
        { table: "Flight Leg", property: "NAS Delay Minutes" },
        { table: "Flight Leg", property: "Late Aircraft Delay Minutes" },
        { table: "Flight Leg", property: "Total Cause Delay Minutes" },
      ],
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
      dataBarOn: { table: "Flight Leg", property: "Total Cause Delay Minutes" },
    }));
    visuals.push(textbox({
      name: visualId(), x: MARGIN, y: detailRow.y + detailRow.h + 4, z: 1041, w: CONTENT_W, h: 36,
      text: "Cause minutes by hour and bank (from the original plan) aren't live here: Direct Lake tables can't carry a calculated hour-of-day column (ADR 0014), and dim_bank isn't joined to fact_flight_leg (a two-month prototype, not a continuous calendar). Shown by month and station instead.",
    }));

    return visuals;
  },
});

// ============================================================
// Page 5: Rebank Impact -- event-study diff-in-diff vs ORD control
// ============================================================
const page5Id = writePage({
  displayName: "Rebank Impact",
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 140 }, { h: 320 }, { h: 220 }]);
    const [titleRow, kpiRow, heroRow, detailRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1600, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Rebank Impact Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));

    // Effect-size cards: DFW vs ORD control, diff-in-diff, weather-adjusted
    // (signature analysis #2). Crimson = DFW got worse relative to control
    // post-rebank; slate = not statistically significant.
    const kc = cols(3, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Rebank Effect", measureName: "D0 Effect (pts)", label: "D0 Effect vs ORD Control, p<0.01", accent: COLOR.crimson }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Rebank Effect", measureName: "A14 Effect (pts)", label: "A14 Effect vs ORD Control, p<0.01", accent: COLOR.crimson }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Rebank Effect", measureName: "TXO Effect (min)", label: "Taxi-Out Effect vs ORD Control, n.s. (p=0.87)", accent: COLOR.slate }));

    // Hero: event-study coefficient by week relative to the rebank, with its
    // 95% CI traced as two companion lines and a reference line at 0 (no
    // effect). This is the diff-in-diff identification check: coefficients
    // should sit near 0 before week 0 (parallel trends) and move away after.
    visuals.push(lineChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      title: "Weekly D0 Effect vs ORD Control, Relative to Rebank Week (Coefficient and 95% CI)",
      table: "Rebank Event Study", category: "Week Relative",
      yMeasures: [
        { table: "Rebank Event Study", property: "Event Study Coef", color: COLOR.steelBlue },
        { table: "Rebank Event Study", property: "Event Study CI Low", color: COLOR.slate },
        { table: "Rebank Event Study", property: "Event Study CI High", color: COLOR.slate },
      ],
      referenceLine: 0,
    }));

    // Detail: full effect table (left) + pre-trend / methodology callout (right)
    const dc = cols(2, CONTENT_W, MARGIN);
    visuals.push(tableExVisual({
      name: visualId(), x: dc[0].x, y: detailRow.y, z: 1030, w: dc[0].w, h: detailRow.h,
      title: "Effect Sizes, DFW vs ORD Control (Diff-in-Diff)",
      columns: [
        { table: "Rebank Effect", property: "Metric" },
        { table: "Rebank Effect", property: "Estimate" },
        { table: "Rebank Effect", property: "Ci Low" },
        { table: "Rebank Effect", property: "Ci High" },
        { table: "Rebank Effect", property: "P Value" },
      ],
      sort: { table: "Rebank Effect", property: "Metric", direction: "Ascending" },
      dataBarOn: { table: "Rebank Effect", property: "Estimate" },
    }));
    visuals.push(textbox({
      name: visualId(), x: dc[1].x, y: detailRow.y, z: 1031, w: dc[1].w, h: detailRow.h,
      size: "11pt", italic: false,
      text: "Pre-trend check: weekly coefficients for weeks -7 through -1 sit close to 0 (parallel-trends holds), "
        + "with one marginal point at week -8 noted as a caveat rather than smoothed away. Post-rebank weeks move "
        + "further negative, consistent with the headline D0/A14 effect sizes at left -- DFW's reliability did not "
        + "improve relative to ORD after the April 2026 rebank, net of weather. Taxi-out time effect is directionally "
        + "favorable but not statistically significant. Method: two-way fixed-effects diff-in-diff, DFW vs ORD, "
        + "weather-adjusted (signature analysis #2, analysis/dfw_rebank_event_study.py).",
    }));

    return visuals;
  },
});

// ============================================================
// Page 6: Passenger Flow -- TSA / CBP hourly profiles at DFW
// ============================================================
const page6Id = writePage({
  displayName: "Passenger Flow",
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 80 }, { h: 140 }, { h: 300 }, { h: 280 }]);
    const [titleRow, kpiRow, heroRow, suppRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1600, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Passenger Flow Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN, y: titleRow.y + titleRow.h - 4, z: 999, w: 140 }));

    // KPI strip: real cumulative totals from the raw TSA/CBP fact tables
    // (not the small hourly-profile tables used for shape below).
    const dfwCheckpointFilter = () => categoricalFilter("Checkpoint Hour", "Station", "'DFW'");
    const kc = cols(4, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Checkpoint Hour", measureName: "TSA Passengers Screened", label: "DFW TSA Passengers Screened (All Hours on File)", accent: COLOR.steelBlue, filters: [dfwCheckpointFilter()] }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "CBP Wait Hour", measureName: "Avg CBP Wait Non-US (min)", label: "DFW CBP Avg Wait, Non-US (min)", accent: COLOR.burntOrange }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "CBP Wait Hour", measureName: "Max CBP Wait Non-US (min)", label: "DFW CBP Peak Wait, Non-US (min)", accent: COLOR.crimson }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "CBP Wait Hour", measureName: "CBP Flight Count (Sum)", label: "DFW International Arrivals Processed (CBP)", accent: COLOR.slate }));

    // Hero: TSA checkpoint throughput (columns, left axis) vs scheduled
    // departures (line, right axis) by local hour -- does checkpoint
    // staffing track the schedule's own departure wave shape?
    visuals.push(comboChart({
      name: visualId(), x: MARGIN, y: heroRow.y, z: 1020, w: CONTENT_W, h: heroRow.h,
      title: "TSA Checkpoint Throughput vs. Scheduled Departures by Local Hour",
      table: "TSA Departure Hour Profile", category: "Local Hour",
      yColumns: [{ table: "TSA Departure Hour Profile", property: "Mean Passengers Screened", agg: 1 }],
      y2Columns: [{ table: "TSA Departure Hour Profile", property: "Mean Scheduled Departures", agg: 1 }],
    }));

    // Supporting row (2-up): CBP wait vs CBP's own volume; cross-check of
    // the BTS-proxy international-arrivals estimate against CBP's actual count.
    const sc = cols(2, CONTENT_W, MARGIN);
    visuals.push(comboChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      title: "CBP Wait Time (US vs Non-US) vs CBP Passenger Volume by Local Hour",
      table: "CBP Hour Profile", category: "Local Hour",
      yColumns: [{ table: "CBP Hour Profile", property: "Mean Cbp Total Passengers", agg: 1 }],
      y2Columns: [
        { table: "CBP Hour Profile", property: "Mean Avg Wait Us Min", agg: 1 },
        { table: "CBP Hour Profile", property: "Mean Avg Wait Non Us Min", agg: 1 },
      ],
    }));
    visuals.push(comboChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      title: "Cross-Check: BTS-Proxy International Arrivals vs. CBP Passenger Volume by Hour",
      table: "CBP Hour Profile", category: "Local Hour",
      yColumns: [{ table: "CBP Hour Profile", property: "Mean Bts Proxy Intl Arrivals", agg: 1 }],
      y2Columns: [{ table: "CBP Hour Profile", property: "Mean Cbp Total Passengers", agg: 1 }],
    }));

    visuals.push(textbox({
      name: visualId(), x: MARGIN, y: suppRow.y + suppRow.h + 4, z: 1041, w: CONTENT_W, h: 36,
      text: "Scope: hourly profiles are means across the analysis window in analysis/dfw_passenger_flow.py (signature "
        + "analysis #6). Checkpoint Hour is TSA's full national checkpoint feed, filtered to DFW above; CBP Wait Hour "
        + "is DFW-only by data construction. BTS-proxy international arrivals are a schedule-based estimate (BTS T-100 "
        + "segment is out of scope -- ADR 0012), not a direct CBP measurement, so the cross-check at right is a shape "
        + "comparison, not a reconciliation to the decimal.",
    }));

    return visuals;
  },
});

// ============================================================
// Page 7: Fleet Turn Detail -- drillthrough target from Turn Performance's
// fleet charts (right-click a fleet bar -> Drill Through). Hidden from the
// page-nav tab strip since it only makes sense with a fleet already
// selected; carries the scheduled-turn-length metric that would otherwise
// be a fourth near-identical "by Fleet Family" bar chart on Page 3.
// ============================================================
const fleetDrillFilter = drillthroughFilter("Aircraft Turn", "Fleet Family");
const page7Id = writePage({
  displayName: "Fleet Turn Detail",
  pageType: "Drillthrough",
  visibility: "HiddenInViewMode",
  filters: [categoricalFilter("Airport", "Airport Key", "'DFW'"), fleetDrillFilter],
  pageBinding: {
    name: "Pod",
    type: "Drillthrough",
    parameters: [{
      name: `Param_${fleetDrillFilter.name}`,
      boundFilter: fleetDrillFilter.name,
      fieldExpr: col("Aircraft Turn", "Fleet Family"),
    }],
  },
  build: () => {
    const visuals = [];
    const rows = layout(MARGIN, [{ h: 100 }, { h: 160 }]);
    const [titleRow, kpiRow] = rows;

    visuals.push(backButton({ name: visualId(), x: MARGIN, y: titleRow.y + 16, z: 999 }));
    visuals.push(visualContainer({
      name: visualId(), x: MARGIN + 64, y: titleRow.y, z: 1000, w: CONTENT_W - 64, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "22pt", color: COLOR.steelBlue },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Aircraft Turn", "Fleet Detail Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(accentRule({ name: visualId(), x: MARGIN + 64, y: titleRow.y + titleRow.h - 4, z: 998, w: 140 }));

    const kc = cols(4, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Outbound D0 %", label: "Outbound D0 %, DFW, This Fleet", accent: COLOR.steelBlue }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Minutes Recovered per Turn", label: "Avg Minutes Recovered/Turn", accent: COLOR.teal }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Turn Count", label: "Paired Turns, DFW", accent: COLOR.slate }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Aircraft Turn", measureName: "Avg Scheduled Turn Min", label: "Avg Scheduled Turn Length (min)", accent: COLOR.gold }));

    visuals.push(textbox({
      name: visualId(), x: MARGIN, y: kpiRow.y + kpiRow.h + 4, z: 1020, w: CONTENT_W, h: 36,
      text: "Drillthrough target: right-click a fleet bar on Turn Performance and choose \"Drill through\" to land here filtered to that fleet. Use the back button (top-left) to return.",
    }));

    return visuals;
  },
});

// ---- pages.json ----
const allPageIds = [page1Id, page2Id, page3Id, page4Id, page5Id, page6Id, page7Id];
const pagesJsonPath = path.join(PAGES_DIR, "pages.json");
const pagesJson = JSON.parse(fs.readFileSync(pagesJsonPath, "utf8"));
pagesJson.pageOrder = allPageIds;
pagesJson.activePageName = allPageIds[0];
fs.writeFileSync(pagesJsonPath, JSON.stringify(pagesJson, null, 2));

console.log(JSON.stringify({ page1Id, page2Id, page3Id, page4Id, page5Id, page6Id, page7Id }, null, 2));
