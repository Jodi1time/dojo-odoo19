# Roster hub: implementation and operator handoff

This guide covers the PR #10 hub integration and PR #11 booking-feedback follow-up on `integration/monday-release`. Justin owns hosting and UI rebranding; this repository supplies the backend integration, functional review screen and repeatable checks. A merge is not a deployment.

For the work that can proceed while provider accounts are pending, use [MONDAY-WITHOUT-PROVIDERS.md](MONDAY-WITHOUT-PROVIDERS.md). It includes the current staging evidence, the remaining hosted receipt check, and the separate private EB Gym rehearsal.

Paul confirmed a fresh installation with no existing WhatsMax deployment. Start with [WHATSMAX-FIRST-INSTALL.md](WHATSMAX-FIRST-INSTALL.md) for its MySQL/Redis/web/worker/scheduler setup, private configuration examples and read-only connection checks, then return here for the Odoo site and user setup.

## What the meeting means in the implementation

| Layer | Responsibility | Current implementation |
| --- | --- | --- |
| Odoo | Authoritative members, classes, enrollment, attendance and audit | Existing `dojo_*` models plus `dojo_companion_hub` |
| Instructor roster | Class list and attendance | Existing `/ops` and class views |
| Roster AI / Do for Me | Scoped context, draft, reviewed action and receipt | New `/hub`, fixed authenticated actions; optional AI drafting |
| WhatsMax | Workspace-scoped communications | API reader/signed ingress and approved SMS/WhatsApp send worker |
| EB Gym | Purchased membership and attendance module | Private source preparation and optional explicit record bridge |
| Dreams Gym / Roster design | Shared visual experience | Existing Companion components; Justin's rebrand remains his work |
| AgentPhone, AgentMail, Workspace | Calls, email, external knowledge | Not connected by this change |

The instructor roster and Roster AI are different components reading the same records. No AI response itself changes a class or sends a message. The authenticated fixed action does that after review and produces an Odoo receipt.

## Booking feedback follow-up (Justin's local rehearsal)

The booking feedback patch is frontend-only; it does not relax Odoo policy or enable messaging. Copy the matching `companion/app/(hub)/hub/page.tsx`, `page.module.css`, and `companion/src/domain/hub-actions.ts` together if applying the fix to a separately rebranded repository. Prefer merging the reviewed commit before starting overlapping edits.

- Success and failure appear beside the selected message's booking controls, with focus moved to the result. A confirmed booking shows its Odoo registration receipt and disables repeat submission until the operator changes the selection.
- Replacing a registration is unavailable once its original class starts. The UI explains using **Add a class registration** for an eligible future makeup instead. A missing/unverifiable original class also cannot be replaced from this screen. Odoo still checks enrollment, attendance, credits, capacity and cancellation rules at submission.
- If Odoo acknowledges the booking but the next screen refresh fails, the receipt remains successful with a refresh warning. Do not submit another booking to refresh the screen.
- If the write result itself is unknown, **Check booking result** reuses the original request key and payload. No automatic resend or new booking key is created. This pending retry is held in the current tab's memory, not persisted through a full reload; resolve it before reloading or signing out.
- Messaging remains intentionally disabled until the provider is configured and tested. A saved draft is not a sent message.

`npm run test:odoo-integration` includes the feedback-state unit tests. The connected rehearsal additionally runs `tests/rehearsal/hub-feedback.cjs`: ten synthetic browser checks, clearly separate from the real Odoo browser/database checks.

### Durable receipts after a full reload

The hosted rehearsal exposed a separate gap: the immediate success notice survived an in-page refresh but not a browser reload. The durable-receipt patch extends **Recent actions** with the saved Odoo registration receipt, class, date, current registration status and attendance state. It reads existing `dojo.hub.receipt` records; no booking replay or new registration is needed, including for bookings made before this patch. Only the most recent 50 scoped audit entries are shown; this is not a complete booking-history browser.

Deploy both `addons/dojo_companion_hub/models/service.py` and the matching Hub page/CSS. Restart the Odoo workers and redeploy Companion. This patch adds no database fields. A new frontend with an older backend explicitly says receipt details are unavailable rather than inventing success. Justin's rebrand should preserve this server-backed receipt component.

The backend projects only verified enrollment details, never the raw stored response or request key. Current site, member and instructor-session scope still governs reads. A cancelled registration is shown as cancelled, not as an active booking. Missing or malformed historical results do not break the Hub. The native tests cover those cases; the browser tests cover hard reload, returning to the page, mobile width and the older-backend warning. The real-Odoo rehearsal also verifies the receipt from a separately authenticated browser.

For the shared site, after deploying the fix: sign in, confirm the existing Monday registration receipt appears in Recent actions, reload, and confirm the same receipt remains. Do not book again merely to recover the receipt. One read-only receipt check does not verify outbound messaging or AI.

## What has been added

- Real individual Odoo login at `/hub`; encrypted, HttpOnly, two-hour frontend session cookie. Actual Odoo UID, company and site grants govern every action. Roles supplied by the browser are rejected.
- Manager resolution of a verified guardian's message to a child and enrolled class; original message, instructor summary and editable draft persist in Odoo.
- Guardian reads are limited to explicitly verified children. Instructor reads/actions require assigned sessions and enrolled students. Ordinary users cannot bypass the service by reading the private hub models directly.
- Reviewed reply queue, approver identity, frozen recipient/configuration, provider acceptance receipt and signed delivery status handling. No automatic retry after an ambiguous send.
- Checkout, eligible future booking, atomic class change, controlled member edits, rank history and new account provisioning. The UI currently exposes parent review and booking; the other operations are documented API actions for Justin's panels.
- Verified guardian account creation requires an existing staff-verified guardian/child/channel binding. Member signup creates a real lead/member and portal account. Existing users are never claimed by matching email. Password setup and provider provisioning remain explicit outstanding steps.
- WhatsMax polling adapter using the supplied template's actual numeric IDs and pagination schema. It signs normalized messages into Odoo and checkpoints acknowledged event hashes.
- Optional EB Gym mapping by the same real partner/company, explicit covered classes and valid membership dates. Mapped check-in/check-out writes share the Odoo transaction.

## Start a disposable local rehearsal

Requirements: Docker, Node 22, Python 3, npm dependencies and the pinned Odoo source specified in `.github/workflows/browser-rehearsal.yml`. Use a clean checkout and free ports 3000/8069.

```bash
cd companion
npm ci
npm run test:odoo-integration
cd ..
python -m unittest discover -s tools/tests -v
bash tools/start_demo.sh
```

The runner creates a new disposable database with synthetic members, subscriptions, classes and a manager. It does not use a production database, send messages or enable AI. Stop the runner to remove the disposable containers.

For automated browser checks, install the pinned Playwright version from the workflow and run `bash tools/run_rehearsal.sh`. It runs native Odoo tests, the original kiosk/staff browser flow, the individual hub login flow and final SQL assertions. Test results and screenshots appear in `rehearsal-evidence/`. Generated login/password fixtures are private temporary files and are deleted at shutdown; do not upload or paste them into chat.

The interactive runner also injects one signed, explicitly synthetic parent report into the local hub. It never contacts a messaging provider. For a manually operated demo, the runner prints the private fixture and environment paths. Read them on the operator's machine to obtain the synthetic hub login/password and pairing keys. Open separate browser profiles for kiosk, staff and hub. The hub manager logs in at `/hub`; the legacy kiosk/staff workspace uses `/integration/pair`.

## Configure a shared staging instance

1. Select one matching frontend/backend revision. Deploy this repo with frontend root `companion`, or copy the complete reviewed subtree into Justin's frontend review branch. His older `master` does not contain this integration. Record the resulting frontend and Odoo commits.
2. Back up the intended test database. With the existing Odoo runtime/config arguments, install `dojo_companion_hub` (`-i dojo_companion_hub --stop-after-init`) or upgrade it on later deployments (`-u dojo_companion_hub --stop-after-init`). Dependencies include kiosk and credit rules. Restart the normal Odoo service afterward.
3. An Odoo administrator opens **Companion Hub → Sites**. Create a site for the actual company and kiosk configuration. Enter the exact WhatsMax workspace ID, approved HTTPS provider origin and secret *environment variable names*. Leave external delivery disabled until account and recipient checks are complete.
4. In **User Access**, grant the intended individual users the correct site role. Instructors need explicit sessions. Guardians need explicit children and a verified binding for their user partner. An Odoo administrator is not automatically a hub owner; create an explicit grant. Revoke a grant to revoke hub access.
5. In **Verified Guardians**, map the actual WhatsMax numeric contact ID and channel to the verified guardian partner and children. Record the staff verification reference. Permit transactional replies only when appropriate. Do not infer identity from display names or assume IDs match across systems.
6. Configure server-only frontend variables from `delivery/frontend/.env.integration.example`, plus:

   ```text
   DOJANG_HUB_ENABLED=true
   DOJANG_ODOO_DATABASE=<staging database name>
   DOJANG_HUB_SITE_ID=<Odoo hub site ID>
   DOJANG_ODOO_URL=https://<reachable Odoo host>
   DOJANG_PUBLIC_ORIGIN=https://<frontend host>
   DOJANG_COOKIE_SECRET=<32 to 256 character secret>
   NEXT_PUBLIC_DEMO_MODE=false
   DOJANG_RELEASE_SHA=<full deployed frontend commit>
   ```

7. The Odoo process receives the secrets referenced by the site, for example `DOJANG_HUB_SCHOOL_WEBHOOK_SECRET` and `DOJANG_HUB_SCHOOL_PROVIDER_TOKEN`. Enter secrets through hosting configuration, never frontend code, chat or committed files. Restart Odoo after changing its environment.
8. Justin can deploy under his own hosting account and provide the staging URL. A team invitation is not required for Jodi to open the site and sign in with an authorized application account. A cloud frontend cannot reach Justin's laptop's `localhost`; Odoo needs a reachable staging endpoint.

The hub's current password login fails closed for accounts requiring Odoo MFA. Do not disable MFA to work around it. Completing the MFA/SSO handoff is separate work. The old paired staff shell also remains a separate test identity flow; this PR does not migrate every existing panel to individual login.

## Connect WhatsMax inbound messages

The supplied template has `/api/v1/auth/me`, `/api/v1/conversations` and `/api/v1/conversations/{numeric_id}/messages`. Its native outgoing `message.received` webhook lacks a workspace/contact binding and uses a different signature format. Do not point that raw webhook directly at the new Odoo route.

Run `tools/sync_whatsmax_hub.py` on a trusted server with a workspace-scoped bearer token that can read conversations. First configure and verify the upstream provider's webhook signature checks inside WhatsMax. The operator flag below records that this prerequisite was actually completed; the script cannot verify the upstream provider's settings itself.

```text
DOJANG_HUB_PROVIDER_INGRESS_VERIFIED=true
DOJANG_HUB_WHATSMAX_ORIGIN=https://<WhatsMax host>
DOJANG_HUB_ODOO_ORIGIN=https://<Odoo host>
DOJANG_HUB_SITE_ID=<Odoo site ID>
DOJANG_HUB_WORKSPACE_ID=<WhatsMax workspace ID>
DOJANG_HUB_WHATSMAX_TOKEN=<workspace bearer token>
DOJANG_HUB_WEBHOOK_SECRET=<same secret referenced by the Odoo site>
DOJANG_HUB_SYNC_SINCE=<fixed ISO timestamp with timezone>
DOJANG_HUB_SYNC_STATE=/private/roster-hub-sync.sqlite
```

```bash
python tools/sync_whatsmax_hub.py
```

Alternatively keep the settings in a private literal dotenv file (`chmod 600`) and run `python tools/sync_whatsmax_hub.py --env-file /private/roster-sync.env`. This does not evaluate shell commands or merge the file with process environment. Run `python tools/check_whatsmax_setup.py connection --env-file /private/roster-sync.env` first for a read-only provider check; its success is not evidence of a message send or an Odoo write.

Run once, inspect count-only output, then schedule it with the host's scheduler. Keep `SYNC_SINCE` fixed and retain the private checkpoint. It stores identifiers/hashes, not message bodies. Failed forwarding does not advance the checkpoint. Repeated events are deduplicated by Odoo too. Limitations: scans conversation/message pages, supports short text SMS/WhatsApp only, reports unsupported messages, and needs scheduling/monitoring on the intended host. It is not a deployed real-time webhook service.

Unknown senders are saved for manager verification. A verified sender still requires staff to select the correct child and class; the assistant does not guess among siblings.

## Enable reviewed outbound replies

1. Configure WhatsMax SMS/WhatsApp account(s), an actual contact and a workspace-scoped token with `messages:write`. Confirm `/api/v1/auth/me` identifies the intended workspace and `demo_mode=false`.
2. Configure the site provider origin/token variable. Enable `outbound_enabled` only on that site. The API still requires a reviewed draft, current guardian binding and authorized staff approval.
3. In Odoo scheduled actions, activate **Companion: dispatch approved replies**. It is deliberately installed inactive. Odoo must run a cron worker; `--max-cron-threads=0` disables dispatch. It processes one queue item per invocation.
4. Approve one controlled test reply in `/hub`. Confirm the recipient/channel and body on screen. The request creates the queue record; the worker sends later.
5. Verify the real provider receipt and actual recipient outcome. `accepted` means the provider returned a message ID. It does not mean delivered. `uncertain` means reconcile against the provider before any resend; this implementation never automatically retries an ambiguous external send.

**Known delivery-status gap in the supplied template:** `/api/v1/messages/send` sends but does not persist an inbox message. WhatsMax's SMS status path updates campaign recipients, and its legacy `message.status` subscription is not a delivered event. Consequently, the polling adapter cannot guarantee final delivery status for replies sent through this endpoint. A provider-authenticated status relay must submit the normalized status below, or the WhatsMax send/status implementation must be extended and tested. Until then, show `accepted` and verify in the provider; never present it as delivered.

The new Odoo endpoint is `/companion/hub/inbound/<site_id>`. It accepts a bounded JSON body with `X-Dojang-Timestamp` (Unix seconds) and `X-Dojang-Signature: sha256=<hex>`, calculated as HMAC-SHA256 of `timestamp + "." + raw_body` using the site's secret. Clock tolerance is five minutes. The relay must first authenticate the actual provider callback; holding this HMAC secret alone does not make an arbitrary status a provider fact.

```json
{"type":"delivery.status","workspaceRef":"11","eventRef":"unique-provider-event","providerRef":"actual-provider-message-id","status":"delivered"}
```

The corresponding inbound message schema is:

```json
{"type":"message.received","workspaceRef":"11","eventRef":"unique-message-id","channel":"sms","contactRef":"42","text":"My child cannot attend. Could we arrange a makeup?"}
```

## Enable and prove AI drafting separately from WhatsMax

WhatsMax configures communication channels, not the existing Odoo model provider. The hosted test currently says **Template draft - AI not enabled**. An administrator must configure an approved provider/account through the existing ElevenLabs Voice Connector AI settings (the implementation reads `elevenlabs_connector.ai_provider` and the OpenAI or Gemini key settings), then enable `integration_companion_ai_enabled` on the relevant kiosk configuration and `ai_enabled` on the matching Hub site. Do not put keys in browser code, a public repository or chat. This does not require enabling outbound messaging.

The current drafting path is `dojo.kiosk.service._followup_draft` → `ai.processor` conversational provider methods in `ai_assistant`. Inspect those deployed methods before enabling the account: model availability, provider billing and secret handling must be approved for that environment. Use a synthetic parent report for the first real request. A valid bounded response produces **AI-assisted draft - human review required**; missing keys, unsupported providers or rejected output must remain honestly labeled as template fallback. Verify the draft persists after refresh. No generated prose itself sends a message or changes a class.

OpenAI conversational drafting now accepts private Odoo-process overrides
`DOJANG_OPENAI_API_KEY` and `DOJANG_OPENAI_CHAT_MODEL`. See
[LIVE-AI-AND-SYNC.md](LIVE-AI-AND-SYNC.md) for the current activation, scheduled
inbound and controlled-reply handoff, including rollback and remaining gaps.

For Monday, separately record evidence of (1) saved Odoo booking and durable receipt, (2) real AI-generated draft, and (3) approved outbound provider acceptance plus recipient-side delivery. The current WhatsMax template still has the documented automatic delivery-status relay gap. If any one is untested, label that portion of the demo accordingly; UI polish does not close an integration gap.

## EB Gym private validation

The original supplied addon is `eb_gym_management` version `19.0.1.3.0`. `tools/prepare_eb_gym.py` creates a private prepared `20.0.1.3.0` copy and converts seven SQL-constraint declarations without overwriting the original. This does not prove installation or migration of an existing database.

Keep the purchased source outside this public repository. Use the prepared addon parent directory with:

```bash
DOJANG_EB_GYM_ADDONS=/absolute/private/addons bash tools/run_rehearsal.sh
```

The directory must contain `eb_gym_management/__manifest__.py`. The runner mounts it read-only, installs `dojo_eb_gym_bridge`, runs five bridge tests against the actual installed addon, then runs the normal browser rehearsal. This private test path has not been executed in the current environment, which has no Docker/Postgres runtime. Do not label EB Gym verified until it passes on Justin's machine or an authorized private runner.

After a passing install, use **Companion Hub → EB Gym Mappings** to map the same partner/company, real membership and explicitly covered class templates. Set `integration_eb_gym_required` on the relevant kiosk config when the mapping is mandatory. An active mapping participates in hub booking eligibility and mapped attendance. The bridge retains the existing dojo subscription/credit rules; it does not migrate invoices, replace billing authority, create all gym memberships automatically, or solve all existing-data reconciliation. Decide the billing/entitlement migration separately.

## Fixed action contract for Justin's panels

POST JSON to same-origin `/api/hub`. The gateway selects the Odoo database, site and user; never send model names, ORM methods, company, role or user authority. Mutations require a stable `requestKey` (UUID is suitable). If a response is uncertain, retry the identical operation/payload/key. A reused key with changed content is rejected. Refresh and review before using a new key after a conflict.

| Operation | Payload | Authority / outcome |
| --- | --- | --- |
| `context` | `{}` | Scoped members, sessions, messages, receipts and capabilities |
| `resolve` | `messageId, memberId, sessionId, expectedRevision` | Staff; binds verified child and registered class |
| `draft` | `messageId, expectedRevision` | Staff; saves summary and draft, never sends |
| `approve_reply` | `messageId, expectedRevision, reply` | Staff; queues exact reviewed text |
| `book` | `memberId, sessionId, expectedVersion` | Staff; future registration using existing eligibility/capacity/credit rules |
| `change_class` | Above plus `fromSessionId` | Staff; cancellation and booking atomic, existing cancellation policy applies |
| `checkout` | `memberId, sessionId` | Staff; saves one checkout timestamp for a real check-in |
| `update_member` | `memberId, expectedVersion, changes` | Owner/manager; allowlisted name/phone/email only |
| `rank` | `memberId, rankId, expectedVersion, stripes, reason` | Owner/manager; appends rank history with explicit reason |
| `link_guardian` | `partnerId, memberIds, channel, contactRef, verificationRef, allowReply` | Owner/manager; records completed human verification |
| `provision` | `name, login, role, memberIds, sessionIds, verificationRef`; guardian also `partnerId` | Owner/manager; real new account/grant; guardian requires prior verified binding |

IDs are strings, revisions/versions integers and `allowReply` a boolean. `expectedVersion` comes from current context. A rank ID or partner ID must come from the corresponding authorized Odoo setup UI; no broad partner/rank directory endpoint was added. Owner creation and linking existing accounts require administrator setup. Returned `password_setup_required` is not completed onboarding; no invitation, provider seat or email is silently sent.

Example booking request:

```json
{"operation":"book","payload":{"memberId":"123","sessionId":"456","expectedVersion":1234567890000000},"requestKey":"a-stable-unique-request-key"}
```

## Monday demonstration and remaining acceptance

1. Start the fresh fixture or shared staging revision. Show `/api/health` and record the release. Pair the kiosk/staff test views and sign into `/hub` with an individual granted account.
2. Check in a synthetic member through the kiosk. Show the same saved attendance in the class roster and member view. Demonstrate persisted checkout through the fixed action.
3. Bring in one actual provider message when configured, or explicitly identify a signed synthetic event in rehearsal. A manager confirms guardian, child and class. The assigned instructor sees the resolved record.
4. Prepare the follow-up. Show whether it is an AI-assisted or template draft. Both site `ai_enabled` and kiosk `integration_companion_ai_enabled` plus the existing Odoo AI provider settings are required for an actual model call.
5. Review a future class and confirm the booking. Show the registration receipt and refreshed Odoo roster. This does not imply a complimentary makeup entitlement; existing subscription/credit policies apply.
6. Review and approve a reply only after the provider is configured. Show queued → accepted → authenticated delivered if a tested status relay exists. Otherwise state the exact observed status.
7. Sign in from a second browser and verify the same summary, booking and receipts. Refresh/retry and confirm there are no duplicate records. Confirm revoked/unassigned accounts cannot act.

Still required beyond the code: shared staging URLs/deployment, private EB Gym install result, real provider accounts and controlled message test, final delivery-status relay, actual AI provider verification, MFA/SSO integration across the full shell, complete provider onboarding, omnichannel embedding and the AgentPhone/AgentMail/Workspace/voice work. The wider meeting architecture is not finished merely because these templates exist. The bounded Monday demonstration can proceed with clearly labeled capabilities and verified records.
