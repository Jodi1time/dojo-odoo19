# Connected CRM lead and task increment

This patch builds on PR #21's member management. It is not a claim that the full project specification or shared staging deployment is complete.

## Working paths

- An individually authenticated owner/manager opens `/hub?view=crm` (also linked from the Hub).
- Search real `crm.lead` records in the site's company. Filter loaded results by actual stage, owner or configured tag/interest. Latest 100 matching leads are shown; narrower server-side search is available.
- Review and create a lead in the configured global intake stage. Review edits to its title and unlinked contact fields. Linked-partner contact edits require Odoo review because core inverses can change shared identities.
- Create an internal next-action task linked by exact ID to a lead or member, with an explicit due date and the signed-in user as assignee.
- Review and complete manual To Do activities with an outcome. Native Odoo completion preserves history; Companion suppresses follower notifications on this internal-only path. Automated/campaign activities remain under their native workflow.
- Propose a configured stage change as a saved review task. This deliberately does **not** change `stage_id`: existing Dojang stage automations can send email/SMS immediately. Completing that task also does not change the stage.
- Lead details show an explicit existing member/trial link when the relevant Dojang CRM fields are installed. A stage or trial flag is never presented as proof of attendance.

No new business database, guessed household/member association, automatic provider message, payment charge, campaign activation or privilege grant is introduced. The ordinary Odoo task/lead records are authoritative; Hub receipts are action audit/idempotency records only.

## Deployment requirements

Deploy this exact repo revision plus the corresponding `companion/` build. Unlike PR #21 alone, this increment adds the standard `crm` dependency and two nullable action-receipt relations. An authorized Odoo operator must back up the staging database and upgrade `dojo_companion_hub` using the deployment's established upgrade procedure, then restart workers. Do not point the frontend at an old Hub service or deploy only its Python file. No new secret is required.

Do not install or enable unrelated marketing/Stripe modules to make the screen appear functional. Existing company grants and individual accounts are still required. The public health endpoint alone cannot validate any of these actions.

## Verification

Local: production Webpack build, TypeScript, ESLint (8 pre-existing warnings), 91 gateway/integration checks, 4 member-context checks, 4 UI behavior checks and 32 Python tooling tests pass.

Added native Odoo tests cover role/company isolation; idempotent lead creation and replay after permission revocation; exact contact edits and linked-partner protection; native linked task creation/completion/history; notification suppression; invalid dates; campaign-task protection; stage proposals without stage mutation; exact-member tasks.

`companion/tests/rehearsal/crm.cjs` adds real browser → gateway → Odoo database checks for reviewed creation/edit, reload persistence, linked tasks, retained completion outcomes, unchanged stage after proposal, a separate browser's independent read and mobile overflow. Connected CI must pass before promoting this increment.

## Still requires separate verification / work

- Deploy and rehearse this build in the actual shared staging environment using an authorized account.
- Live AI activation in the Odoo worker and a freshly generated human-reviewed draft. An independently tested provider key is not live activation evidence.
- Payment collection/refunds and trial enrollment/conversion rules. Billing here remains read-only; a task is not a booking or payment.
- Reviewed customer delivery and scheduled inbound synchronization in the authorized provider environment; these are not implicitly enabled by CRM task creation.
- Full write-up parity (including onboarding permissions, instructor evaluation and deployment-specific CRM automations) still needs explicit acceptance checks.
