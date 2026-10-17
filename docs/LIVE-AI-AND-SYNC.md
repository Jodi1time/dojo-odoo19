# Live AI and scheduled WhatsMax sync — Justin's staging handoff

Use the merged PR #14 frontend and this backend patch. Do not reseed shared staging.

## Current evidence

Justin's October 10 evening report covers real Meta test-number inbound into hosted Hub, rejection of unsigned/incorrectly signed callbacks, sync deduplication, receipt #4 surviving reload/separate sign-in, and all five private EB Gym native bridge tests. The private EB Gym browser rehearsal remains separate.

The public health endpoint independently reported frontend `5250b28256838a608cb27cedb394455817417427` in connected test mode; that predates PR #14. Justin now reports `max_cron_threads=1`. Outbound was off, dispatch inactive and deliveries zero at report time. Recheck before activation.

## 1. Deploy matching code

Deploy the merged backend including this patch's `ai_assistant` and `dojo_kiosk` changes; restart all Odoo workers. No database fields are added. Sync the merged `companion/` subtree into Justin's frontend, preserve `apphosting.yaml`, and deploy. Set `DOJANG_RELEASE_SHA` to the exact frontend commit built, not the backend SHA.

With the staff pairing key supplied in the operator's private environment:

```bash
node companion/tools/check-deployment.mjs https://companion--dojang-companion.us-central1.hosted.app FULL_FRONTEND_COMMIT_SHA
```

This checks paired kiosk/staff reads; it does not verify Hub login, AI or delivery. Record frontend/backend SHAs separately.

## 2. Activate OpenAI drafts

Paul named OpenAI, ElevenLabs and Workspace MCP. This step covers OpenAI text drafts. ADK orchestration, voice and Workspace tools need separate wiring and tests.

Inject `DOJANG_OPENAI_API_KEY` as a private secret into the **Odoo process**, and `DOJANG_OPENAI_CHAT_MODEL` as the approved Chat Completions model ID. The existing default is `gpt-4o-mini`; confirm account/model access. Restart Odoo.

Set admin parameter `elevenlabs_connector.ai_provider=openai`. Enable `ai_enabled` on the intended `dojo.hub.site` and `integration_companion_ai_enabled` on that site's `kiosk_config_id` only. Record previous settings. Keep outbound off for this test.

The environment key overrides legacy database keys. An explicitly empty/invalid environment key fails closed. If absent, `openai.api_key` / `elevenlabs_connector.openai_api_key` still work. Model fallback is `openai.chat_model`, then the default. These overrides cover conversational drafting, not the separate older voice/intent processors. Never use frontend-public variables.

Prepare a **new synthetic report** in hosted Hub with the correct verified guardian, child and class. Expect **AI-assisted draft - human review required**. Review both texts and verify persistence after refresh. Existing saved drafts do not change just because AI was enabled. Record model, time and displayed mode without key/message content. A credential-present flag is not an inference pass. If fallback appears, check provider access/quota/model/private configuration; never relabel it as AI. Disable both AI flags to return to templates.

The transport sends no tools, caps completion tokens, uses `store:false`, rejects redirects/incomplete/refused/oversized output, and does not automatically retry. `store:false` does not mean zero provider retention. Odoo still controls actions and human approval.

## 3. Schedule inbound on GKE

The supplied `delivery/whatsmax/sync-cronjob.yaml` targets namespace `dojang`, starts suspended, polls each minute, forbids overlapping CronJob runs and persists its checkpoint. The CLI locks that checkpoint too, including manual runs on the same volume. Keep one scheduler/checkpoint per workspace/site. Separate machines/volumes do not share this lock.

Build from the repository root on Justin's Docker-capable workstation; replace the repository placeholder with the existing approved Artifact Registry repository:

```bash
ROSTER_SYNC_IMAGE=us-central1-docker.pkg.dev/dojang-companion/REPLACE_EXISTING_REPOSITORY/roster-sync:$(git rev-parse --short=12 HEAD)
docker build -f delivery/whatsmax/sync.Dockerfile -t "$ROSTER_SYNC_IMAGE" .
docker push "$ROSTER_SYNC_IMAGE"
docker image inspect "$ROSTER_SYNC_IMAGE" --format '{{index .RepoDigests 0}}'
```

Use the existing private sync settings and **fixed** `DOJANG_HUB_SYNC_SINCE`. Workspace/site are `1` in Justin's report; confirm these. Use the read-only WhatsMax token and Odoo signing key, not the outbound token. The ingress-verified flag is justified by the completed callback checks, not by setting the flag itself.

For kubectl use **unquoted literal KEY=value lines**: no export, expansions or inline comments. With shell tracing off:

```bash
chmod 600 /absolute/private/roster-sync.env
python tools/check_whatsmax_setup.py connection --env-file /absolute/private/roster-sync.env
kubectl -n dojang create secret generic roster-sync --from-env-file=/absolute/private/roster-sync.env --dry-run=client -o json | kubectl apply -f -
```

Confirm kubectl targets Justin's staging cluster. Copy the manifest to a private deployment directory, replace `REPLACE_WITH_SYNC_IMAGE_DIGEST` with the pushed `...@sha256:...` value, and apply that edited file with `suspend:true`. Verify the PVC binds using the cluster's default storage class.

Stop old manual polling. Using Justin's normal maintenance-pod method, copy the private checkpoint into the PVC as `/state/roster-hub-sync.sqlite`, UID/GID `10001:10001`, mode `600`, while no job runs. Do not publish it. If deliberately starting a fresh checkpoint, retain the original since value: prior events may be forwarded again and Odoo must deduplicate them. Do not delete Odoo messages or move since forward to hide replay/missed messages.

Run a single job from the suspended CronJob:

```bash
ROSTER_SYNC_JOB=roster-sync-check-$(date +%s)
kubectl -n dojang create job "$ROSTER_SYNC_JOB" --from=cronjob/roster-whatsmax-sync
kubectl -n dojang wait --for=condition=complete --timeout=300s "job/$ROSTER_SYNC_JOB"
kubectl -n dojang logs "job/$ROSTER_SYNC_JOB"
```

Logs contain counts only. A skipped/busy result is not a completed read test. Verify Hub intake and repeat-run deduplication, then resume:

```bash
kubectl -n dojang patch cronjob roster-whatsmax-sync --type=merge -p '{"spec":{"suspend":false}}'
```

Send one controlled inbound from Justin's verified phone. Confirm it appears once in hosted Hub **without manual sync**, check two scheduled completions and persistence after pod replacement. To pause, patch `suspend:true`; this prevents future jobs but does not stop an active one. Preserve Secret/PVC. Monitor the four-minute deadline and full-history/page-limit behavior as the workspace grows.

## 4. One controlled outbound reply

After Jodi approves Justin's requested one-message test, use only the Meta test number and Justin's verified phone. Review guardian binding, child/class, displayed recipient, workspace/send token and exact reply. Confirm there are no other queued or uncertain deliveries before enabling the site and dispatch action: cron processes queued approvals, not just the open browser. Confirm an Odoo cron worker runs.

Approve once. Record Odoo delivery ID, provider receipt, timestamp and arrival observed on Justin's phone. Reconcile uncertain outcomes before any retry. Turn site outbound off and dispatch inactive after the test. No bulk or other recipients.

Phone arrival is manual delivery evidence. The UI may still show acceptance because automatic delivery-status relay is not implemented. Scheduled sync does not fix this: the vendor send route does not create the outbound inbox row required by its polling status path.

## 5. Return one report

Return frontend/backend SHAs; scoped deployment result; AI model/label/persistence; scheduled inbound/deduplication; controlled reply receipt/arrival; remaining failures. No credentials or parent content.

Justin can finish the UI rebrand on these routes. Prepare eligible October 12 classes without reseeding shared staging, then rehearse kiosk -> roster -> Hub draft -> reviewed reply -> makeup receipt.

Still separate: SMTP invitations/resets (`MAIL_TRANSPORT=log`), automatic delivery status, private EB Gym browser rehearsal, ADK, ElevenLabs voice and Workspace MCP. Paul's production Meta connection needs callback/workspace/contact/guardian/sender checks again; the test-number pass does not validate a replacement account.

References: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) and [Kubernetes CronJobs](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/).
