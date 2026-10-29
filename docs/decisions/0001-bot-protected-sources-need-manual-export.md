# ADR 0001: FAA registry and DOT Consumer Report need manual export, not automated pull

**Status:** confirmed 2026-10-29, during Phase 1 source validation.

## Context

The plan's data sources table assumed CBP Airport Wait Times is the only
"manual Excel export landed in Files" source, with BTS, TSA, DOT, and FAA
all reachable by an automated HTTP pull in a Fabric Copy activity.

While validating sources against real data:

- `transtats.bts.gov` (BTS On-Time, T-100): plain HTTP, no bot protection.
  A direct `curl`/Copy-activity GET works fine.
- `tsa.gov/foia/readingroom`: plain HTTP, no bot protection. Scraping the
  link list and downloading weekly PDFs works fine.
- `registry.faa.gov`: returns `403 Forbidden` (Akamai) to a plain HTTP
  client.
- `transportation.gov/airconsumer`: returns an Akamai "Access Denied" edge
  block to a plain HTTP client.

## Decision

Treat FAA Releasable Aircraft Database and DOT Air Travel Consumer Report
the same way the plan already treats CBP: a manual, browser-driven export
landed in `Files/`, not an automated Copy activity. Do not attempt to
spoof a browser user-agent or otherwise work around the bot protection --
that's a deliberate control on a federal site, not a technical accident to
route around.

## Consequences

- Phase 1/4/7 ingest for these two sources needs a documented manual-drop
  runbook step (see `docs/runbook.md`, once written), same pattern as CBP:
  `meta.source_registry` still tracks cadence and last-loaded period, but
  there's no scheduled trigger -- a human downloads and drops the file,
  then the pipeline picks it up.
- This is itself a demonstration of the "data wrangling pain" the job
  posting asks about: knowing which sources need a human in the loop isn't
  a limitation to hide, it's exactly the kind of operational judgment call
  the role expects (see "Serves as the liaison between Data Engineering,
  Automation, and Analytics organization").
- If a real Mosaic-equivalent internal feed replaces these public proxies
  later (per the interview talk track's "bridge" beat), this constraint
  likely disappears -- internal data warehouses don't have Akamai bot
  walls aimed at the public internet.
