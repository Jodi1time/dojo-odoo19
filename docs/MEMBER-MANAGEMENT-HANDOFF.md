# Member management — 2026-10-12

This change builds on merged PR #20. Deploy the matching `companion/` frontend
and `addons/dojo_companion_hub/models/service.py`, then restart Odoo workers.
No database fields or additional secrets are introduced. An old Odoo backend
rejects the new read operation; the UI must show unavailable, never sample data.

## Entry and authority

Open `/hub`, sign in using an individual Odoo account with a site grant, choose
a member, and open member management. Connected member profiles also link here
from Payment and Profile details. The URL is
`/hub?memberId=<actual Odoo member ID>&view=manage`.

Paired kiosk/staff-device cookies are not Hub authority. Sample preview profiles
do not link to real member management. Unknown IDs never fall back to another
member. Instructor reads remain within assigned classes and enrolled members.

| Workflow | Authority and data |
| --- | --- |
| Contact edit | Owner/manager; name, phone and email only; review, member/partner version checks, audited idempotent write |
| Rank award | Owner/manager; same-company active rank, valid stripes and explicit assessment reason; preserves rank history |
| Class replacement | Staff; exact original and destination, future-session/attendance/eligibility rules, atomic cancellation and booking |
| Checkout | Staff; existing present/late attendance, one saved checkout timestamp |
| Billing view | Owner/manager; explicit subscription-linked posted invoices/credit notes, company-scoped, read-only |

All mutations require review. Unknown outcomes retain the original request key
and payload. Resolve the unconfirmed action before changing another record or
reloading. Successful writes remain successful even if a subsequent read fails.

Billing displays up to 50 subscriptions and 50 linked posted invoices. Shared
household invoice totals are labeled as whole-invoice amounts, not the selected
child's balance. No linked invoices is not proof of zero debt. Next invoice date
is not a confirmed payment date. No card details, payment collection or refunds
are exposed.

## Verification gates

- Gateway test: `npm run test:odoo-integration`.
- Native Odoo tests: `TestCompanionHub`, including scoped member reads, hidden
  instructor billing, saved checkout/rank history, contact-version conflicts and
  explicit invoice linkage.
- Connected browser rehearsal: `tests/rehearsal/hub.cjs` proves a reviewed contact
  edit survives reload and an independent browser read without changing another
  member; UI checkout survives reload.
- Synthetic browser fault checks: `tests/rehearsal/member-management.cjs` covers
  wrong identity, denied role, conflicts, lost-response retries, failed reads,
  rank and class-change payloads, mobile width and shared-invoice labels.

Run `bash tools/run_rehearsal.sh` in a disposable environment, not shared staging.
CI results are separate from proof of the shared deployment.

## Still separate work

This patch does not complete the entire CRM specification: live AI activation,
provider messaging/delivery reconciliation, payment collection/refunds, full
sales pipeline/task screens, account onboarding/password setup, and any EB Gym
migration require their own implementation/configuration and tests. Do not
enable outbound messaging, charge anyone, or reseed staging for this handoff.
