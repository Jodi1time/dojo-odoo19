# Dojang workspace design pass

Based on the existing `integration/monday-release` at `fd1c56f` (PR #42), preserving the user's integrated app and fixes. Continues design PR #43.

## Design direction

The user's 1Club operator/member reference informs grouped navigation, scannable records and contextual actions. Dojang retains the finalized charcoal/off-white palette, editorial serif page headings, sans-serif operational labels, restrained red actions, frosted pill controls and circular icons. No crest, repeating watermark, decorative left accents or burgundy surfaces.

## Changes

- Grouped desktop navigation and member directory with exact-record navigation, membership filtering, clear filters and responsive identity/rank/state rows.
- Consistent title scale, page width, spacing and neutral glass across Today, classes, profiles, follow-ups and Hub views.
- Compact Hub tool disclosure preserves all existing role-gated links. Mobile titles stack above actions, keeping long titles readable.
- CRM summary strip, open pipeline lanes, flatter lead cards and a single-column list view. Search and view controls use compact pills.
- Intake, messaging, attendance, testing, bookings, billing/member management and reports share restrained fields and surfaces. Final reviewed confirmations use solid red; navigation and preparation remain neutral. Existing disabled guards, review handlers and action payloads are unchanged.
- Reduced nested card framing, responsive intake fields, legible phone inputs, readable report tables with print styling retained, and visible keyboard focus on the Companion composer.

## Verification

- Final normal production build + TypeScript passed. An explicit demo build also passed before the final navigation refinement.
- Existing 136 gateway/domain checks, four member-context checks and four dock checks passed.
- Existing directory/browser check passed: search/filter/clear, exact Theo profile navigation, desktop/390px/320px layouts, overflow and dock collapse.
- Additional local UI fixture checks cover CRM board/list, intake layouts, reviewed confirmation color, cancel behavior and tool disclosure. Fixture requests are intercepted only in the local test browser; no fixtures were added to application data or the backend.

These checks validate presentation and existing local regressions. They are not a new connected-Odoo acceptance result. The normal connected build remains the release artifact; local synthetic demonstration builds must never replace it. No API, Odoo model, authentication, payment handler or deployment configuration has changed. Connected CI remains the release gate.

