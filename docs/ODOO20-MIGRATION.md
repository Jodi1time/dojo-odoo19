# Odoo 20 Migration Plan

## Purpose

This branch is the isolated Odoo 20 migration line. It must not be mixed with kiosk, Companion, Next.js, or unrelated feature development.

Current baseline: Odoo saas-19.2.
Target baseline: Odoo 20.0.

## Migration rules

1. Never migrate the production database in place first.
2. Use a copied/staging database for module compatibility and data migration.
3. Keep feature work on separate branches.
4. Preserve the existing domain behavior before changing UX.
5. Treat Odoo as the operational system of record; do not create duplicate member, attendance, membership, booking, payment, rank, or household stores.
6. Keep kiosk/public authentication separate from staff/admin authentication.
7. All consequential writes must remain permission-checked, auditable, and idempotent.
8. Rank promotion remains human-governed.
9. Billing writes are never queued offline.

## Safe migration sequence

```text
main
  |
  +-- migration/odoo20
        |
        +-- Odoo 20 source + isolated staging DB
        +-- module compatibility scan
        +-- API/controller compatibility audit
        +-- migration scripts where required
        +-- domain tests
        +-- kiosk/session/attendance regression
        +-- data migration rehearsal
        +-- cutover plan
```

## Odoo 20 API implications

Odoo's legacy external XML-RPC/JSON-RPC services are deprecated. The database service is removed in Odoo 20. JSON-2 is the replacement for generic external model RPC.

Important distinction: custom Odoo controllers using `@http.route(type="jsonrpc")` are not the deprecated external `/jsonrpc` object service and may continue to be used where appropriate.

For Dojang, generic JSON-2 is an adapter option, not the front-end contract. The preferred architecture remains:

```text
Client / Next.js
      |
      v
Domain API / business action contract
      |
      v
Odoo adapter
  |- custom controllers
  |- JSON-2 where useful
  '- internal model/service calls
```

React/Next.js must not call arbitrary Odoo model methods directly.

## Current repository API inventory

### dojo_bridge
Current `/bridge/v1/*` endpoints are custom HTTP controllers returning raw JSON. They are not legacy external RPC calls.

Review for Odoo 20:
- Registry/cursor APIs
- request/database resolution
- authentication boundaries
- SUPERUSER usage
- company/tenant isolation
- response envelope consistency
- idempotency for mutations
- webhook signature verification

### ai_assistant
Current `/api/v1/ai/discover` and `/api/v1/ai/execute` are custom HTTP controllers used by n8n.

Review for Odoo 20:
- route/controller compatibility
- API-key auth
- intent permission checks
- confirmation flow
- response/error contract
- audit logging

### kiosk
Custom kiosk JSON-RPC controllers are internal controller routes. Do not confuse them with the deprecated external `/jsonrpc` service.

Preserve the canonical check-in invariant:

```text
choose live session
  -> load actual roster
  -> identify member
  -> validate eligibility
  -> attendance command
  -> success
  -> privacy clear
```

Never make check-in depend on AI.

## First compatibility gate

Before marking this migration ready:

- [ ] Odoo 20 source boots against a clean staging database.
- [ ] All custom addons install or each incompatibility is documented.
- [ ] Bridge health endpoint works.
- [ ] Member read works.
- [ ] Live session read works.
- [ ] Session-first attendance check-in works.
- [ ] Household relationships work.
- [ ] Membership eligibility works.
- [ ] Kiosk privacy clear works.
- [ ] API auth rejects invalid tenant/company context.
- [ ] Mutations are idempotent or explicitly guarded.
- [ ] Existing n8n AI API still passes contract tests.
- [ ] No client depends on removed Odoo database RPC service.

## Not part of this branch

- Kiosk visual redesign
- Companion UX
- Next.js framework migration
- Dreams/Roster template conversion
- AgentPhone
- AgentMail
- advanced marketing automation

Those stay on their own feature/migration lines.
