# 1Club-inspired workspace pass

## Basis and scope

Continue the existing `integration/monday-release` app at `fd1c56f` (PR #42), not the obsolete PR #28 handoff or the old standalone design preview.

Reference: the user's original `1Club_Dojang_Miro_Master_Board_133_Frames_KIOSK_REFERENCE.pdf`, including the source-color operator screenshots and member-profile frame 93. The black-and-white architecture workbook is not the product's visual theme. This pass adapts the operator hierarchy to the agreed Dojang black/red identity; it is not a pixel copy of 1Club's light/purple skin.

## Implemented

- Group existing desktop navigation into Workspace, Relationships and Management without changing route targets, member context or permissions.
- Scope neutral text colors and compact sans-serif headings to the staff shell. Public kiosk typography remains unchanged.
- Reduce oversized title and schedule spacing; retain restrained pill actions, circular avatars, fine dividers and the existing mobile dock.
- Present the existing member records in aligned name/number, rank and membership columns on desktop, stacking labels on phones.
- Search and filter the already-authorized member results by their actual membership state. Show the loaded-view count, truthful missing fields, a no-match state and clear filters.
- Normalize profile summary-card corners, profile headings and Hub heading sizes. No ornamental left accents added.

No API, Odoo addon, authentication, payment, check-in mutation, receipt or deployment configuration changes. No replacement business database or production demo data.

## Verified locally

- Production Next/Webpack build and TypeScript pass (normal build and explicit demo build).
- 136 gateway/domain checks, four member-context checks and four dock checks pass.
- ESLint: zero errors, nine pre-existing warnings.
- `tests/rehearsal/design-workspace.cjs`: production browser checks for search + membership filtering + clear, exact Theo profile navigation, desktop/390px/320px layouts, no page/main horizontal overflow, and existing home dock collapse under reduced motion.
- Desktop directory, Today, profile and mobile directory screenshots inspected internally. Browser data was explicitly labeled synthetic demo data. This is not new connected-Odoo or live staging acceptance evidence.

To repeat the UI test: install the pinned Playwright version used in `.github/workflows/browser-rehearsal.yml`; build and run Companion with `NEXT_PUBLIC_DEMO_MODE=true` on localhost, then run `DESIGN_TEST_URL=http://127.0.0.1:<port> node tests/rehearsal/design-workspace.cjs`. Do not deploy that demo build to connected staging.

## Remaining design acceptance

This is the first shared-shell/directory/profile hierarchy pass. It does not claim complete 1Club parity across onboarding, calendar, advanced CRM, billing, reports and kiosk. Review those screens against their specific evidence before further restructuring. Run the connected CI regression and deploy the normal connected build only through the existing authorized release process. No staging deployment was performed here.
