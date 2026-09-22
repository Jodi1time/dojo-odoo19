# Dojang Kiosk / AI Companion — 1Club + Template Integration

## Direction
Odoo 19 remains the source of truth. The existing Dojang kiosk is preserved and extended rather than rewritten. 1Club is the behavior/interaction reference; purchased gym/roster templates are acceleration sources, not new databases.

## Canonical kiosk journey
Welcome → Identify → Resolve household/member → Resolve eligibility → Choose class/service → Confirm → Atomic Odoo action → Receipt → AI next action → Privacy reset.

## Implemented in this branch
- Additive AI Companion drawer loaded by the existing kiosk shell.
- Live companion context endpoint backed by Odoo.
- Member search plus barcode/QR-payload credential resolution.
- Household/guardian resolution and family-member switching.
- Membership summary and issue states from the authoritative subscription/member records.
- Live class eligibility, booking, waitlist, cancellation and secure check-in handoff.
- Upcoming belt-test visibility.
- Read-only Ask Dojang chat using the existing AI intent/role system; mutating AI intents are blocked from public execution and handed back to deterministic kiosk flows.
- Online/offline status with mutation blocking while disconnected.
- Privacy reset on completion, close and idle timeout.
- Capability discovery so unsupported functions are not faked.
- Existing check-in/search flows remain the canonical action path.
- Existing token security remains mandatory.

## Existing flows intentionally preserved
Member search; barcode lookup; trial check-in; enrolled-session lookup; check-in/check-out; instructor PIN; roster management; attendance; member profile; session management; announcements; accessibility sizing; theme controls.

## Next integration slices
1. Guardian-scoped permissions for actions that affect minors or billing owners.
2. Pass/day-pass purchase and payment collection once the kiosk payment contract is confirmed.
3. Facility/location/map data once an authoritative Odoo facility model is confirmed.
4. NFC/wallet credential adapters when a backend credential model is available.
5. Testing/event registration and payment rules.
6. Optional AI-to-action handoff for additional allowlisted intents; public AI remains read-only by default.
7. Deeper offline recovery/idempotency for reconnect scenarios without ever showing a false success state.

## Rules
- No duplicate member, attendance, membership, payment or booking databases.
- Do not show an action as live unless an Odoo model/service backs it.
- Kiosk mutations must be token-gated and server validated.
- AI never receives unrestricted ORM access.
- Shared-device private state must be cleared after completion/idle timeout.
