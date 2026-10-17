# Fresh WhatsMax installation: Justin's hosting handoff

Paul confirmed that this is a new installation with no existing WhatsMax workspace or server. This guide connects the supplied licensed application to the tested Companion/Odoo implementation. Justin owns hosting and the UI rebrand. Account creation, billing and a real message test happen on the intended host with the team's provider accounts.

## 1. Deploy the services that actually exist

| Component | Runtime / data | First-install requirement |
| --- | --- | --- |
| Companion (`companion/` in this repo) | Next.js server | Server routes and server-only Odoo variables; matching frontend/backend revision |
| Odoo and `dojo_companion_hub` | Odoo, PostgreSQL, persistent filestore | Reachable staging endpoint, installed addons, individual user/site grants |
| WhatsMax web | Next.js server, **MySQL**, Redis | HTTPS origin, license setup, initial admin and one real workspace |
| WhatsMax worker | Long-running Node process | Same private environment, MySQL, Redis and storage as web |
| WhatsMax scheduler | Long-running Node process | One scheduler for the initial deployment; same environment |
| Signed inbound sync | Scheduled Python command from this repo | Private workspace token, shared Odoo signing secret, persistent SQLite checkpoint |

Firebase/GKE is the hosting direction. Justin chooses the actual project, resources and endpoints. The WhatsMax worker/scheduler require running compute alongside the web app. MySQL is for WhatsMax; PostgreSQL remains Odoo's database. Provide persistent database and file storage before accepting real records. Neither cloud resources nor provider accounts are created by this guide.

Use the tested `dojo_*` Odoo records for the current kiosk/roster/CRM flow. The Dojang OS starter's separate `ops_core`/`ops_academy` stack is architecture reference, not an interchangeable database for this Companion. Starting its Compose stack does not connect the existing Companion automatically.

## 2. Get the private template and preserve its license

Sources inspected:

- [Whats:Max.zip](https://drive.google.com/file/d/121XTqUR_7ofpLlSwLCrAhFTO9HkroC-I/view), `whatsmax` package version 1.0.0.
- [dojang-operations-os-beta-0.3.zip](https://drive.google.com/file/d/1yfViJpz7V48R7vnQdcnCdXHlEa0nDEMK/view), `README.txt`, `.env.example`, `docs/W_GATES_E_H.txt` and communications adapter.

The inspected WhatsMax archive SHA-256 is `aa5970fca26e24c3c36c76e95ef5640b1e9a0e6057b0a4dbd928d05b6384d0ea`. If Paul supplies a newer archive, recheck its contracts against this guide.

Extract the purchased app privately and enter `Whats:Max/whatsmax/`. Keep this source, its license files and private environment outside the public Dojang repository. Use the original installation/license setup; this handoff does not replace or bypass it.

**API correction:** the OS starter's `{channel, to, text}` send example differs from the actual purchased application's endpoint. Our existing Odoo adapter already sends `{contact_id, channel, body}` to `/api/v1/messages/send` and reads `provider_message_id`. Retain that adapter. The purchased endpoint currently handles WhatsApp and SMS sends; its email branch is unimplemented. AgentMail/email is a separate integration.

## 3. Configure the new WhatsMax environment

The template recommends Node 22 and npm 10, MySQL 8+ and Redis 6+. Start from the vendor's `.env.example`, retaining its additional settings. Use [`delivery/whatsmax/whatsmax.env.example`](../delivery/whatsmax/whatsmax.env.example) as the Dojang checklist. Inject the completed values into all three processes, or use a private `.env` file in the private application directory.

- `APP_URL` and `NEXTAUTH_URL`: the **same final WhatsMax HTTPS origin**.
- `APP_ENV=production`, `APP_DEMO_MODE=false`, `SEED_DEMO_DATA=false`.
- `DATABASE_URL`: a new MySQL database/user, with URL-encoded password characters.
- `REDIS_URL`: the private Redis endpoint reachable by all three processes.
- `AUTH_SECRET`: generate with `openssl rand -base64 32`.
- `APP_ENCRYPTION_KEY`: generate with `openssl rand -hex 32`. Preserve this exact key across backups/redeployments: it decrypts saved provider credentials.
- `SEED_ADMIN_EMAIL` and a privately selected `SEED_ADMIN_PASSWORD` for the first install. If password is omitted, the vendor seed prints a generated password once; keep that output private. Remove seed credentials from normal runtime configuration afterward.
- SMTP: configure a real mail service for invitations and password resets. `MAIL_TRANSPORT=log` only logs email.
- Storage: persist `public/uploads` and `public/branding`, or configure the supported S3-compatible storage driver and verify any remaining local branding storage. Shared processes must see the files they need.
- Keep `WHATSAPP_QR_ENABLED=false` for this deployment. The core Cloud API path does not require the optional QR gateway.

On the Linux deployment/operator environment, protect the file and run the configuration check from this repository:

```bash
chmod 600 /private/whatsmax.env
python tools/check_whatsmax_setup.py config --env-file /private/whatsmax.env
```

This validates configuration **without contacting any service**. Exit 1 means a blocking setting is missing or invalid. Exit 0 means the checked settings passed; runtime, license, provider and delivery checks remain explicitly pending. Output contains check names/hints, not secret values.

The dotenv reader supports literal `KEY=value`, comments on their own lines and matching outer quotes. It does not execute shell commands, expand variables, merge with process environment or accept duplicate keys. Use literal completed values, including URL-encoded service credentials.

## 4. Install, initialize and run WhatsMax

In the private WhatsMax source directory, with its completed environment available:

```bash
npm ci
npm run db:generate
npm run db:deploy
npm run db:seed
npm run build
```

Run the seed as the deliberate first-install step on the new database, not on every web restart. Database migrations must finish before starting the runtime processes. Keep installation output private because the seed may print the initial password.

Run and supervise these three processes using the same release and runtime configuration:

| Process | Command in the private WhatsMax directory |
| --- | --- |
| Web | `npm start` |
| Worker | `node node_modules/tsx/dist/cli.mjs src/workers/index.ts` |
| Scheduler | `node node_modules/tsx/dist/cli.mjs src/scheduler/index.ts` |

The worker/scheduler commands use the same vendor entrypoints as `npm run worker` and `npm run scheduler`, without the development watch wrapper. Retain `tsx` in the runtime installation; the purchased package lists it as a development dependency. Installing with `--omit=dev` breaks these commands and the Prisma setup tools.

Expose the web process through HTTPS; keep database and Redis access private. Verify web sign-in, migrations, worker startup and scheduler heartbeat. A successful frontend build alone does not verify any of those services. Container images/manifests and the actual GKE deployment remain Justin's hosting work.

## 5. Create a workspace and connect the first channel

1. Complete initial admin/license setup in WhatsMax. Create the studio's client/workspace and an authorized workspace user.
2. Select that workspace before creating API tokens. Record its actual **numeric workspace ID**. Odoo IDs and WhatsMax IDs are separate.
3. Configure the real channel in WhatsMax. The inspected WhatsApp intake authenticates provider callbacks, queues messages, and requires a worker to populate the inbox. In production a missing Meta app secret is rejected. Verify the configured callback and a real inbound test message before enabling sync.
4. For the initial complete parent-message flow, use the configured WhatsApp Cloud API path. The supplied SMS route at `/api/webhooks/sms/{provider}` processes **delivery reports for campaign recipients**; it is not a parent-SMS inbox receiver. SMS sending exists, but a real SMS receive path must be implemented/verified before presenting an SMS round trip.
5. In the workspace Developer/API Tokens area, create a token with `conversations:read` for inbound sync. Create a separate `messages:write` token for Odoo's reviewed outbound worker. Both must resolve to the same intended workspace. Avoid a wildcard token. Additional scopes are only needed for additional functionality.

The native WhatsMax outgoing webhook is not the Odoo ingress contract: payload binding and signatures differ. The provided sync adapter reads the real vendor API and signs the normalized event for Odoo.

## 6. Verify the read connection before forwarding anything

Complete a private copy of [`delivery/whatsmax/roster-sync.env.example`](../delivery/whatsmax/roster-sync.env.example). For the read-only connection check, only these values are required:

```text
DOJANG_HUB_WHATSMAX_ORIGIN=https://<actual WhatsMax host>
DOJANG_HUB_WORKSPACE_ID=<actual numeric workspace ID>
DOJANG_HUB_WHATSMAX_TOKEN=<private conversations:read token>
```

```bash
chmod 600 /private/roster-sync.env
python tools/check_whatsmax_setup.py connection --env-file /private/roster-sync.env
```

It uses GET only: `/api/v1/auth/me`, the first conversation page, and the first thread's message page when a conversation exists. It rejects demo mode, workspace mismatches, malformed vendor responses and unsafe pagination links; it never follows redirects or pagination links. The provider may update token-use metadata and rate limits as part of those reads. No contact details, message text, provider bodies or token values are printed.

An empty new workspace reports the thread check as **pending**. `read_connection_verified` means the available reads passed; it does not prove send scope, channel configuration, worker health, Odoo ingress or delivery. Receive a controlled parent message and rerun before the full rehearsal.

## 7. Wire this workspace to Odoo and Companion

Follow [ROSTER-HUB-SETUP.md](ROSTER-HUB-SETUP.md#configure-a-shared-staging-instance) for addon installation, the Odoo site, individual user access, verified guardians and frontend settings. Use this mapping:

| Odoo / sync value | Source |
| --- | --- |
| Site `workspace_ref` / `DOJANG_HUB_WORKSPACE_ID` | Actual WhatsMax workspace ID |
| Site `provider_origin` / `DOJANG_HUB_WHATSMAX_ORIGIN` | Exact WhatsMax HTTPS origin |
| Site `provider_token_env` | Name of an Odoo-process env variable holding the **outbound** workspace token |
| `DOJANG_HUB_WHATSMAX_TOKEN` | **Inbound read** token, available only to the sync job |
| Site `webhook_secret_env` | Name of an Odoo-process env variable holding the signing secret |
| `DOJANG_HUB_WEBHOOK_SECRET` | Same signing secret, available only to the sync job |
| `DOJANG_HUB_SITE_ID` | Actual Odoo hub site ID |
| Guardian `contact_ref` | Exact numeric WhatsMax contact ID, verified by staff |

Finish the remaining sync settings: Odoo HTTPS origin, site ID, signing secret, fixed timezone-qualified `SYNC_SINCE`, and a private persistent SQLite checkpoint path. Set `DOJANG_HUB_PROVIDER_INGRESS_VERIFIED=true` only after independently verifying the provider's authenticated intake. The flag itself performs no signature test.

```bash
python tools/sync_whatsmax_hub.py --env-file /private/roster-sync.env
```

Verify that one incoming message appears in the hub, resolve the verified guardian/child/class, and check the instructor summary. Run the same sync again: it should report a duplicate and create no additional Odoo message. Keep the checkpoint and the original `SYNC_SINCE`. Schedule non-overlapping runs with the host scheduler and monitor exit status/counts within the vendor's API rate limits. This is polling, not an instantaneous deployed webhook relay.

After checking the outbound token's intended workspace, sender, test recipient and site settings, enable site `outbound_enabled` and activate **Companion: dispatch approved replies** in Odoo scheduled actions. Odoo must have a cron worker running. Review one reply in `/hub`, approve it, and verify its provider receipt and actual arrival on the controlled recipient's device.

`accepted` is not `delivered`. The vendor send endpoint does not persist an inbox message, so the existing polling job cannot guarantee a final status for that reply. Authenticated delivery-status relay work remains separate. Never retry an ambiguous send automatically; reconcile its provider outcome first.

## 8. Evidence Justin should return

- Companion, Odoo and WhatsMax staging URLs; deployed revision and addon versions.
- Successful configuration/read-check reports, with **pending** items retained.
- Worker/scheduler running and storage surviving restart.
- One real provider message in WhatsMax, one matching Odoo hub message, one reviewed reply and its observed provider/recipient outcome.
- A future makeup booking with its Odoo receipt; same records visible in a second browser after refresh.
- Explicit remaining gaps: delivery-status relay, real AI verification, private EB Gym installation result, broader SSO/voice/AgentMail/Workspace integrations as applicable.

No live account, cloud deployment or external send is performed by the scripts in this change. The known-good merged application baseline is PR #11, commit `f608c229399f4b3f4c17c4a96f53542fee6d4f52`; it passed both merged-release workflows. This fresh-install handoff extends its operator tooling without replacing the tested application.

Local verification on 2026-10-10: all 23 Python preparation/setup/sync tests passed. In the private, unmodified WhatsMax source, `npm ci`, Prisma client generation, TypeScript checking and the production build completed on Node 24.19.0; 34 focused vendor tests for bearer authorization, API pagination and WhatsApp signature checking also passed. The build logged missing database/encryption settings and database-access errors while falling back; its zero exit status is compilation evidence only. No MySQL database, Redis runtime, seed, license activation or real provider account was exercised. Justin still needs the configured host/runtime checks above; the template recommends Node 22 for that deployment.
