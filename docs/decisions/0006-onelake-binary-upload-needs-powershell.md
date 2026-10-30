# ADR 0006: Upload binary files to OneLake via PowerShell, not `az rest`

**Status:** done, 2026-10-30.

## Context

Needed to upload a small (92KB) clean CSV directly into a Lakehouse's
`Files/` area via OneLake's ADLS Gen2-compatible DFS API (create -> append
-> flush). `az rest --body @path` works fine for JSON request bodies
(every other call in this project uses it that way), but for this binary
upload the `append` call returned `202 Accepted` / exit code 0 with **zero
actual bytes landing** -- confirmed by listing the file afterward and
seeing `contentLength: "0"`. The subsequent `flush?position=91726` then
correctly failed with `InvalidFlushPosition`, since nothing was actually
there to flush. No error surfaced at the append step itself, which is the
dangerous part -- a silent partial failure, not a loud one.

## Decision

For binary file uploads to OneLake, use PowerShell's
`Invoke-WebRequest` with an explicit bearer token and `-Body $bytes` from
`[System.IO.File]::ReadAllBytes(...)`, not `az rest`:

```powershell
$token = (az account get-access-token --resource "https://storage.azure.com" --query accessToken -o tsv)
$bytes = [System.IO.File]::ReadAllBytes($file)
Invoke-WebRequest -Uri $url -Method PATCH `
  -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/octet-stream" } `
  -Body $bytes -UseBasicParsing
```

This correctly uploaded all 91,726 bytes on the first try. `az rest`
remains the right tool for every JSON-bodied Fabric/OneLake call in this
project (item CRUD, job scheduling, directory listing) -- this is
specifically about binary request bodies.

## Consequences

- Any future binary upload (a bigger FAA registry slice, a logo/image
  asset, anything non-JSON) should use this PowerShell pattern from the
  start, not rediscover the silent-zero-byte failure.
- Always verify an upload by listing the destination path afterward
  (`contentLength` matches the local file size) rather than trusting a
  202/exit-0 from the append call alone -- that status code does not mean
  the bytes arrived.
