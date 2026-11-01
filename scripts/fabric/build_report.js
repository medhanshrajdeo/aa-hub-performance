// Generates rpt_hubops's 4 report pages (PBIR) on top of sm_hubops, per the
// "Industrial Dense / Analyst Workbench" design identity (see
// fabric/rpt_hubops.Report/StaticResources/RegisteredResources/HubOpsWorkbench-*.json):
// off-white canvas, steel-blue/burnt-orange accent pair, tabular-numeral KPI
// cards with YoY-status accent bars, FillRule-gradient single-measure bars,
// field-parameter-driven metric pickers, and dynamic (measure-bound) titles.
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

// ---- design tokens (mirrors HubOpsWorkbench-a1f3.json dataColors) ----
const COLOR = {
  steelBlue: "#3B6E91",
  steelBlueTint: "#D7E6EE",
  burntOrange: "#C8742A",
  burntOrangeTint: "#F3DFC9",
  forest: "#1C5E47",
  forestTint: "#CFE3DB",
  crimson: "#B23A48",
  crimsonTint: "#F0D4D7",
  slate: "#64748B",
  slateTint: "#DEE3E9",
  gold: "#C9A227",
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

function staticTitleVCO(text, size = "14D") {
  return { title: [{ properties: {
    show: litExpr("true"),
    text: litExpr(`'${text}'`),
    fontSize: litExpr(size),
    alignment: litExpr("'left'"),
  } }] };
}

// Dynamic title bound to a DAX measure instead of a literal string.
function dynamicTitleVCO(table, measureName, size = "14D") {
  return { title: [{ properties: {
    show: litExpr("true"),
    text: { expr: meas(table, measureName) },
    fontSize: litExpr(size),
    alignment: litExpr("'left'"),
  } }] };
}

// ============================================================
// Cards
// ============================================================
// measure: { table, name, label, accent, accentTint, referenceMeasure? (table,name) }
function kpiCard({ name, x, y, z, w, h, table, measureName, label, accent, statusConditional, filters }) {
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
        value: [{ properties: { fontSize: litExpr("24D"), fontColor: solidLit(accent) }, selector: { id: "default" } }],
        label: [{ properties: { text: litExpr(`'${label}'`), fontSize: litExpr("10D"), fontColor: solidLit(COLOR.muted) }, selector: { id: "default" } }],
      },
      visualContainerObjects: {
        background: [{ properties: { show: litExpr("true"), color: solidLit("#FFFFFF"), transparency: litExpr("0D") } }],
        border: [{ properties: { show: litExpr("true"), color: solidLit(COLOR.hairline), radius: litExpr("8D") } }],
        padding: [{ properties: { top: litExpr("0D"), bottom: litExpr("0D"), left: litExpr("0D"), right: litExpr("0D") } }],
        visualHeader: [{ properties: { show: litExpr("false") } }],
      },
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

function barChart({ name, x, y, z, w, h, type = "clusteredColumnChart", title, dynamicTitle, table, category, categoryExtra = [], series, yMeasures, sort, filters, gradient, invertAxis }) {
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
      visualContainerObjects: dynamicTitle ? dynamicTitleVCO(dynamicTitle.table, dynamicTitle.measure) : (title ? staticTitleVCO(title) : undefined),
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

// ============================================================
// Line chart, optionally bound to a field-parameter "Fields" column
// ============================================================
function lineChart({ name, x, y, z, w, h, title, dynamicTitle, table, category, categoryExtra = [], yMeasures, yFieldParam, referenceLine, filters }) {
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
      visualContainerObjects: dynamicTitle ? dynamicTitleVCO(dynamicTitle.table, dynamicTitle.measure) : (title ? staticTitleVCO(title) : undefined),
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

function tileSlicer({ name, x, y, z, w, h = 80, table, property, header }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "advancedSlicerVisual",
      query: { queryState: { Values: { projections: [colProj(table, property)] } } },
      objects: {
        layout: [{ properties: { style: litExpr("'Cards'"), rowCount: litExpr("1L") } }],
      },
      visualContainerObjects: {
        title: [{ properties: { show: litExpr("true"), text: litExpr(`'${header}'`), fontSize: litExpr("10D") } }],
        padding: [{ properties: { top: litExpr("8D"), bottom: litExpr("8D"), left: litExpr("8D"), right: litExpr("8D") } }],
      },
    },
  });
}

function textbox({ name, x, y, z, w, h, text, size = "9pt", italic = true, color = COLOR.muted }) {
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

function writePage({ displayName, filters, build }) {
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
    const rows = layout(MARGIN, [{ h: 80 }, { h: 120 }, { h: 280 }, { h: 200 }, { h: 240 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    // Title band: dynamic title textbox + 3 slicers
    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 820, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.ink },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Hub Scorecard Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 876, y: titleRow.y, z: 1001, w: 200, h: titleRow.h, table: "Airport", property: "Airport Key", header: "Station" }));
    visuals.push(dropdownSlicer({ name: visualId(), x: 1092, y: titleRow.y, z: 1002, w: 140, h: titleRow.h, table: "Date", property: "Year", header: "Year" }));
    visuals.push(tileSlicer({ name: visualId(), x: 1248, y: titleRow.y, z: 1003, w: 608, h: titleRow.h, table: "Hub KPI", property: "Hub KPI", header: "Hero Chart Metric" }));

    // KPI strip -- 5 cards with reference-label context (vs-LY for D0/A14), YoY status accent
    const kc = cols(5, CONTENT_W, MARGIN);
    visuals.push(kpiCard({ name: visualId(), x: kc[0].x, y: kpiRow.y, z: 1010, w: kc[0].w, h: kpiRow.h, table: "Flight Leg", measureName: "D0 %", label: "D0 % · vs LY accent", accent: COLOR.steelBlue, statusConditional: { table: "Flight Leg", measure: "D0 vs Same Period LY Pts" } }));
    visuals.push(kpiCard({ name: visualId(), x: kc[1].x, y: kpiRow.y, z: 1011, w: kc[1].w, h: kpiRow.h, table: "Flight Leg", measureName: "A14 %", label: "A14 % · vs LY accent", accent: COLOR.burntOrange, statusConditional: { table: "Flight Leg", measure: "A14 vs Same Period LY Pts" } }));
    visuals.push(kpiCard({ name: visualId(), x: kc[2].x, y: kpiRow.y, z: 1012, w: kc[2].w, h: kpiRow.h, table: "Flight Leg", measureName: "CF %", label: "Completion Factor", accent: COLOR.forest }));
    visuals.push(kpiCard({ name: visualId(), x: kc[3].x, y: kpiRow.y, z: 1013, w: kc[3].w, h: kpiRow.h, table: "Flight Leg", measureName: "Controllable CF %", label: "Controllable CF %", accent: COLOR.teal }));
    visuals.push(kpiCard({ name: visualId(), x: kc[4].x, y: kpiRow.y, z: 1014, w: kc[4].w, h: kpiRow.h, table: "Station Day Scorecard", measureName: "Avg D0 vs Expected (pp)", label: "Weather-Adjusted D0 Gap", accent: COLOR.gold }));

    // Hero: D0% / A14% trend. (The "Hero Chart Metric" tile slicer above is
    // the Hub KPI field parameter exposed for analyst self-service in
    // Desktop -- drag it onto any visual's axis to swap the plotted metric.
    // A pre-wired dynamic axis swap needs Desktop's own internal field-
    // parameter query encoding, which isn't in the public PBIR schema --
    // confirmed by testing: a plain Column reference in a Measure-kind role
    // fails both validate and live render with InvalidUnconstrainedJoin.)
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
    visuals.push(lineChart({
      name: visualId(), x: sc[2].x, y: suppRow.y, z: 1032, w: sc[2].w, h: suppRow.h,
      title: "Weather-Adjusted D0 Gap Trend (6 Hubs)",
      table: "Date", category: "Year", categoryExtra: [{ table: "Date", property: "Month Name" }],
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
    const rows = layout(MARGIN, [{ h: 80 }, { h: 120 }, { h: 280 }, { h: 200 }, { h: 240 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1240, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.ink },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Bank Performance Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
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

    const sc = cols(3, CONTENT_W, MARGIN);
    visuals.push(barChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      type: "clusteredColumnChart", title: "D0 % / A14 % Pre vs Post Rebank",
      table: "Date", category: "Is Post Rebank",
      yMeasures: [{ table: "Flight Leg", property: "D0 %", color: COLOR.steelBlue }, { table: "Flight Leg", property: "A14 %", color: COLOR.burntOrange }],
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      type: "clusteredColumnChart", title: "Scheduled Departures Pre vs Post Rebank",
      table: "Date", category: "Is Post Rebank",
      yMeasures: [{ table: "Flight Leg", property: "Flight Count" }],
      gradient: { min: COLOR.slateTint, max: COLOR.slate },
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[2].x, y: suppRow.y, z: 1032, w: sc[2].w, h: suppRow.h,
      type: "clusteredColumnChart", title: "Mean Taxi-Out Min Pre vs Post Rebank",
      table: "Date", category: "Is Post Rebank",
      yMeasures: [{ table: "Flight Leg", property: "Mean Taxi-Out Min" }],
      gradient: { min: COLOR.tealTint, max: COLOR.teal },
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
    const rows = layout(MARGIN, [{ h: 80 }, { h: 120 }, { h: 280 }, { h: 200 }, { h: 196 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;
    const dfwTurnFilter = () => categoricalFilter("Airport", "Airport Key", "'DFW'");

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1240, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.ink },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Turn Performance Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
    visuals.push(tileSlicer({ name: visualId(), x: 1296, y: titleRow.y, z: 1001, w: 424, h: titleRow.h, table: "Turn Metric", property: "Turn Metric", header: "Chart Metric" }));

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

    const sc = cols(3, CONTENT_W, MARGIN);
    visuals.push(barChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      title: "Avg Minutes Recovered/Turn by Fleet (Top 10 by Volume, DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Minutes Recovered per Turn" }],
      sort: { table: "Aircraft Turn", property: "Minutes Recovered per Turn", direction: "Ascending" },
      filters: [dfwTurnFilter(), topNFilter("Aircraft Turn", "Fleet Family", 10, 2, "Scheduled Turn Min", 2)],
      gradient: { min: COLOR.tealTint, max: COLOR.teal },
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      title: "Turn Volume by Fleet (Top 10, DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Turn Count" }],
      sort: { table: "Aircraft Turn", property: "Turn Count", direction: "Descending" },
      filters: [dfwTurnFilter(), topNFilter("Aircraft Turn", "Fleet Family", 10, 2, "Scheduled Turn Min", 2)],
      gradient: { min: COLOR.slateTint, max: COLOR.slate },
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[2].x, y: suppRow.y, z: 1032, w: sc[2].w, h: suppRow.h,
      title: "Avg Scheduled Turn Length by Fleet (Top 10 by Volume, min, DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Scheduled Turn Min", agg: 1 }],
      sort: { table: "Aircraft Turn", property: "Scheduled Turn Min", direction: "Ascending", agg: 1 },
      filters: [dfwTurnFilter(), topNFilter("Aircraft Turn", "Fleet Family", 10, 2, "Scheduled Turn Min", 2)],
      gradient: { min: COLOR.crimsonTint, max: COLOR.crimson },
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
    const rows = layout(MARGIN, [{ h: 80 }, { h: 120 }, { h: 280 }, { h: 200 }, { h: 196 }]);
    const [titleRow, kpiRow, heroRow, suppRow, detailRow] = rows;

    visuals.push(visualContainer({
      name: visualId(), x: MARGIN, y: titleRow.y, z: 1000, w: 1400, h: titleRow.h,
      visual: {
        visualType: "textbox",
        objects: {
          general: [{ properties: { paragraphs: [{ textRuns: [{
            value: { propertyIdentifier: { objectName: "values", propertyName: "expr" }, selector: { id: "pageTitle" } },
            textStyle: { fontFamily: "Segoe UI Semibold", fontSize: "20pt", color: COLOR.ink },
          }] }] } }],
          values: [{ properties: { expr: { expr: meas("Flight Leg", "Delay Root Cause Title") } }, selector: { id: "pageTitle" } }],
        },
      },
    }));
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

    const sc = cols(3, CONTENT_W, MARGIN);
    visuals.push(lineChart({
      name: visualId(), x: sc[0].x, y: suppRow.y, z: 1030, w: sc[0].w, h: suppRow.h,
      title: "Controllable & Late-Aircraft Share by Month",
      table: "Date", category: "Year", categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [{ table: "Flight Leg", property: "Controllable Delay Share", color: COLOR.steelBlue }, { table: "Flight Leg", property: "Late Aircraft Delay Share", color: COLOR.plum }],
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[1].x, y: suppRow.y, z: 1031, w: sc[1].w, h: suppRow.h,
      type: "barChart", title: "Carrier Delay Minutes by Station",
      table: "Airport", category: "Airport Key",
      yMeasures: [{ table: "Flight Leg", property: "Carrier Delay Minutes" }],
      sort: { table: "Flight Leg", property: "Carrier Delay Minutes", direction: "Descending" },
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
      gradient: { min: COLOR.burntOrangeTint, max: COLOR.burntOrange },
    }));
    visuals.push(barChart({
      name: visualId(), x: sc[2].x, y: suppRow.y, z: 1032, w: sc[2].w, h: suppRow.h,
      type: "barChart", title: "Controllable Delay Share by Station",
      table: "Airport", category: "Airport Key",
      yMeasures: [{ table: "Flight Leg", property: "Controllable Delay Share" }],
      sort: { table: "Flight Leg", property: "Controllable Delay Share", direction: "Descending" },
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
      gradient: { min: COLOR.steelBlueTint, max: COLOR.steelBlue },
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

// ---- pages.json ----
const allPageIds = [page1Id, page2Id, page3Id, page4Id];
const pagesJsonPath = path.join(PAGES_DIR, "pages.json");
const pagesJson = JSON.parse(fs.readFileSync(pagesJsonPath, "utf8"));
pagesJson.pageOrder = allPageIds;
pagesJson.activePageName = allPageIds[0];
fs.writeFileSync(pagesJsonPath, JSON.stringify(pagesJson, null, 2));

console.log(JSON.stringify({ page1Id, page2Id, page3Id, page4Id }, null, 2));
