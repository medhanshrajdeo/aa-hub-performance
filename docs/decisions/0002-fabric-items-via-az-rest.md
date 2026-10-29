# ADR 0002: Create Fabric items via `az rest`, not the `fab` CLI or portal clicking

**Status:** done, 2026-10-29.

## Context

Phase 0 needs `lh_bronze`, `lh_silver`, and `wh_gold` created in the
`aa-hubops-dev` workspace. The Microsoft Fabric CLI (`fab`) is not
installed in this environment; Azure CLI (`az`) is, and is already
authenticated as medhansh.rajdeo@tamu.edu with access to the workspace's
tenant.

Fabric exposes a REST API (`https://api.fabric.microsoft.com/v1/...`) that
`az rest --resource https://api.fabric.microsoft.com` can call directly
with the logged-in user's token -- no extra app registration needed.

Hit one environment issue along the way: `az rest` initially failed with
`Decryption failed: [WinError -2146893813] Key not valid for use in
specified state` -- a corrupted local MSAL token cache
(`~/.azure/msal_token_cache.bin`), unrelated to Fabric permissions. Fixed
by deleting the cache file and re-running `az login`.

## Decision

Use `az rest` against the Fabric Items API for workspace item management
(create lakehouse/warehouse, list items) for the rest of this project,
rather than installing `fab` or clicking through the portal, since it's
already working, scriptable, and leaves a command-line record of what was
created.

```
az rest --method POST \
  --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/lakehouses" \
  --resource "https://api.fabric.microsoft.com" \
  --headers "Content-Type=application/json" \
  --body '{"displayName": "lh_bronze", "description": "..."}'
```

## Consequences

- Creating a Lakehouse auto-creates a paired SQL Endpoint item with the
  same display name -- expect `len(items) > len(lakehouses) + len(warehouses)`
  when listing.
- Git integration (the rest of Phase 0's "done when") still needs a
  GitHub remote for this repo, which doesn't exist yet -- that's a
  separate decision, not a CLI gap.
