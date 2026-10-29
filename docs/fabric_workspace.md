# Fabric workspace

Dev workspace for this project (phase 0 of `planning/project-plan.md`
calls it `aa-hubops-dev`):

- URL: https://app.fabric.microsoft.com/groups/16b2a9c6-6418-485f-865f-c47442b8b6c6/list?experience=fabric-developer
- Workspace ID: `16b2a9c6-6418-485f-865f-c47442b8b6c6`
- Capacity: Fabric Trial
- Workspace settings: large semantic model storage format

Created 2026-10-29 via `az rest` against the Fabric REST API (see
`docs/decisions/0002-fabric-items-via-az-rest.md`):

- [x] Lakehouse `lh_bronze` (id `09a8b1f5-f33c-4eed-912d-40b9cbdc9b06`) -- auto-created a `lh_bronze` SQL Endpoint item too
- [x] Lakehouse `lh_silver` (id `852a5b52-5eee-4ca5-8380-3f9a970fa0a0`) -- auto-created a `lh_silver` SQL Endpoint item too
- [x] Warehouse `wh_gold` (id `9fa99a49-fc04-45d7-ac4a-598c8b0ad25e`)
- [ ] Connect Git integration -- **deferred to Phase 8 hardening**, not a blocker for Phases 1-7. GitHub is greyed out as a Git provider for this workspace (tenant setting off); Azure DevOps works but needs an org, which doesn't exist yet. Revisit then: either enable the GitHub tenant setting (Admin Portal, if this is a tenant medhansh.rajdeo@tamu.edu administers) or stand up an ADO org.
