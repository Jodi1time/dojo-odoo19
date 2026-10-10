# Roster integration: meeting decisions, implementation and acceptance

This is the current implementation handoff for Justin and Jodi. It consolidates the meeting decisions instead of asking the team to choose between competing write-ups. The verified integration in [PR #7](https://github.com/Jodi1time/dojo-odoo19/pull/7) is merged into `integration/monday-release` at `6cb6826d8a537bdb337cd2fad427bb8380588ec0`. The follow-on branch `integration/justin-companion-handoff` reconciles Justin's newer frontend with that tested backend.

## What Paul is asking for

One headless dojo workspace: Odoo holds business records; EB Gym is the selected gym business foundation; Roster is the companion interface; Dreams Gym supplies reusable UI; WhatsMax supplies the nested communications and marketing hub. Do for Me uses authorized server-side tools to read or act on the signed-in user's behalf. The instructor roster is a class list, while Roster AI is the assistant that works with those records.

The frontend must carry the selected tenant, member and class context across panels. Signing into a shell does not itself provision provider credentials or authorize every Odoo app. Every tool execution must resolve the real principal, tenant, role and record scope on the server. No frontend provider secrets, caller-selected roles or arbitrary model-generated ORM operations.

Original references: [architecture write-up](https://docs.google.com/document/d/1p4LcjUvZYRnKBKi7DUzWuODOMW4k9IoPaUm2cYgXWG0/edit), [template folder](https://drive.google.com/drive/folders/17itxxYy2RVL70yqx6M6d8N8wBlZnBC2Q). The meeting names “What's Next” and similar variations refer to the supplied WhatsMax template; “Bioloop/Violoop” refers to the supplied Roster companion. Code/package names below are from the inspected archives.


## October 9 handoff: repositories, deployment and EB Gym

Justin supplied [jDelille/companion](https://github.com/jDelille/companion). Its default `master` is `c108399f38093c01f2e1f187e7feaa929a43eace`, with mock member/attendance/companion data. His `feature/odoo-integration` branch at `324976ccd089c03bc8a4bce8472e0ba794381385` adds an Odoo gateway and newer kiosk/member UI, but its Companion routes still serve mocks even in Odoo test mode. The supplied GitHub connection has read-only access to that repository.

The repo's listed [Vercel site](https://dojang-companion.vercel.app/) returned HTTP 200 and redirected to `/people/5001`; session and companion-context GETs also returned 200. This proves reachability only. The deployed commit and hosting configuration were not accessible through the connected Vercel account, and no real backend round trip was established on that site. Merging this Odoo repository does not automatically change Justin's separate frontend deployment.

The follow-on integration preserves Justin's member selection, People entry, attendance tab, local-time display, kiosk component changes and demo concierge UI. It retains the tested staff/kiosk gateway, encrypted offline recovery, durable follow-ups, class roster, mobile navigation and patched runtime dependencies. Unsupported member tabs stay disabled. The attendance tab displays the real latest check-in and explicitly says full history is not yet provided by the backend. Public concierge mocks are disabled in connected mode, so a simulated staff call or action cannot produce a connected success receipt. The companion-answer text contrast found during screenshot review is corrected.

Justin confirmed that he does not have a migrated EB Gym addon. The original `eb_gym_management-19.0.1.3.0.zip` exists in the shared template folder; its manifest is `19.0.1.3.0`. The earlier assumption that a migrated copy was waiting on Justin was incorrect. Migration is outstanding work. The bounded demo uses the existing `dojo_*` modules and does not require EB Gym; the full architecture still does. Keep the purchased addon in the team's authorized private source storage.

### Immediate implementation handoff

1. Review the combined `companion/` tree on `integration/justin-companion-handoff` and its exact-commit CI. It includes Justin's source at the pinned commit above. Do not replace it with his older `master` or copy back the older mock companion routes.
2. For a controlled staging deployment, use this repository with frontend root directory `companion`, or import this complete reviewed subtree into Justin's frontend repo on a review branch. Keep the matching Odoo addons from this same source revision. Choose one deployed frontend source and record its commit; do not assume both repositories automatically sync.
3. Upgrade `dojo_kiosk` on a disposable Odoo test database and apply the synthetic fixture/configuration. A repeatable local demo is available through `tools/start_demo.sh`; CI uses `tools/run_rehearsal.sh`.
4. Configure `DOJANG_INTEGRATION_MODE=odoo-test`, `NEXT_PUBLIC_DEMO_MODE=false`, the exact frontend origin and the hosted Odoo HTTPS origin. Configure separate kiosk/staff gateway and pairing keys plus cookie signing secret in the hosting environment. The environment template is `delivery/frontend/.env.integration.example`. A cloud deployment's `localhost` refers to that deployment, not Justin's laptop.
5. Pair separate kiosk and staff browser profiles at `/integration/pair`. Run the Monday walkthrough below. Verify the same saved record from a second staff session and record the deployed frontend/backend revisions.
6. Keep generative AI, external sends, calls, bookings and account provisioning explicitly pending until their real accounts, mappings, permissions and receipts have been tested. The public kiosk concierge remains demo-only.

The immediate external dependency is a reachable Odoo staging endpoint and authorized configuration/deployment access. Firebase/GKE remains Paul's target architecture; the currently listed frontend is on Vercel. Moving hosting providers is a separate deployment decision, not something completed by these commits.

### Onboarding and provider work from the meeting

Onboarding should establish the organization/tenant and site; create or invite owner, manager, instructor, member and guardian identities; verify guardian/member relationships; assign capabilities; connect channel accounts; and report each provisioning step's actual outcome with retry support. A saved form is not completed provisioning.

WhatsMax is the intended embedded inbox and automation service. Bind each Odoo tenant and verified person to its WhatsMax workspace/contact before processing messages. AgentPhone should support an incoming call, scoped answers, lead creation, transcription and human handoff; AgentMail should support threaded inbound/outbound email and approved replies. Google Workspace should supply authorized documents and business context. Voice commands must use the same tool authorization and approval path as text. Template availability does not prove provider connectivity, and installing an addon does not prove those flows work.

The requested finished absence flow is: verified guardian report → correct student/class → persisted source message → instructor summary → proposed reply/makeup solution → explicit approval → authorized booking/message execution → delivery/result receipt in Odoo and the companion. Today's verified slice starts with staff-entered text and ends with an internal follow-up receipt. It does not mark an absence automatically or invent a booking/message delivery.

## Meeting requirements mapped to evidence

| Requirement | Present evidence | Remaining acceptance condition |
| --- | --- | --- |
| Kiosk writes attendance used by CRM and instructor roster | Session-first gateway, Odoo attendance model, member and class views; real database rehearsal | Passing exact-commit rehearsal and deployed cross-device test |
| Student exit / checkout | Downloaded EB Gym has a `check_out` field; this connected kiosk slice implements check-in | Confirm class/session checkout semantics and wire the authorized exit command and timeline event; no checkout claim for this build |
| Staff navigation and contextual companion | Shared staff layout, `/ops`, `/integration/members`, `/people/<id>`, `/ops/sessions/<id>`; phone navigation and panel controls | Rehearsal must show context/receipts survive client navigation, and records survive reload |
| Parent report → instructor summary → useful action | Staff-entered report, enrolled class, durable proposal, neutral summary, optional AI reply, internal approval receipt | Verified inbound guardian identity, live model, delivery and eligible makeup booking are not connected |
| Odoo 20 + EB Gym backend | Pinned Odoo runtime; existing repo uses `dojo.member`, `dojo.class.session`, `dojo.attendance.log`; downloaded EB Gym is `19.0.1.3.0` | Migrate the supplied EB Gym 19 package and test install on the pinned Odoo runtime; map `res.partner`, `gym.membership`, `gym.attendance` to existing dojo records without duplicate canonical attendance |
| WhatsMax nested hub | Supplied Next/MySQL template has actual conversations, channels and social integrations | Mount/adapt the hub into the shell, bind tenant and contact identities, connect real account credentials; it is not mounted in this branch |
| Dreams Gym UI integration | React template available; current shell uses Justin's imported companion design | Apply agreed panel/navigation designs to connected routes; don't treat static template controls as implemented business functions |
| Onboarding provisions all user types | Starter onboarding saves steps/data | Implement tenant/site, owner/manager/instructor/member/guardian principals, guardian links, permissions and provider provisioning with retryable status |
| Same-login IAM | Separate signed synthetic kiosk/staff device cookies and scoped backend checks exist | Real individual identities, role-to-capability mapping, tenant isolation, revocation and actor-attributed receipts; pairing is not production IAM |
| CRM member, class and belt changes immediately update Odoo | Existing Odoo modules contain business models; connected member UI currently reads identity and attendance | Map each editable UI action to the actual migrated model/method, validate permissions and versions, persist once and refresh all affected views/history |
| AgentPhone / AgentMail / Workspace | Provider integration designs and starter adapters/templates exist | Correct transport and account configuration, authenticated webhooks, tenant binding, thread/history persistence, approval and delivery receipts; no live provider test yet |
| Voice Do for Me | Frontend composer has voice input | Browser microphone/transcription test and the same bounded tool/approval path as text; no independent voice authority |
| Firebase / GKE live access | Deployment architecture described in meetings; existing Firebase functions config names project `unitywrkos`, with no Companion hosting entry | Verify intended staging project/URL, deployed commit and configuration, then test from Justin's separate session |

The inspected `jDelille/dojo-odoo19` migration branch at `e54466e91e3cf848fcc4046dc1711ded988de0d5` contains no `eb_gym_management` addon. Its custom MCP bridge is not proof of native Odoo user-scoped MCP authorization. This is a specific missing source/configuration boundary, not a request to rebuild the purchased templates.

## Bridge implementation order

1. Start with the existing Odoo service boundaries and the supplied EB Gym source; Justin confirmed he does not hold a migrated copy. Define tenant-qualified mappings for member ↔ partner/membership, class/session, enrollment and attendance. Preserve existing IDs through explicit mappings; never assume two applications' numeric IDs refer to the same person.
2. Resolve login identity and capabilities in a backend-for-frontend. Carry selection IDs from the UI but reauthorize every read/write at the service boundary. The public kiosk receives only kiosk capabilities.
3. Make onboarding provision those identities, relationships and account connections. Report each provisioning step's real state; saving form JSON does not mean an account was created.
4. Embed/adapt WhatsMax views under Roster. The inspected contract uses `/api/v1/auth/me`, workspace-scoped `/api/v1/conversations`, and `/api/v1/messages/send` with `contact_id`, `channel`, `body`. Bind the Odoo tenant to the returned workspace and verified guardian to the channel contact. The generic send route inspected supports WhatsApp/SMS; other providers need their actual adapters.
5. Receive a verified parent event, deduplicate its provider ID and persist the source/thread against the authorized guardian/member. Roster reads the same records and prepares an explicit action with recipient, message and proposed class change.
6. At approval, recheck scope, contact authority, class capacity/eligibility and record versions. Execute supported actions with stable idempotency keys; persist provider acknowledgment, delivery/failure state and Odoo history. An uncertain network result is not a completed action.
7. Deploy the tested commit to the intended Firebase/GKE staging environment. Prove a second staff session sees the same saved record. Only then label that journey live.

The separate reviewed Beta03 starter handoff remains a candidate adapter implementation, not code deployed by this PR. Its communications endpoint deliberately refuses external sends until durable approval and guardian/contact resolution exist. Do not copy it into this repo and claim the whole hub is connected.

## Connected behavior

- `/` opens the authorized class workspace in connected mode, without selecting a hard-coded demo member.
- `/integration/members` uses the same persistent staff shell as class/member pages. Desktop and phone navigation use client links; the companion preserves per-page receipts while navigating. Persisted follow-ups are reloaded from Odoo after a full refresh.
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

## Verification and release gate

Local audit checks: 53 Node gateway/recovery tests pass; Next production build including TypeScript passes; ESLint has no errors and five existing unused-variable warnings; Python/JavaScript syntax and diff whitespace checks pass. These checks do not substitute for the native/browser run.

Dependency audit: updated Next and its ESLint config from `16.3.6` to `16.3.8`, Sharp to `0.35.5`, and source-map-js to `1.2.2`. The production dependency audit reports zero known advisories on 2026-10-09; CI now checks production dependencies as well as the 53 gateway tests and lint. This audit result covers the Companion production dependency tree, not every purchased template or development dependency.

The first repaired run at `f955ffba1883670e6cfcccfc52d6831ee3528ae9` passed all 35 native Odoo cases, then failed to reach Odoo over the host HTTP port. The runner now saves startup logs on failure, separately probes Odoo inside its container, and can relay a Linux internal-network service to host loopback while retaining Odoo's external network isolation. The complete rehearsal then passed on `0cadfc62da14daab762a9c77fc3c8f5ba69f7b48`: 35 native cases and 14 browser checks, with the expected database counts. See [run 38006668699](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38006668699). The follow-on UI reconciliation has its own exact-commit rehearsal; this earlier pass is not evidence for later changes.

The merged PR #6 backend and browser checks failed before browser execution because an expiry test tried to modify Odoo's protected `create_date` field. The readiness branch advances the clock instead and tests expiry at 30 minutes, approval before expiry, and receipt replay after expiry. It runs the native suite and real Odoo/Postgres/browser rehearsal through `.github/workflows/roster-readiness-audit.yml`. Read the exact commit's [Actions results](https://github.com/Jodi1time/dojo-odoo19/actions) and attached evidence; a queued/running run is not a pass.

`companion/tests/rehearsal/browser.cjs` uses actual Odoo, with three synthetic students: online check-in, offline recovery, and parent follow-up. It checks role separation, idempotent attendance, the same member attendance, internal proposal/approval/replay, persisted member/class follow-up, factual pending-attendance answer, client navigation, reload, phone controls and no browser exceptions. Final SQL verification requires exactly two attendance rows, one review receipt and one approved follow-up. Screenshots, browser results and SQL counts are CI artifacts.

`companion/tests/rehearsal/companion-ui.cjs` is a separate synthetic HTTP fixture test. It is not evidence of native Odoo, live AI or outbound delivery. Native tests mock provider methods; the real database rehearsal leaves generative AI off and labels template replies.

Before presenting the larger hub as ready, complete migration of the supplied EB Gym source; collect: passing Odoo/addon install; tenant/principal mappings; approved staging provider account configuration; real inbound parent event; explicit approval; provider receipt and matching Odoo history; Firebase/GKE URL and deployed commit; a separate authorized staff login that sees the same result. Credentials stay in the deployment's secret mechanism, not this document or chat.

A successful synthetic rehearsal proves the bounded attendance/internal follow-up slice. It does not prove production IAM, automated onboarding, WhatsMax embedding, a live model, external communication, makeup booking, all Odoo modules, or a public deployment. Keep that distinction explicit in Monday's demonstration.
