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

Mobile navigation occupies its own grid row below the scrolling workspace within
`100dvh`. Safe-area padding is on the dock; Companion is a dock action. Avoid fixed,
translated navigation inside an animated or scrolling content container.
