# Refactor Web UI API architecture

## Goal
Completely refactor the Web UI backend API design and the frontend API handling flow so they follow consistent API design rules, align with the frontend's real-time interaction model, and improve maintainability, error semantics, and observability.

## Confirmed Decisions
- Breaking changes are allowed.
- Legacy Web UI API contracts may be removed directly.
- Frontend and backend should migrate together to the new contract without a compatibility layer.
- Scope is limited to the Web UI API surface and the frontend API handling used by Web UI pages.
- This refactor does not include `/render/*` endpoints or the QQ bot command protocol.
- The new API response style uses a unified envelope.
- Success responses should use `data`, and list responses may include `meta`.
- Error responses should use a standard `error` object.
- The command configuration page must keep its current frontend interaction model.
- For the command configuration page, the existing user-visible interaction stays the same: users trigger save through the current per-action UI flow such as save buttons and toggle actions.
- The refactor target for the command configuration page is the backend API contract and the frontend API handling implementation, replacing the legacy batch-save API with per-action real-time persistence semantics.

## Requirements
- Refactor the Web UI backend API layer with a clearer and more consistent API design.
- Refactor the frontend API handling layer to match the new backend API contract.
- Use the `api-design` skill for API contract and route design.
- Use the `backend-logging-guard` skill for backend logging, exception handling, and traceability considerations.
- Replace the command configuration page's legacy batch update API with an API model that matches the page's existing per-action save behavior.
- Do not break the existing command configuration page's frontend interaction logic.
- Review request and response structures, error semantics, and status code usage across the Web UI API surface.
- Preserve raw API field names and values where returning original upstream payloads is required.

## Acceptance Criteria
- [ ] Web UI backend API routes are redesigned with clear resource semantics.
- [ ] Frontend API handling is updated to the new contract.
- [ ] Command configuration updates no longer depend on batch submission and instead use per-action persistence semantics aligned with the existing UI behavior.
- [ ] The command configuration page's visible interaction flow remains unchanged.
- [ ] Error responses and status codes are consistent across the Web UI API.
- [ ] Logging and exception handling are reviewed and updated where needed.
- [ ] The refactor does not break existing core Web UI management flows.

## Concrete API Contract

### Unified response envelope
- Success item/action: `{ "data": { ... } }`
- Success collection: `{ "data": [ ... ], "meta": { ... } }`
- Error: `{ "error": { "code": string, "message": string, "details"?: [...] } }`

### Command configuration routes
- `GET /webui/api/commands`
  - Response: `{ "data": [commandConfig, ...] }`
- `PATCH /webui/api/commands/{command_key}`
  - Request: `{ "data": { "enabled"?: boolean, "param_values"?: object } }`
  - Response: `{ "data": commandConfig }`

### Collection endpoint migration rules
- Users list: primary list goes in `data`, auxiliary group data moves to `meta.groups`.
- Groups list: primary list goes in `data`, auxiliary builtin group data moves to `meta.builtin_groups`.
- Servers list: primary list goes in `data`.
- Dashboard and settings GET should also remove `ok` and keep envelope consistency.
- Write operations should stop returning `ok` / resource-named top-level fields and return `data` only.

## Validation and Error Matrix
- Invalid JSON -> `400` + `error.code = "invalid_json"`
- Invalid request body shape -> `400` + `error.code = "invalid_request_body"`
- Semantic validation failure -> `422` + `error.code = "validation_error"`
- Resource not found -> `404` + `error.code = "not_found"`
- Conflict state -> `409` + `error.code = "conflict"`
- Unexpected internal failure -> `500` + `error.code = "internal_error"`

## Good / Base / Bad Cases

### Good
- `PATCH /webui/api/commands/{command_key}` with:
  - `{ "data": { "enabled": false } }`
  - `{ "data": { "param_values": { "cooldown": 30 } } }`
- Reason: one user action updates one command resource, matching the current UI behavior.

### Base
- `PUT /webui/api/commands/{command_key}` with a full command payload.
- Reason: workable, but weaker semantics for partial updates and easier to misuse.

### Bad
- `PUT /webui/api/commands/batch` with `{ "commands": [ ... ] }` for single-item saves.
- Reason: resource semantics do not match the current user interaction model and preserve the legacy transport mismatch.

## Technical Notes
- This is a cross-layer refactor spanning backend API design and frontend integration.
- Existing behavior and route usage need to be audited before changing contracts.
- The command configuration page is a key legacy hotspot and should be treated as a primary design driver.
- This refactor may introduce contract-level breaking changes across the existing Web UI API surface.
- Current `.trellis/spec/backend/*` files are mostly templates, and `.trellis/spec/frontend/*` files are missing. Implementation should follow existing project patterns, the loaded skills, and the cross-layer / code-reuse guides.
