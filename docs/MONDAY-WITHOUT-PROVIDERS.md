# Monday rehearsal while provider accounts are pending

Update: Justin's October 10 evening report covers real test-number inbound,
hosted receipt persistence and private EB Gym native tests. Use
[LIVE-AI-AND-SYNC.md](LIVE-AI-AND-SYNC.md) for current activation steps. This guide
remains the isolated provider-free rehearsal; its earlier hosting snapshot is historical.

The bounded demo can proceed without Meta/WhatsApp or a paid AI account. Demonstrate real Odoo attendance, persisted internal follow-up and a future registration with a durable receipt. Identify the parent message as synthetic and the draft as a template. Do not describe a queued or saved reply as delivered, or this bounded demo as the complete meeting architecture.

Justin owns staging deployment and the UI rebrand. This guide requires no new cloud project, provider account or billing approval. Keep outbound delivery disabled and the dispatch scheduled action inactive until the approved sender and recipient have been verified. The existing rehearsal runs with AI and external sends disabled.

## Evidence at the PR #13 baseline, October 10, 2026

| Item | Evidence and limit |
| --- | --- |
| Odoo source | `Jodi1time/dojo-odoo19` release branch at `b97af04a1f604d80b784c4bed0e6eb9cc06352fe`; PR #13 merged |
| Companion source | `jDelille/companion` `monday-release` at `5250b28256838a608cb27cedb394455817417427`; Justin reports this build deployed |
| Release CI | [Monday native checks](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38084985368), [Companion validation and rehearsal](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38084985528), and [WhatsMax setup checks](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38084988883) passed |
| Hosted public health | Independent GET of [Companion health](https://companion--dojang-companion.us-central1.hosted.app/api/health): HTTP 200, `mode=odoo-test`, `status=reachable`, `revision=null`, `backend=not_checked`. This does not verify Odoo or the running source revision. |
| Hosting, WhatsMax worker/storage/read token | Justin's supplied staging evidence reports these passing; real provider ingress, sending and AI remain unverified |
| Hosted receipt after PR #13 | Still requires the read-only check below. CI covers reload and a separate authenticated browser, but is not a check of the hosted database. |
| EB Gym | Justin reports the private port installs locally. The five bridge tests against that private addon have not been evidenced. |

This is a dated baseline, not a dynamic status page. Record new frontend/backend revisions after each deployment. The native release job previously exercised the kiosk class only; this change adds the Hub class to that job. The separate Companion release workflow already runs the full browser/database rehearsal and remains in place.

## 1. Identify the deployed build

Justin: set server-side `DOJANG_RELEASE_SHA` to the **full commit actually used for the Companion deployment**, then redeploy/restart that revision and verify `/api/health` returns it. For the currently reported frontend it is `5250b28256838a608cb27cedb394455817417427`. After importing another change or applying UI work, use the new frontend SHA; the Odoo source SHA is a separate value. Do not set an expected value merely to make a check pass.

Use the release's matching operator script with the staff pair key supplied securely in the operator environment (no command-line key):

```bash
node companion/tools/check-deployment.mjs https://companion--dojang-companion.us-central1.hosted.app FULL_DEPLOYED_FRONTEND_SHA
```

The updated endpoint and checker identify the scope as `paired_kiosk_staff_reads`. They test both device roles and internal follow-up reads. `hub=configured_not_exercised` is only frontend configuration evidence. `makeupBooking=not_checked` and `externalMessaging=not_checked` mean this endpoint did not exercise those flows; they do not mean the Hub code is absent. Individual login, booking persistence, private gym integration and actual delivery need separate evidence. `productionReady` remains false.

The updated checker intentionally requires the updated scope field. Use it after deploying the corresponding frontend, not against an older build. Alternatively, inspect the paired staff readiness page and retain its explicit limits.

## 2. Close the hosted receipt check without another booking

1. Sign in to [the Hub](https://companion--dojang-companion.us-central1.hosted.app/hub) with the authorized staging account. If the automated browser requires a manual handoff, complete these steps in an ordinary browser; do not work around credential protection.
2. Select **Followup Student** and find the existing Monday registration under **Recent actions**. In the earlier hosted test the registration receipt was **#4**. Use the actual saved receipt if the staging fixture has since changed.
3. Record receipt number, class date/time and current registration status. Reload the whole page, sign in again if needed, and select the same student. Confirm those saved details remain.
4. Repeat the read from a separate authorized browser/profile. Do not click **Confirm booking** merely to recover a missing receipt.
5. If details are unavailable, confirm the deployed Odoo `dojo_companion_hub` code and Companion code both include PR #13 and restart the relevant workers. Investigate the existing registration before creating another one.

Pass evidence: the same Odoo receipt before reload, after reload and in the second session. This is not provider-delivery evidence.

## 3. Run the provider-independent rehearsal

Use a disposable clean checkout of the exact candidate with Node 22, Python 3, Docker and free local ports 3000/8069. `tools/start_demo.sh` fetches the pinned Odoo runtime and installs frontend dependencies:

```bash
bash tools/start_demo.sh
```

It creates disposable containers and synthetic records, injects one signed synthetic parent event and prints the private fixture file locations. Inspect credentials locally, not in shared logs/chat. Stop with Ctrl-C to remove only that disposable environment. Do not run this fixture setup against the hosted database or overwrite provider configuration.

Walk through:

1. Separate kiosk/staff profiles: pair, check in Demo Student, then confirm the same attendance in the class roster and member record after reload.
2. Prepare and approve an **internal** follow-up for Followup Student. Verify its persistence. A paired staff approval is not the Hub's external reply approval.
3. Individual Hub login: resolve the explicitly synthetic parent message, select the correct student/class, and save a template draft.
4. Add one eligible future class registration. Show success/failure beside the controls and the durable Odoo receipt after reload. If the original class has started, use **Add a class registration**; do not weaken cancellation policy to make replacement pass.
5. Show that the kiosk cannot read staff details and that a user without the applicable Hub/member/session grant cannot perform the action. The automated rehearsal/native tests exercise these boundaries without changing real staff access.

The existing CI rehearsal validates native Odoo rules, kiosk/staff and Hub browser flows, repeat requests, durable receipts and final database counts. AI provider methods in native tests are mocked; no successful test here proves a live model response or messaging delivery.

For Monday, October 12, prepare time-appropriate synthetic classes in the isolated demo environment. Before presenting, verify the check-in class is currently eligible and the makeup class is still in the future. Do not reset the shared staging database just to change demo times.

## 4. Validate the purchased EB Gym bridge privately

On Justin's Docker-capable machine, use a clean checkout with the pinned Odoo source and frontend dependencies prepared as above. For the automated browser run, install the version pinned by `.github/workflows/browser-rehearsal.yml`:

```bash
cd companion
npm install --no-save --package-lock=false playwright@1.56.1
npx playwright install --with-deps chromium
cd ..
DOJANG_EB_GYM_ADDONS=/absolute/private/odoo20-addons bash tools/run_rehearsal.sh
```

The private directory must contain `eb_gym_management/__manifest__.py` from the prepared addon. The runner installs `dojo_eb_gym_bridge`, requires all five bridge tests to execute, and runs the normal connected rehearsal. Return a sanitized pass/fail summary and failing test names if any; never publish the purchased source, private fixtures or credentials. Review generated logs before sharing them.

An installed addon or `installed_not_integration_verified` readiness value is not a bridge pass. Without this evidence, demonstrate the existing dojo records and identify EB Gym integration as pending. Full billing/entitlement migration is outside this bridge rehearsal.

## 5. Keep provider activation as a separate gate

| Work that can proceed now | What still needs approved access or runtime evidence |
| --- | --- |
| UI polish, responsive layouts and honest success/error states | Justin's final hosted UI walkthrough |
| Odoo attendance, internal follow-up, permissions and booking/receipt checks | Hosted second-session receipt confirmation |
| Private EB Gym bridge rehearsal | Justin's private addon and Docker-capable runtime |
| Review existing WhatsMax sync settings, checkpoint storage and cron plan without activating them | Authenticated real inbound message before enabling provider sync |
| Review draft validation and template fallback tests | Approved AI provider/project, billing and a real draft request |
| Review the outbound approval/queue path with mocked providers | Approved sender/recipient, send token, Odoo cron worker and controlled send |

When accounts arrive, follow [WHATSMAX-FIRST-INSTALL.md](WHATSMAX-FIRST-INSTALL.md) and [ROSTER-HUB-SETUP.md](ROSTER-HUB-SETUP.md). The staging report has `max_cron_threads=0`, so a working cron worker is required before dispatch can run. Do not flip the verified-ingress flag before testing the real signed provider callback. The delivery-status relay is still a separate implementation gap: `accepted` is not `delivered`. SMTP invitations/password resets also remain pending while `MAIL_TRANSPORT=log`.

Return one short report: deployed frontend/backend SHAs; public health and scoped readiness results; hosted receipt result; private EB Gym result; UI rehearsal result; and each still-pending provider gate. Do not mark live messaging or real AI complete on synthetic evidence.
