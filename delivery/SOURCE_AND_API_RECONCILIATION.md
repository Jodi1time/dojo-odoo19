# Integrated operations implementation — 12 October 2026

Integration target: `Jodi1time/dojo-odoo19:integration/monday-release`.
Preserved baseline: `0b070d1` (PR26 connected workspace and PR27 neutral Dojang branding merged).
Working branch: `feat/integrated-operations`. No competing application or vendor database was created.

## Actual inspected sources

- Original `Kiosk-responsive-2.html`, Drive `1DtApwWuht8MToF34hR3e7u6EVPrUrkmY`: 219,955 bytes, 8 pages, 156 named functions, 37 API paths. Original public/session and staff interfaces inspected. The matching legacy `/api/display` and `/api/admin` backend is not present in this repository; the HTML cannot be represented as a verified connected application here.
- Paul's written document `16NAtCJXYMiDgoPgrirsY6-zDx4Vd2EWjQRQ6O4cBegs`: retrieved complete text, 116,647 characters. Its beta vertical, domain authorization, conservative AI approvals, human rank/deletion approval, audit, tenant isolation, and template-inspection requirements govern this work. Its suggested new repository structure is superseded by the user's explicit instruction not to restart.
- VNext preview: owner Today operational rhythm, priorities, sessions, modern typography/navigation. Its sample counts are design references, not operational records.
- Justin Companion `c108399`: original React routes, state reducers, Member360, asynchronous receipts and kiosk/member linking. Those components remain the foundation under `companion/`; added native gateways replace mock execution only in configured connected mode.
- Justin migration `e54466e9`: compared against integration; existing Odoo 20 migration retained. No migrated `eb_gym_management` source is in that branch.
- PR26 merged, PR27 merged. PR25 remains open; its three-file recording changes were inspected and applied to this integrated candidate, not blindly merged into the release branch.

## Purchased source audit

The four originals were located using the earlier handoff's `SOURCE_INVENTORY.json`, then downloaded from Drive and inspected. They are **available**, but their presence is not proof of installed integration. Commercial sources remain outside the repository; activation checks were not removed.

| Package | Source / stack | Existing reuse and compatibility | Remaining gate |
| --- | --- | --- | --- |
| WhatsMax | `121XTqUR_7ofpLlSwLCrAhFTO9HkroC-I`; Next 16.3.5, React 19.2.4, Prisma/MySQL, BullMQ workers | Existing `tools/sync_whatsmax_hub.py`, `delivery/whatsmax`, Hub delivery adapter. Actual `/api/v1/messages/send` accepts known scoped `contact_id`, channel, body; WhatsApp/SMS only. Native worker remains the communications provider, not Odoo's member source. | A running licensed provider, approved workspace/contact bindings, consent/quiet-hours and actual delivery evidence. Do not claim email/voice support from the enum. |
| Roster Template | `19vsfcXH64r7cGbXXHPTnqMX4w96QunB8`; `Companion:Biz:Violoop/roster`; Next 15, React 19, Prisma/Postgres, Redis/BullMQ | Inspected schema, UI card primitives, license gate and routes. Its member/contact/invoice tables overlap Odoo. Preserve Justin's modern shell rather than import a second router or authority. | Explicit license/deployment mapping and selected component porting; the integration ZIP is not the purchased Roster source itself. |
| EB-Gym | `13piAf6aIH7UENZqD4qjAebZIiqYzzHOz`; Odoo `19.0.1.3.0` commercial addon | Native HR staff wizard uses `hr.employee` and `hr.attendance`. New instructor workflow uses those same native records and existing `dojo.instructor.profile.employee_id`, not a competing shift model. Existing `dojo_eb_gym_bridge` still requires real addon installation. | Migrated Odoo20 vendor addon, bridge installation/test, approved member/membership identity reconciliation before switching any source of truth. |
| Dreams Gym | `1v2x9-IqgPIoZFUHzz8mvydKxRmU6hB3g`; nested NextJS/React/Laravel/etc archives | Inspected Next package: Next16.2.11, React19.2.7, Tailwind4, Redux, date/select/table/modal components. Attendance form imports static JSON defaults; not a connected backend. Do not copy sample records or install all frameworks. | Selectively adapt licensed UI components only where they improve the current experience, preserving the current tokens and domain contracts. |

WhatsMax and Roster source describe commercial Envato licensing. This inspection does not establish the purchased license tier or authorize redistribution. No original vendor code was published in this change.

## API and component mapping

| Original behavior | Integrated component / domain endpoint | Authority / current boundary |
| --- | --- | --- |
| `/api/display/sessions` GET | `SessionSelectScreen`, `/api/v2/sessions` → JSON-RPC POST `/kiosk/v2/sessions` | Paired device configuration supplies tenant/location and allowlist. New public response is school-day scoped with instructor, location and timezone. Staff future schedule remains available. No arbitrary browser-selected company. |
| `/api/display/roster` GET | `IdentifyScreen`, `/api/v2/sessions/:id/roster` → `/kiosk/v2/session/roster` | Explicit session/member allowlists, privacy-limited names; no full public member listing UI. |
| `/api/display/checkin` POST | `checkInFlow`, attendance gateway → `/kiosk/v2/attendance/check-ins` | Exact numeric native IDs, device key, expected version, idempotency/correlation, native eligibility, attendance/outbox/receipt transaction. |
| Shared instructor PIN → arbitrary instructor selection | `hub?view=instructor`, `instructor_context`, `instructor_clock` → authenticated `/companion/hub/action` | Individual Odoo session + site grant + own linked instructor/employee + rate-limited kiosk PIN. Shared PIN cannot impersonate another instructor. No public employee directory. |
| Instructor clock-in/out | `InstructorWorkspace`, `instructor.py` → native `hr.attendance` | Direction is explicit, clock-out binds exact open shift; employee row lock prevents concurrent shifts. Retry key scoped to actor/site. PIN excluded from receipt and digest. |
| Staff browser → public kiosk | `PublicModeGate`, `/api/integration/public-mode`, `AdaptiveShell` | Remove individual Hub and paired-staff cookies; retain kiosk-only pairing. Try native session revocation, report its outcome separately. Public UI stays blocked on failed browser reset; other staff tabs are cleared and Back forces reauthentication. |
| `/api/admin/instructor-timesheets` GET/PATCH | `InstructorWorkspace`, `instructor_context`, `correct_timesheet` | Managers/owners see current-company linked instructors; instructors see only self. Closed draft shifts only; expected version, UTC timestamps, overlap rules, before/after/reason/actor receipt. No payroll approval. |
| Student detail/edit, bookings, billing, progression | Existing `MemberManagement`, `ConnectedMemberDetails`, Hub member operations | Preserve PR26 exact member context, verified guardian relationships, manager-only sensitive sections and review-before-save. |
| Original reporting/email/automation/promotions/rosters | Existing native addons plus incomplete Companion operational surfaces | Do not equate native model presence with UI/end-to-end acceptance; tracked in feature status. |

## Verification boundary

Before modifications: 101 gateway/domain tests, 4 UI behavior tests, 4 member-context tests, TypeScript, and 32 tooling tests passed. After-change local checks pass; lint has the same 8 pre-existing warnings and no errors. Native Odoo and connected browser checks are required before this candidate is promoted.

Run `bash tools/run_rehearsal.sh` with the pinned Odoo checkout and Docker, or use the connected browser rehearsal CI. `DOJANG_INTERACTIVE_DEMO=1` runs the same real synthetic Odoo/Next stack for human walkthrough. Operator credentials stay in the private fixture, never screenshots or source.

This candidate does **not** assert whole-product acceptance, production rollout, template integration completion, provider delivery, payment settlement, waiver signing, full original operational-feature parity, or unified production device/guardian identity.

Further native wizard inspection: `dojo.book.trial.wizard.action_confirm` changes the lead stage and triggers existing email/SMS automation; it does not create a member enrollment. `dojo.convert.lead.wizard.action_convert` can create subscriptions, invite portal users and attach a payment method, and currently sets `trial_attended=True` as a conversion side effect. These cannot be presented as harmless preparation actions or wired directly to AI. A reviewed, scoped contract and side-effect-specific acceptance are still required; existing internal preparation stays intact.
