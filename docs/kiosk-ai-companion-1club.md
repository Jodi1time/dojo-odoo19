# Dojang Kiosk / AI Companion — 1Club + Template Integration

## Direction
Odoo 19 remains the source of truth. The existing Dojang kiosk is preserved and extended rather than rewritten. 1Club is the behavior/interaction reference; purchased gym/roster templates are acceleration sources, not new databases.

## Canonical kiosk journey
Welcome → Identify → Resolve household/member → Resolve eligibility → Choose class/service → Confirm → Atomic Odoo action → Receipt → AI next action → Privacy reset.

## Implemented in this branch
- Additive AI Companion drawer loaded by the existing kiosk shell.
- Live companion context endpoint backed by Odoo.
- Capability discovery so unsupported functions are not faked.
- Quick actions for check-in, member search, today's classes, membership, family, testing/events, facility map and AI help.
- Existing check-in/search flows remain the canonical action path.
- Existing token security remains mandatory.

## Existing flows intentionally preserved
Member search; barcode lookup; trial check-in; enrolled-session lookup; check-in/check-out; instructor PIN; roster management; attendance; member profile; session management; announcements; accessibility sizing; theme controls.

## Next integration slices
1. Member/household resolver and guardian-scoped actions.
2. Membership eligibility + plan/pass/day-pass actions.
3. Booking, waitlist and class-service selection.
4. Facility/location/map data once an authoritative Odoo facility model is confirmed.
5. Credential adapter for QR/PIN/NFC/wallet.
6. Testing/events registration.
7. AI action execution through allowlisted commands only.
8. Offline queue/idempotency and privacy reset hardening.

## Rules
- No duplicate member, attendance, membership, payment or booking databases.
- Do not show an action as live unless an Odoo model/service backs it.
- Kiosk mutations must be token-gated and server validated.
- AI never receives unrestricted ORM access.
- Shared-device private state must be cleared after completion/idle timeout.
