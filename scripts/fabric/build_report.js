// Generates rpt_hubops's 4 report pages (PBIR) on top of sm_hubops.
// Deterministic generator per the powerbi-report-authoring skill's own
// recommendation for full-report builds (avoids hand-editing dozens of
// visual.json files). Run with: node scripts/fabric/build_report.js
//
// Page content follows planning/project-plan.md's "Power BI report" table
// exactly, with two documented simplifications where the semantic model
// doesn't yet support the literal ask (see ADR 0014 -- Direct Lake can't
// carry calculated columns, so there's no by-hour attribute on Flight Leg):
//   - Page 2 "D0 and station-originated delay by bank" -> bank *volume*
//     waves (what dim_bank actually has) + a DFW pre/post-rebank D0/A14
//     comparison, not delay-by-bank (no bank join exists on fact_flight_leg).
//   - Page 4 "cause minutes by hour and bank" -> cause minutes/shares by
//     month (Date is wired up; hour and bank are not).

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

function lit(value) {
  return { Literal: { Value: value } };
}

// ---- visual container helpers ----
function visualContainer({ name, x, y, z, w, h, visual }) {
  return {
    $schema: SCHEMA_VISUAL,
    name,
    position: { x, y, z, height: h, width: w, tabOrder: z },
    visual,
  };
}

function titleVCO(text) {
  return {
    title: [{
      properties: {
        show: { expr: { Literal: { Value: "true" } } },
        text: { expr: { Literal: { Value: `'${text}'` } } },
        fontSize: { expr: { Literal: { Value: "11D" } } },
        alignment: { expr: { Literal: { Value: "'left'" } } },
      },
    }],
  };
}

function cardVisual({ name, x, y, z, w, h, table, measures, label }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "cardVisual",
      query: { queryState: { Data: { projections: measures.map((m) => measProj(table, m)) } } },
      objects: {
        outline: [{ properties: { show: { expr: { Literal: { Value: "false" } } } }, selector: { id: "default" } }],
      },
      visualContainerObjects: label ? titleVCO(label) : undefined,
    },
  });
}

const AGG_NAME = { 0: "Sum", 1: "Average", 2: "Count", 3: "Min", 4: "Max", 5: "CountNonNull", 6: "Median", 7: "StandardDeviation", 8: "Variance" };

function barChart({ name, x, y, z, w, h, type = "clusteredColumnChart", title, table, category, categoryExtra = [], series, yMeasures, sort, filters }) {
  const categoryProjections = [colProj(table, category, { active: true }), ...categoryExtra.map((c) => colProj(c.table, c.property, { active: true }))];
  const queryState = {
    Category: { projections: categoryProjections },
    Y: { projections: yMeasures.map((y) => (y.agg !== undefined ? {
      field: { Aggregation: { Expression: { Column: { Expression: { SourceRef: { Entity: y.table } }, Property: y.property } }, Function: y.agg } },
      queryRef: `${AGG_NAME[y.agg]}(${y.table}.${y.property})`,
      nativeQueryRef: `${AGG_NAME[y.agg]} of ${y.property}`,
    } : measProj(y.table, y.property))) },
  };
  if (series) queryState.Series = { projections: [colProj(series.table, series.property)] };
  const query = { queryState };
  if (sort) {
    query.sortDefinition = { sort: [{ field: meas(sort.table, sort.property), direction: sort.direction }], isDefaultSort: true };
  }
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: type,
      query,
      objects: {
        legend: yMeasures.length > 1 || series ? [{ properties: { position: { expr: { Literal: { Value: "'Top'" } } } } }] : undefined,
      },
      visualContainerObjects: title ? titleVCO(title) : undefined,
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

function lineChart({ name, x, y, z, w, h, title, table, category, categoryExtra = [], yMeasures }) {
  const categoryProjections = [colProj(table, category, { active: true }), ...categoryExtra.map((c) => colProj(c.table, c.property, { active: true }))];
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "lineChart",
      query: { queryState: {
        Category: { projections: categoryProjections },
        Y: { projections: yMeasures.map((y) => measProj(y.table, y.property)) },
      } },
      objects: {
        legend: [{ properties: { position: { expr: { Literal: { Value: "'Top'" } } } } }],
      },
      visualContainerObjects: title ? titleVCO(title) : undefined,
    },
  });
}

function pivotTable({ name, x, y, z, w, h, title, rows, values, filters }) {
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "pivotTable",
      query: { queryState: {
        Rows: { projections: rows.map((r) => colProj(r.table, r.property)) },
        Values: { projections: values.map((v) => measProj(v.table, v.property)) },
      } },
      objects: {
        columnHeaders: [{ properties: {
          columnAdjustment: { expr: { Literal: { Value: "'growToFit'" } } },
          autoSizeColumnWidth: { expr: { Literal: { Value: "true" } } },
        } }],
      },
      visualContainerObjects: title ? titleVCO(title) : undefined,
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

function tableExVisual({ name, x, y, z, w, h, title, table, columns, filters, sort }) {
  const query = { queryState: { Values: { projections: columns.map((c) => (c.measure ? measProj(c.table, c.property) : colProj(c.table, c.property))) } } };
  if (sort) query.sortDefinition = { sort: [{ field: sort.measure ? meas(sort.table, sort.property) : col(sort.table, sort.property), direction: sort.direction }], isDefaultSort: true };
  const vc = visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "tableEx",
      query,
      objects: {
        columnHeaders: [{ properties: {
          columnAdjustment: { expr: { Literal: { Value: "'growToFit'" } } },
          autoSizeColumnWidth: { expr: { Literal: { Value: "true" } } },
        } }],
      },
      visualContainerObjects: title ? titleVCO(title) : undefined,
    },
  });
  if (filters) vc.filterConfig = { filters };
  return vc;
}

function dropdownSlicer({ name, x, y, z, w = 180, h = 80, table, property, header }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "slicer",
      query: { queryState: { Values: { projections: [colProj(table, property)] } } },
      objects: {
        data: [{ properties: { mode: { expr: { Literal: { Value: "'Dropdown'" } } } } }],
        header: [{ properties: {
          show: { expr: { Literal: { Value: "true" } } },
          text: { expr: { Literal: { Value: `'${header}'` } } },
        } }],
      },
      visualContainerObjects: {
        padding: [{ properties: {
          top: { expr: { Literal: { Value: "8D" } } },
          bottom: { expr: { Literal: { Value: "8D" } } },
          left: { expr: { Literal: { Value: "8D" } } },
          right: { expr: { Literal: { Value: "8D" } } },
        } }],
      },
    },
  });
}

function textbox({ name, x, y, z, w, h, text }) {
  return visualContainer({
    name, x, y, z, w, h,
    visual: {
      visualType: "textbox",
      objects: {
        general: [{ properties: {
          paragraphs: [{
            textRuns: [{ value: text, textStyle: { fontSize: "10px", color: "#605E5C", fontStyle: "italic" } }],
          }],
        } }],
      },
    },
  });
}

// Visual-level categorical filter: "<table>[<property>] = <literalValue>" (literalValue pre-quoted)
function categoricalFilter(table, property, literalValue) {
  return {
    name: filterId(),
    field: col(table, property),
    type: "Categorical",
    filter: {
      Version: 2,
      From: [{ Name: "t", Entity: table, Type: 0 }],
      Where: [{
        Condition: {
          In: {
            Expressions: [{ Column: { Expression: { SourceRef: { Source: "t" } }, Property: property } }],
            Values: [[{ Literal: { Value: literalValue } }]],
          },
        },
      }],
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
    height: 720,
    width: 1280,
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
  build: () => [
    dropdownSlicer({ name: visualId(), x: 1080, y: 20, z: 1000, table: "Airport", property: "Airport Key", header: "Station" }),
    cardVisual({
      name: visualId(), x: 20, y: 20, z: 1001, w: 1040, h: 110,
      table: "Flight Leg",
      measures: ["D0 %", "A14 %", "CF %", "Controllable CF %", "Mean Taxi-Out Min", "Hub Throughput Pts"],
    }),
    cardVisual({
      name: visualId(), x: 20, y: 150, z: 1002, w: 250, h: 100,
      table: "Station Day Scorecard", measures: ["Avg D0 vs Expected (pp)"],
      label: "Weather-Adjusted D0 Gap",
    }),
    pivotTable({
      name: visualId(), x: 290, y: 150, z: 1003, w: 970, h: 220,
      title: "D0 / A14 / CF by AA Hub Station",
      rows: [{ table: "Airport", property: "Airport Key" }],
      values: [
        { table: "Flight Leg", property: "D0 %" },
        { table: "Flight Leg", property: "A14 %" },
        { table: "Flight Leg", property: "CF %" },
        { table: "Flight Leg", property: "Flight Count" },
      ],
      filters: [categoricalFilter("Airport", "AA Hub", "true")],
    }),
    lineChart({
      name: visualId(), x: 20, y: 390, z: 1004, w: 1240, h: 310,
      title: "D0 % and A14 % by Month",
      table: "Date", category: "Year",
      categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [{ table: "Flight Leg", property: "D0 %" }, { table: "Flight Leg", property: "A14 %" }],
    }),
  ],
});

// ============================================================
// Page 2: Bank Performance (DFW)
// ============================================================
const page2Id = writePage({
  displayName: "Bank Performance",
  filters: [categoricalFilter("Airport", "Airport Key", "'DFW'")],
  build: () => [
    dropdownSlicer({ name: visualId(), x: 20, y: 20, z: 1000, table: "Bank", property: "Period", header: "Period (Bank Chart)" }),
    barChart({
      name: visualId(), x: 220, y: 20, z: 1001, w: 1040, h: 300,
      title: "DFW Bank Waves: Peak Avg Flights by Time, Pre vs Post Rebank",
      table: "Bank", category: "Peak Time", series: { table: "Bank", property: "Direction" },
      yMeasures: [{ table: "Bank", property: "Peak Avg Flights", agg: 1 }],
    }),
    barChart({
      name: visualId(), x: 20, y: 340, z: 1002, w: 600, h: 300,
      type: "clusteredColumnChart",
      title: "DFW D0 % / A14 % Pre vs Post Rebank",
      table: "Date", category: "Is Post Rebank",
      yMeasures: [{ table: "Flight Leg", property: "D0 %" }, { table: "Flight Leg", property: "A14 %" }],
    }),
    tableExVisual({
      name: visualId(), x: 640, y: 340, z: 1003, w: 620, h: 300,
      title: "DFW Bank Windows (Reconstructed from the Schedule)",
      table: "Bank",
      columns: [
        { table: "Bank", property: "Period" },
        { table: "Bank", property: "Direction" },
        { table: "Bank", property: "Bank Number" },
        { table: "Bank", property: "Peak Time" },
        { table: "Bank", property: "Peak Avg Flights" },
        { table: "Bank", property: "Window Start" },
        { table: "Bank", property: "Window End" },
      ],
      sort: { table: "Bank", property: "Direction", direction: "Ascending" },
    }),
  ],
});

// ============================================================
// Page 3: Turn Performance (DFW)
// ============================================================
const page3Id = writePage({
  displayName: "Turn Performance",
  build: () => [
    barChart({
      name: visualId(), x: 20, y: 20, z: 1000, w: 610, h: 320,
      title: "Outbound D0 % by Fleet (On-Time-Inbound Turns, DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Outbound D0 %" }],
      sort: { table: "Aircraft Turn", property: "Outbound D0 %", direction: "Descending" },
      filters: [categoricalFilter("Airport", "Airport Key", "'DFW'")],
    }),
    barChart({
      name: visualId(), x: 650, y: 20, z: 1001, w: 610, h: 320,
      title: "Avg Minutes Recovered per Turn by Fleet (DFW)",
      table: "Aircraft Turn", category: "Fleet Family",
      yMeasures: [{ table: "Aircraft Turn", property: "Minutes Recovered per Turn" }],
      sort: { table: "Aircraft Turn", property: "Minutes Recovered per Turn", direction: "Ascending" },
      filters: [categoricalFilter("Airport", "Airport Key", "'DFW'")],
    }),
    tableExVisual({
      name: visualId(), x: 20, y: 360, z: 1002, w: 1240, h: 260,
      title: "Worst Routes Into DFW by A14 % (Origin Station)",
      table: "Flight Leg",
      columns: [
        { table: "Airport", property: "Airport Key" },
        { table: "Flight Leg", property: "A14 %", measure: true },
        { table: "Flight Leg", property: "D0 %", measure: true },
        { table: "Flight Leg", property: "Flight Count", measure: true },
      ],
      // Dest-only filter, deliberately NOT combined with an Origin=DFW
      // filter -- Flight Leg's Origin and Dest relationships to Airport
      // are two different tables (Airport / Airport (Dest)) specifically
      // so a "DFW as destination, any origin" query like this one doesn't
      // collide with a DFW-as-origin restriction (same class of conflict
      // ADR 0014 documents for Hub Throughput Pts under RLS).
      filters: [categoricalFilter("Airport (Dest)", "Airport Key", "'DFW'")],
      sort: { table: "Flight Leg", property: "A14 %", direction: "Ascending", measure: true },
    }),
    textbox({
      name: visualId(), x: 20, y: 630, z: 1003, w: 1240, h: 60,
      text: "Scope: DFW only. Turn-length-cliff detail (the exact scheduled-turn-length minute where D0 falls off per fleet) is in docs/analysis/dfw_turn_cliff_by_fleet.png -- Direct Lake tables can't carry a calculated turn-length-bucket column (see ADR 0014), so that cut isn't live here yet.",
    }),
  ],
});

// ============================================================
// Page 4: Delay Root Cause
// ============================================================
const aaFamilyFilter = categoricalFilter("Carrier", "AA Family", "true");
const page4Id = writePage({
  displayName: "Delay Root Cause",
  filters: [aaFamilyFilter],
  build: () => [
    barChart({
      name: visualId(), x: 20, y: 20, z: 1000, w: 1240, h: 320,
      type: "columnChart",
      title: "Delay-Cause Minutes by Month (AA Family, 15+ min Arrival Delays Only)",
      table: "Date", category: "Year",
      categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [
        { table: "Flight Leg", property: "Carrier Delay Minutes" },
        { table: "Flight Leg", property: "Weather Delay Minutes" },
        { table: "Flight Leg", property: "NAS Delay Minutes" },
        { table: "Flight Leg", property: "Security Delay Minutes" },
        { table: "Flight Leg", property: "Late Aircraft Delay Minutes" },
      ],
    }),
    lineChart({
      name: visualId(), x: 20, y: 360, z: 1001, w: 1240, h: 260,
      title: "Controllable and Late-Aircraft Delay Share by Month (AA Family)",
      table: "Date", category: "Year",
      categoryExtra: [{ table: "Date", property: "Month Name" }],
      yMeasures: [
        { table: "Flight Leg", property: "Controllable Delay Share" },
        { table: "Flight Leg", property: "Late Aircraft Delay Share" },
      ],
    }),
    textbox({
      name: visualId(), x: 20, y: 630, z: 1002, w: 1240, h: 60,
      text: "Cause minutes by hour and bank (from the original plan) aren't live here: Direct Lake tables can't carry a calculated hour-of-day column (see ADR 0014), and dim_bank isn't joined to fact_flight_leg (a two-month prototype, not a continuous calendar). Shown by month instead.",
    }),
  ],
});

// ---- pages.json ----
const pagesJsonPath = path.join(PAGES_DIR, "pages.json");
const pagesJson = JSON.parse(fs.readFileSync(pagesJsonPath, "utf8"));
pagesJson.pageOrder = [page1Id, page2Id, page3Id, page4Id];
pagesJson.activePageName = page1Id;
fs.writeFileSync(pagesJsonPath, JSON.stringify(pagesJson, null, 2));

console.log(JSON.stringify({ page1Id, page2Id, page3Id, page4Id }, null, 2));
