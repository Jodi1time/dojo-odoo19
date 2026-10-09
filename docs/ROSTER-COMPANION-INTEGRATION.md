# Roster Companion integration candidate

Based on `integration/monday-release` at `128a6c7`. Justin's imported UI remains the base. This branch extends the existing Odoo test gateway; it is not a production auth or messaging rollout.

## Connected behavior

- `/ops` displays authorized Odoo sessions and the reviewed follow-up queue.
- `/ops/sessions/<id>` displays the actual scoped class roster and that session's reviewed follow-ups. Pending means not yet checked in, never inferred absence.
- `/people/<id>` keeps the existing attendance view and adds persisted follow-up history.
- The Companion rail derives member/session context from the current route. It can show verified attendance and answer class-roster queries. AI explanations use only scoped read data when enabled.
- On a member, **Prepare parent follow-up** accepts a staff-entered attendance report and an explicitly selected enrolled session. It creates a durable proposal, shows original input, a neutral instructor summary, draft reply, provenance and exact action boundary.
- Approving the proposal saves an internal Odoo follow-up and receipt. The member, session and staff queue show the same record after refresh. No outbound message, booking, excused absence, financial change or rank change is executed.
- The existing kiosk check-in and encrypted recovery flow are unchanged. Its Odoo attendance is what the Companion reads. Staff capabilities are not mounted into the public kiosk.

## Setup on an authorized synthetic test environment

1. Check out this branch and upgrade `dojo_kiosk` on the disposable test database (`-u dojo_kiosk --stop-after-init` with your existing runtime/database arguments). New fields and the `dojo.companion.followup` model require the upgrade.
2. Preserve the existing separate kiosk/staff gateway credentials, signed-cookie pairing, allowed members and allowed sessions. Do not reuse a production database.
3. On the chosen `dojo.kiosk.config`, an administrator sets `integration_companion_followup_enabled = True`. `tools/seed_demo.py` enables it only for the new synthetic rehearsal fixture.
4. Run `npm ci` in `companion`, retain the documented integration environment, then build/start as usual. Pair a staff browser at `/integration/pair` and open `/ops` or a member.
5. Optional AI: configure the existing Odoo `ai.processor` provider and credentials through its normal administrator settings. Set `integration_companion_ai_enabled = True` on the selected config to permit scoped synthetic data to that provider. The adapter supports the existing OpenAI/Odoo-native and Gemini conversational methods. No new provider key or SDK is introduced in the browser or Next.js frontend.
6. With AI off, guided attendance and class-roster reads still work; replies are explicitly labeled template drafts. Provider failure falls back to a labeled template for follow-up preparation. Free-form Q&A reports unavailable rather than fabricating an answer.

## Monday walkthrough

1. Pair separate kiosk and staff browser profiles. Check a synthetic student in through the existing session-first kiosk.
2. Open the same session in `/ops`; show the roster and that student's member attendance.
3. Choose another enrolled synthetic student who has not checked in. Open that member's Companion and expand **Prepare parent follow-up**.
4. Select the class, paste "My child cannot attend this class. Can we arrange a makeup?", and confirm the selected identity/class. This is explicitly staff-entered input, not a demonstrated live parent inbox.
5. Prepare. Read the proposal and draft mode. Approve once. Show the receipt, the member's follow-up history, the class follow-up list and the staff queue.
6. Explain the next step accurately: a staff member still needs to verify guardian authority, makeup eligibility/availability and send an approved reply through a connected channel. This build does not claim any message was delivered or makeup booked.

## Contracts and limits

The only new upstream route is `/kiosk/v2/staff/companion-workflow`, with an allowlisted `context`, `ask`, `prepare` or `approve` operation. The service revalidates config, tenant, member/session allowlists and enrollment. It rejects public kiosk credentials. The language model only returns draft language/read-only explanations; it cannot choose an ORM method, member, recipient or write.

Preparation is idempotent by config/request key plus payload fingerprint. Approval locks the plan, idempotency key, attendance tuple and session, checks a 30-minute freshness window and the session/attendance snapshot, and returns a persistent receipt. Network uncertainty keeps the original approval key for retry. Scope is revalidated before replay.

The instructor summary is deliberately neutral and rule-based, without medical detail; AI, if configured, assists with reply wording and scoped Q&A. Raw parent reports remain on the proposal and are not included in the general staff queue. The queue is a shared authorized **staff test view**, not a production instructor-specific permission model. Parent identity/channel verification, notifications, external sending, eligibility-aware makeup booking, real user audit identity, deployed LLM credentials, and production role policies remain separate integration work.

## Validation

- `npm run test:odoo-integration` covers the transport, role/origin rejection, bounded proposal capabilities, context, receipts, conflicts and encrypted attendance recovery.
- `npx next typegen && npx tsc --noEmit`, `npm run lint`, `npm run build` validate the frontend.
- Native Odoo cases in `addons/dojo_kiosk/tests/test_kiosk_v2.py` cover persisted plans, approval/replay, same-record queue views, stale attendance, revoked scope, enrollment, explicit enablement and provider boundaries. The existing Monday CI is extended to this branch to run them against the pinned Odoo runtime.
- Provider methods are mocked in native tests. A successful test is not evidence of live model access or external-message delivery.

## Verification recorded for this handoff

Local checks passed: 53 Node gateway/recovery tests, Next production build (including TypeScript), ESLint with no errors (five existing unused-variable warnings), and Python compilation.

The browser fixture test is included at `companion/tests/rehearsal/companion-ui.cjs`; run it from `companion` after a build with Playwright and Chromium installed. It exercises the real Next routes against a synthetic HTTP fixture, not a native Odoo database. It could not complete in the authoring environment because Chromium was absent and browser downloads returned invalid archives. The production Next server did start successfully.

The new native Odoo tests were added but not executed locally: this environment has no Docker/Odoo/PostgreSQL runtime. Live model credentials, parent inbox, actual external delivery, and a deployed end-to-end flow have not been verified. GitHub publication was blocked by automatic approval review pending the user's explicit permission, so the branch CI has not run.
