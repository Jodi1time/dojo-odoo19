# dojo_bridge — Odoo ↔ NestJS Bridge API

Headless REST bridge that exposes Odoo 19 business data to the NestJS Control
Plane (and ultimately Next.js frontends) via a versioned, JWT-authenticated API.

---

## Architecture

```
Firebase   →  NestJS Control Plane  →  POST /bridge/v1/auth/resolve
                     ↓
           issues HS256 JWT containing:
             iss, aud, firebase_uid, tenant_db, company_id, role, email, name
                     ↓
               Odoo dojo_bridge (this module)
                     ↓
           reads dojo.member, dojo.class.session, dojo.subscription, …
```

**Odoo never talks to Firebase.** NestJS bears that responsibility and distills
the identity into an internal JWT. Odoo only verifies that JWT.

---

## Authentication

Every request (except `/health`) must carry:

```
Authorization: Bearer <HS256-JWT>
```

### Required JWT Claims

| Claim          | Type   | Description                                                     |
| -------------- | ------ | --------------------------------------------------------------- |
| `iss`          | string | Must match `x_bridge.jwt_issuer` (default `dojo-control-plane`) |
| `aud`          | string | Must match `x_bridge.jwt_audience` (default `dojo-odoo-bridge`) |
| `exp`          | int    | Unix expiry                                                     |
| `iat`          | int    | Issued-at                                                       |
| `firebase_uid` | string | Firebase UID — immutable identity anchor                        |
| `tenant_db`    | string | Odoo database name for this tenant                              |
| `company_id`   | int    | `res.company.id` for data isolation                             |
| `role`         | string | `member` \| `admin` \| `instructor`                             |
| `email`        | string | Used to link to `dojo.member` by e-mail                         |

### Generating a Test JWT (dev only)

```bash
docker exec odoo-web-1 python3 -c "
import jwt, time
payload = {
    'iss': 'dojo-control-plane',
    'aud': 'dojo-odoo-bridge',
    'iat': int(time.time()),
    'exp': int(time.time()) + 3600,
    'firebase_uid': 'test-firebase-uid-001',
    'tenant_db': 'odoo19',
    'company_id': 1,
    'role': 'member',
    'email': 'user@example.com',
    'name': 'Test User',
}
print(jwt.encode(payload, '<JWT_SECRET>', algorithm='HS256'))
"
```

---

## Configuration (ir.config_parameter)

Set these in the Odoo DB before the bridge will operate:

```sql
-- connect: docker exec -i odoo-db-1 psql -U odoo -d odoo19
INSERT INTO ir_config_parameter (key, value) VALUES
  ('x_bridge.jwt_secret',     '<min-32-char-secret>'),
  ('x_bridge.webhook_secret', '<hmac-secret>'),
  ('x_bridge.jwt_issuer',     'dojo-control-plane'),
  ('x_bridge.jwt_audience',   'dojo-odoo-bridge'),
  ('x_bridge.cors_origins',   'http://localhost:3000,https://app.yourdojo.com'),
  ('x_bridge.tenant_map',     '{"tenant-id": "odoo_db_name"}')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**After inserting via raw SQL, restart Odoo to flush the ORM cache:**

```bash
docker compose restart web
```

---

## Endpoints

### Health (no auth)

```
GET /bridge/v1/health
```

```json
{
  "status": "ok",
  "service": "dojo-bridge",
  "version": "v1",
  "db": "odoo19",
  "bridge_configured": true,
  "identity_table": true
}
```

---

### Identity

#### Resolve / Provision Identity

```
POST /bridge/v1/auth/resolve
Authorization: Bearer <JWT>
```

Creates an `x.bridge.identity` record if none exists for this `firebase_uid` +
`company_id` pair. Safe to call on every sign-in — idempotent.

**Response:**

```json
{
  "ok": true,
  "data": {
    "identity_id": 1,
    "firebase_uid": "uid-abc123",
    "company_id": 1,
    "company_name": "My Dojo",
    "is_active": true,
    "member_id": 42, // null if not yet linked
    "member_number": "BJJ-042",
    "membership_state": "active",
    "role": "member",
    "display_name": "Jane Doe",
    "email": "jane@example.com",
    "last_seen": "2026-03-10T20:01:53"
  }
}
```

`member_id` is `null` until a `dojo.member` record with the same e-mail exists
in Odoo. Link is established automatically on the next `auth/resolve` call
after the member record is created.

---

### Members

All member endpoints require a pre-existing identity (call `auth/resolve` first).

#### Profile

```
GET /bridge/v1/members/me
Authorization: Bearer <JWT>
```

Returns identity + full member profile (`null` if not linked).

#### Subscriptions

```
GET /bridge/v1/members/me/subscriptions
Authorization: Bearer <JWT>
```

Returns active + recent subscriptions.

#### Rank / Belt History

```
GET /bridge/v1/members/me/rank
Authorization: Bearer <JWT>
```

Returns current rank + promotion history.

---

### Classes

#### Session Schedule

```
GET /bridge/v1/classes/sessions
    ?from=<ISO-datetime>    # optional, default: now
    ?to=<ISO-datetime>      # optional, default: now + 30 days
    ?program_id=<int>       # optional filter
Authorization: Bearer <JWT>
```

Returns a list of open sessions with capacity and enrollment status.

#### Session Detail

```
GET /bridge/v1/classes/sessions/<session_id>
Authorization: Bearer <JWT>
```

#### Enroll

```
POST /bridge/v1/classes/sessions/<session_id>/enroll
Authorization: Bearer <JWT>
```

#### Cancel Enrollment

```
DELETE /bridge/v1/classes/sessions/<session_id>/enroll
Authorization: Bearer <JWT>
```

#### Check In

```
POST /bridge/v1/classes/sessions/<session_id>/checkin
Authorization: Bearer <JWT>
```

---

### Webhooks

```
POST /bridge/v1/webhooks/event
Content-Type: application/json
X-Bridge-Signature: sha256=<hmac-sha256-hex>
```

The HMAC is computed over the raw request body using `x_bridge.webhook_secret`.

**Supported event types:**

| `event_type`                 | Description                                     |
| ---------------------------- | ----------------------------------------------- |
| `subscription.created`       | Member subscription activated in billing system |
| `subscription.cancelled`     | Member subscription ended                       |
| `member.firebase_uid_linked` | Firebase account linked to member               |
| `payment.succeeded`          | Payment recorded                                |

**Body:**

```json
{
  "event_type": "subscription.created",
  "tenant_db": "odoo19",
  "company_id": 1,
  "firebase_uid": "uid-abc123",
  "data": { ... }
}
```

**Computing signature (bash):**

```bash
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print "sha256="$2}')
curl -X POST http://odoo:8069/bridge/v1/webhooks/event \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Signature: $SIG" \
  -d "$BODY"
```

---

## CORS

All bridge endpoints accept `OPTIONS` preflight requests and return:

```
Access-Control-Allow-Origin:  <reflecting request origin if in whitelist>
Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type, X-Bridge-Signature, X-Requested-With
Access-Control-Allow-Credentials: true
Access-Control-Max-Age: 86400
```

Configure allowed origins via `x_bridge.cors_origins` (comma-separated).

---

## Error Format

All errors return JSON with HTTP status code:

```json
{
  "error": "Unauthorized",
  "reason": "JWT has expired."
}
```

| Status | Meaning                                               |
| ------ | ----------------------------------------------------- |
| `400`  | Bad request (invalid JSON, missing required fields)   |
| `401`  | Unauthorized (missing/invalid/expired JWT)            |
| `403`  | Forbidden (inactive identity, unknown tenant)         |
| `404`  | Resource not found                                    |
| `500`  | Internal error (bridge misconfigured, Odoo exception) |

---

## Module Dependencies

```
dojo_base
dojo_members
dojo_classes
dojo_subscriptions
dojo_attendance
dojo_belt_progression
```

---

## Production Checklist

- [ ] Replace `x_bridge.jwt_secret` with a cryptographically random 64+ char secret
- [ ] Replace `x_bridge.webhook_secret` with a separate random secret
- [ ] Set `x_bridge.cors_origins` to `https://your-app.com` (no wildcards)
- [ ] Configure `x_bridge.tenant_map` with real tenant → DB mapping
- [ ] Rotate JWT secrets regularly (NestJS and Odoo must be updated simultaneously)
- [ ] Enable Odoo access logs for `/bridge/v1/*` paths in nginx/traefik
- [ ] Set `session_id` cookie to `SameSite=None; Secure` in proxy if serving over HTTPS


---

## API v2 (Odoo 20 migration)

The Odoo 20 migration branch introduces a domain-oriented `/bridge/v2/*` contract while keeping `/bridge/v1/*` available during migration.

### v2 response envelope

Success:

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

Error:

```json
{
  "ok": false,
  "error": {
    "code": "checkin_not_allowed",
    "message": "Member is not on this session roster.",
    "details": {}
  },
  "meta": {
    "api_version": "v2",
    "request_id": "..."
  }
}
```

### v2 endpoints in the first migration slice

```text
GET    /bridge/v2/health
POST   /bridge/v2/auth/resolve
GET    /bridge/v2/members/me
GET    /bridge/v2/classes/sessions
GET    /bridge/v2/classes/sessions/<session_id>
POST   /bridge/v2/classes/sessions/<session_id>/bookings
DELETE /bridge/v2/classes/sessions/<session_id>/bookings
POST   /bridge/v2/attendance/check-ins
```

### Check-in invariant

The v2 attendance endpoint enforces the session-first kiosk rule:

```text
live session
-> actual roster
-> member identity
-> active membership/subscription
-> attendance write
-> receipt
```

A member must already have a registered enrollment for the selected session.
Repeated check-in requests return the existing attendance record instead of creating a duplicate.

### Headers

Clients may send:

```text
X-Request-Id
X-Idempotency-Key
```

The v2 response returns the request ID for tracing. Booking and attendance responses also echo the supplied idempotency key while the underlying domain operations remain duplicate-safe.

### Odoo 20 external API note

Do not expose generic Odoo model CRUD as the product-facing contract. JSON-2 may be used by an adapter where appropriate, but Next.js/control-plane clients should call domain actions such as bookings and attendance through the bridge/domain API.
