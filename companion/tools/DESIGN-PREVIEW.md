# Actual-source design preview

`design/dojang-black-red` builds on `sync/justin-ui` (PR #16). The Next app in
`companion/` is the source of truth for the redesign. Firebase staging is unchanged.

The current preview host runs those same app, component, API and SCSS files through
Vinext on Cloudflare Workers. Its separate hosting checkout contains only host
build tooling plus an exact copy of this source. It is not the earlier static UI.
`/source.json` records the GitHub commit, source hash, and copied file list.

The host builds with `NEXT_PUBLIC_DEMO_MODE=true`. It uses the repository's fictional
sample records and existing mock APIs. Demo state is temporary server memory and
may reset or vary across worker instances; it is not a persistent staging database.
No Odoo credentials, pairing keys or backend secrets belong in this preview.

To update: commit the app changes, run `node companion/tools/sync-design-preview.mjs
/absolute/path/to/the/configured/preview`, then use the Sites source/build/publish
workflow. The host pins demo mode at build time and preserves the `sites()` plugin.
Run the normal Next build as well to verify the GitHub app independently of the host.

The preview also pins `NEXT_PUBLIC_DOCUMENT_NAVIGATION=true` at build time.
All page links use the shared `AppLink`, which becomes a native anchor in that
mode: browser navigation, reload, back, and opening a new tab do not depend on
the alternate runtime's client route transitions. In the Next deployment the
flag is unset and `AppLink` retains normal Next navigation. Keep in-page buttons
and form actions as React interactions.

Mobile navigation occupies its own grid row below the scrolling workspace within
`100dvh`. Safe-area padding is on the dock; Companion is a dock action. Avoid fixed,
translated navigation inside an animated or scrolling content container.

## Shape and visual direction

Use Dojang black and red. Follow the Porchlight control shapes: circular compact
icons/avatars and oval buttons, fields, filters, and status chips. Large reading
surfaces retain ordinary soft corners. Favor open lists and spacing over nested
cards, decorative gradients, repeated helper text, or heavy outlines. Keep one
clear primary action. The mobile dock keeps its own bottom layout row.

## Glass and motion

Use the supplied reference's open schedule rows, generous spacing, warm type,
and editorial serif headings. Glass belongs on the main class card, member
detail cards, review surfaces, and sheets; keep lists and ordinary text open.
The background uses stationary, faint red light on near-black. Never animate
blur or add continuous decoration. Content may arrive over 420ms; controls get
short press feedback. Never transform the shell or dock ancestor. Respect
reduced motion and transparency; the glass mixin provides a solid fallback.
Keep existing live-data labels accurate rather than copying invented reference
metrics or attendance claims.
