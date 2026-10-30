# ADR 0008: Parse TSA PDFs locally, upload clean result (not in-Fabric parsing)

**Status:** done, 2026-10-30.

## Context

`nb_bronze_tsa` needed `pdfplumber`, which isn't in Fabric's default Spark
runtime. ADR 0007 fixed one blocker (cell ordering around `%pip install`),
but the install still failed on every batch `RunNotebook` job with:

```
MagicUsageError: %pip magic command is disabled.
```

...while the exact same notebook definition succeeded when run
interactively from the portal. Microsoft's library-management docs
confirm this is intentional, not a bug: inline `%pip`/`%conda` installs
are disabled by default for scheduled/pipeline runs specifically because
they can produce different dependency trees run to run. The documented
fix is a custom Fabric **Environment** with the library pre-installed,
published in **Full mode** (3-6 minutes to publish, 1-3 minutes added
session startup) and attached to the notebook.

## Decision

Didn't build the custom Environment. Instead, parse all 35 weeks locally
(`pdfplumber` already works there, proven across dozens of real BTS/TSA
files this session) and upload only the clean, already-parsed CSV to
Fabric -- same pattern as FAA (ADR 0006), just for a different reason
(no %pip support vs. PII handling).

This is not a speed compromise. The PDF parsing itself is single-threaded,
driver-side Python work (`pdfplumber` extracting text/tables page by
page) -- Spark's distributed compute doesn't apply to it regardless of
where it runs. Running it "in Fabric" would add per-job Spark session
startup overhead (this project's pattern is one job per week, for
capacity reasons) without adding any parallelism. The real tradeoff
skipped here is the *setup cost* of a Full-mode Environment (several
publish/iterate cycles to get right) against a path that was already
working.

## Consequences

- `nb_bronze_tsa`'s committed notebook source (`fabric/nb_bronze_tsa.
  Notebook/`) is being replaced with a simpler version that just reads an
  already-parsed CSV from `Files/tsa/` and writes Delta -- no `%pip`, no
  PDF parsing in Fabric at all, matching `nb_bronze_cbp` and `nb_bronze_faa`'s
  shape.
- If a future notebook genuinely needs Spark-parallel processing *and* a
  non-default library (unlike TSA's single-threaded PDF parsing), building
  a proper Full-mode Environment would be worth the setup cost. Not every
  "needs an extra pip package" problem should default to local-then-upload
  -- this was the right call specifically because the workload wasn't
  Spark-parallelizable in the first place.
