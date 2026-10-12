# Invoice checkout and live AI drafting

Built on merged PR #22. Booking, profile, attendance, ranks, CRM leads and tasks are retained.

## Changes

- Member billing prepares a reviewed native Odoo invoice checkout for exactly the linked member/invoice and current outstanding amount. The gateway restricts checkout URLs to its configured Odoo origin and exact invoice. Household totals remain labeled.
- Existing published payment providers and available methods control availability. No key, provider, charge, invoice, refund or payment transaction is created by preparing checkout. Payment submission and webhook reconciliation use Odoo's existing checkout. Refresh reads the current native invoice balance; opening a link is never reported as payment.
- An owner can open Hub → AI drafting, review activation, and generate one synthetic draft in the Odoo worker. Both site flags are enabled only if a real AI draft is returned. A failure rolls back the flags. Managers can inspect status; other roles cannot. Idempotent retries do not call the provider again. Historical checks are explicitly not current-availability guarantees.
- All customer reply sending still requires its existing explicit review.

## Install and configure

Deploy this matching frontend/backend revision using the existing pipeline. Back up staging and upgrade `dojo_companion_hub` through the established operator procedure: its dependency now includes standard `account_payment`. No payment provider is enabled automatically.

Use the existing Odoo online payment configuration and published provider. `web.base.url` / invoice website must resolve to the same HTTPS origin as `DOJANG_ODOO_URL`. Odoo handles provider credentials and callbacks.

Load the supplied OpenAI key privately into the Odoo worker as `DOJANG_OPENAI_API_KEY`, with `DOJANG_OPENAI_CHAT_MODEL=gpt-4o-mini` (or the explicitly approved accessible model), then restart all workers. Existing private parameter fallback remains supported. Provider must be OpenAI; this patch does not switch a database-wide provider. Do not place the key in frontend-public variables or Git.

Sign in as the existing authorized owner. Open `/hub?view=ai`, review and confirm. Check the saved check timestamp, model and mode after refresh. Generate a new reviewed draft in the actual workflow; old template drafts are unchanged.

## Evidence and limits

The supplied OpenAI key produced valid summary/reply JSON in a direct synthetic request using the existing transport. This is credential/model evidence, not evidence that the hosted Odoo worker has loaded the secret.

Local production build, TypeScript and 95 gateway/integration tests passed. Four member-context and four UI utility tests and 32 Python tooling tests also passed. Lint has zero errors and eight existing warnings. New native Odoo tests cover signed checkout, no payment side effect, amount/version conflicts, unlink/revocation replay, role checks, AI activation persistence/replay and rollback on fallback. Native/connected CI is a separate required gate.

The assistant cannot verify shared staging actions while the Hub is signed out. The Firebase source repository `jDelille/companion` is read-only for the connected GitHub account; no hosted deployment of this patch is claimed. No real customer payment or external reply was sent. Refund processing and the broader specification are not certified by this increment.
