# Odoo 20 API Compatibility Contract

## Goal

Define a stable Dojang-facing contract while Odoo moves from saas-19.2 to 20.0.

The application contract should describe business actions, not raw ORM operations.

## Response envelope

Successful response:

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "api_version": "v2",
    "request_id": "..."
  }
}
```

Error response:

```json
{
  "ok": false,
  "error": {
    "code": "member_not_found",
    "message": "Member not found.",
    "details": {}
  },
  "meta": {
    "api_version": "v2",
    "request_id": "..."
  }
}
```

## Mutation requirements

Every meaningful write should support or document:

- authenticated actor/device
- derived tenant context
- permission/capability check
- idempotency key or equivalent duplicate protection
- version/concurrency guard where applicable
- audit event
- domain event emission
- deterministic error code

## Tenant rule

Never trust an organization/company identifier merely because it arrived in a browser request body. Tenant/company context must be derived from authenticated server-side context and validated before domain access.

## Odoo adapter rule

External clients should not call generic Odoo ORM endpoints as the product contract.

Good:

```text
POST /api/v2/attendance/check-ins
POST /api/v2/classes/{sessionId}/bookings
GET  /api/v2/members/{memberId}
```

Avoid as the product contract:

```text
POST /json/2/dojo.member/write
POST /json/2/dojo.class.enrollment/create
```

JSON-2 may still be used internally by a provider adapter when appropriate.

## Kiosk mutation example

Request intent:

```text
session + member + device identity + idempotency key
```

Server sequence:

```text
resolve tenant/device
-> load live session
-> load roster/member
-> validate membership/eligibility
-> check duplicate/idempotency
-> write attendance
-> emit attendance event
-> return receipt
```

AI is not in the required path.

## Migration compatibility notes

- Legacy external `/xmlrpc`, `/xmlrpc/2`, and `/jsonrpc` services are deprecated.
- Odoo 20 removes the legacy database service.
- Odoo's JSON-2 API is available for external model access.
- Custom controller routes using `type="jsonrpc"` are a different mechanism from the deprecated external RPC service.
