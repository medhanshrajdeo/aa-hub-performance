# ADR 0007: `%pip install` restarts the Fabric notebook kernel -- order cells accordingly

**Status:** done, 2026-10-30.

## Context

`nb_bronze_tsa`'s first real run failed in 12 seconds -- too fast to have
even attempted the PDF download, let alone a 4-minute parse. The REST
API's job-status failure reason was the same generic
`System_Cancelled_Session_Statements_Failed` seen before, no help. The
portal's run history showed the real traceback:

```
NameError: name 'WEEK_START' is not defined
```

...raised in the cell right after `%pip install pdfplumber -q`, even
though `WEEK_START` was assigned in an earlier parameters cell that ran
successfully before it. The only explanation: **`%pip install` in a
Fabric notebook restarts the Python kernel**, clearing every variable
defined in cells before it -- same as a local Jupyter kernel restart
after installing a package that needs a fresh import. The original cell
order was `[parameters, pip install, download, ...]`; the second cell's
install wiped the first cell's assignments before the third cell could
use them.

## Decision

Any notebook that needs `%pip install` must run it in the **first** cell,
before any variables (including a parameters cell) are defined.
`scripts/fabric/build_tsa_week_notebook.py` now orders cells as
`[pip install, parameters, download, parse, write, verify]`.

## Consequences

- `nb_bronze_bts` and `nb_bronze_faa` and `nb_bronze_weather` don't hit
  this because none of them need extra packages beyond what Fabric's
  default Spark runtime ships with (pdfplumber is TSA-specific).
- General rule for any future notebook needing `%pip install`: it goes
  in cell 0, full stop, even if that means the "parameters" cell isn't
  literally the first cell in the file.
- This is also a second confirmed case (alongside ADR 0005's runtime
  parameter-injection finding) where the REST API's job-status response
  alone was insufficient to diagnose a failure -- the portal's run
  history keeps being the only reliable source for a real traceback. See
  ADR 0004's "what this caught" section for the first case.
