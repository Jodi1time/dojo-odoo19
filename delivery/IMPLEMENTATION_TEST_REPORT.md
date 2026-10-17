# Integrated Dojang verification and handoff

## Remaining-feature stack (PR31–PR38) — 13 October 2026

Each PR is stacked on the one before it, so they merge in order. PR30 is merged (`876aa1c`) and PR31 now targets `integration/monday-release`. Every application commit below passed the connected rehearsal on its own branch. Each of those runs covered the pinned Odoo 20 native tests, the gateway/contract tests, lint, the production Next build, every preserved browser rehearsal plus the new ones, and every SQL gate. Nothing is staged or deployed.

| PR | Branch | Tested app commit | Connected run | Adds | Native tests |
| --- | --- | --- | --- | --- | --- |
| [#31](https://github.com/Jodi1time/dojo-odoo19/pull/31) | `feat/audited-attendance` | `b178db0` | [38356756283](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38356756283) | Audited manual attendance and manager-only reversal; staff marks no longer send the guardian "just checked in" notice | 144 |
| [#32](https://github.com/Jodi1time/dojo-odoo19/pull/32) | `feat/trial-reservation-conversion` | `bf135f6` | [38357198275](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38357198275) | Trial reservation, cancel, attended/no-show, previewed conversion; no stage move, payment, portal invite or message | 151 |
| [#33](https://github.com/Jodi1time/dojo-odoo19/pull/33) | `feat/testing-promotions` | `c80da48` | [38360137558](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38360137558) | Testing eligibility, saved testing rosters, results, reviewed bulk promotion, latest-award reversal | 155 |
| [#34](https://github.com/Jodi1time/dojo-odoo19/pull/34) | `feat/guardian-kiosk` | `0b27c15` | [38360179541](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38360179541) | Parent check-in at the kiosk with phone and a staff-set hashed PIN; lockouts; own children only | 159 |
| [#35](https://github.com/Jodi1time/dojo-odoo19/pull/35) | `feat/communications-consent` | `cee21f1` | [38361407561](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38361407561) | Consent history, frozen-audience broadcasts with approval and quiet hours (queued, never "sent" without a provider), owner-only automation pause | 163 |
| [#36](https://github.com/Jodi1time/dojo-odoo19/pull/36) | `feat/refunds-settlement` | `3afcc10` | [38362484340](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38362484340) | Settlement evidence per payment; manager request, owner approval, native credit note, staff-recorded refund payment | 166 |
| [#37](https://github.com/Jodi1time/dojo-odoo19/pull/37) | `feat/waiver-signing` | `a95913e` | [38363509842](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38363509842) | Staff-witnessed waiver signing on the native `dojo_sign` record with evidence hashes | 168 |
| [#38](https://github.com/Jodi1time/dojo-odoo19/pull/38) | `feat/onboarding-setup` | `136e1a8` | [38364054476](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38364054476) | School setup and member onboarding checklists; responsive no-overflow check | 170 |

**CI diagnostics.** `tools/run_rehearsal.sh` now writes the failing step and its gate values as GitHub annotations. Those can be read through the API even though log downloads are blocked here. One stacked run failed and was fixed before acceptance: `38358048379` (`74f730b`). A testing-roster row and its result select shared one accessible name; `c80da48` fixed this.

**Decisions and boundaries recorded in this stack**
- **Source of truth (from Paul's write-up):** the dojo addons own members and memberships.
  - EB-Gym is a reference only and no bridge is installed.
  - The private Odoo 20 EB-Gym port `20.0.1.3.0` installs cleanly on the pinned Odoo 20 and is not committed.
- **Messaging:** no provider is connected in the rehearsal. Approved broadcasts stay queued. No provider credentials were used or stored.
- **Payments:** a payment counts as settled only through Odoo reconciliation, and each payment shows either the provider reference or that staff recorded it. Online-payment refunds wait for the provider's refund transaction; nothing calls the provider.
- **Dreams template:** the visual layer belongs to Justin. Its licensed files must not be committed to this public repository.
- **Deployment:** still blocked on deployment-owner access to promote matching frontend and backend revisions.


## Reviewed student intake increment (PR29) — 12 October 2026

This increment completes reviewed student/guardian/household **profile intake**. It is not membership sale, trial booking, onboarding or production rollout.

- Branch `feat/reviewed-student-intake`, [draft PR29](https://github.com/Jodi1time/dojo-odoo19/pull/29), based on `integration/monday-release` at the merged PR28 commit `3f32432fcf3abb60294542b604a1b9f07fe27300`.
- Application commit tested: `846ed11f1af65d834ed6bbcab3a8e2e4a1e94929`. Intake commits: `8ab2f9e`, `a10c434`, `5f6fc6f`, `846ed11`.
- Connected run [38335011606](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38335011606) **passed** at that commit: native install/tests, frontend contracts, lint, production Next build, all preserved browser rehearsals plus intake, and SQL assertions.
- Earlier runs that are **not** acceptance evidence: `38331291955` (`a10c434`) failed; `38333758855` (`5f6fc6f`) failed. CI logs and artifacts could not be downloaded from the working environment, so each cause was established by reproducing the same stage locally with the pinned Odoo runtime:
  - `a10c434`: `test_existing_guardian_change_invalidates_review` errored. Odoo 20 stamps `write_date` with the transaction time and ignores a supplied value, so the test never produced a stale guardian. The test now simulates another transaction's committed edit and also asserts no records were written.
  - `5f6fc6f`: the browser readback asserted the birth date inside a collapsed section. The member summary line now shows the stored birth date.

### What staff can do

1. Open **New student** from Members or the Hub (owner/manager only, individual Odoo sign-in, current school only).
2. Enter an adult (one contact method required) or a child with a new guardian or an explicitly selected existing household. Review lists every missing or invalid field by name; nothing is written.
3. If the same school already has the student (same normalized name and birth date, archived included) or the guardian contact, review stops and links the exact existing profile or offers only the matching selectable household. Unrelated or archived contacts are not disclosed as choices.
4. Confirm the reviewed summary. Odoo creates native `res.partner`/`dojo.member` records in lead state, and for a child the household with its primary guardian, in one transaction with a persisted receipt.
5. Open the created student. Member management shows the stored birth date and the **Household and guardians** Member360 section, which lists native household guardians separately from verified messaging channels.

A lost response is retried with the original key and replays the stored receipt. If the browser has lost that key (reload), the review finds the committed profile and links to it instead of creating a second one. A guardian or household edited after review returns a version conflict.

### Evidence

| Layer | Result at `846ed11f1af65d834ed6bbcab3a8e2e4a1e94929` | Boundary |
| --- | --- | --- |
| Native Odoo | 129 tests, zero failures/errors (112 preserved + 17 intake) | Synthetic database; Hub/Kiosk and installed dependencies only |
| Frontend contracts/state | 115 gateway/domain (including 8 intake) + 4 navigation + 4 member-context | Contract/state tests, not exhaustive component rendering |
| Tooling | 40 tests (including 8 intake validator) | — |
| Lint | 0 errors, the same 8 pre-existing warnings | Warnings recorded, not called clean |
| Browser | Intake: 6 checks — field-by-field validation, adult lost-response replay, Member360 birth-date/lead readback, child lost response plus reload reconciliation with household guardian readback, guardian-contact match linking, phone layout. All preserved PR26–PR28 browser suites rerun | Synthetic records; fault injection for dropped responses |
| Database | `intake_receipts=3`, `intake_members=3`, `intake_identity_keys=3`, `intake_households=1` (primary guardian linked to its household), `intake_household_children=2`, `intake_side_effects=0` (subscriptions, enrollments, users, channel bindings) | Not the production school database |

A Docker-free local run on the same commit and pinned Odoo (`d40b7c8c`) also passed 129 native tests, 76 browser checks across the seven rehearsal suites and the same SQL values. It used Python 3.13, local Postgres 16 and a webpack build with a stand-in font because Google Fonts is unreachable there; CI (Python 3.12 Docker, Turbopack) is the evidence of record.

### Still outside this increment

Membership or plan sale, trial reservation and conversion, waiver/document signing, photo upload, portal invitation and any outbound message remain separate reviewed workflows. Editing an existing household or guardian, converting an existing adult student into a guardian, and resolving archived or unlinked matching contacts still require administrator review in Odoo. Nothing in this increment is staged or deployed.

The PR28 report below is unchanged and remains the baseline for preserved features.

## Scope and release identity

This is a tested implementation increment, **not completion of Paul's total directive**. The original application, VNext design, all four purchased source packages, Justin's React and migration branches, and PRs 25–27 were inspected before this increment. See [source/API reconciliation](SOURCE_AND_API_RECONCILIATION.md) and the [full feature acceptance register](FEATURE_STATUS.md).

- Repository: `Jodi1time/dojo-odoo19`.
- Sole integration target: `integration/monday-release`.
- Working branch: `feat/integrated-operations`; [PR28](https://github.com/Jodi1time/dojo-odoo19/pull/28).
- Preserved starting revision: `0b070d172da1580bd28859b2295f51147d9c01b1`.
- Code candidate: `d4a5b937f5b2f49b52b180059cce17fa6d02d6fb`.
- GitHub tested merge ref: `b78b12f7f6fdc8fb57163a6de956e4d516c96979`; sanitized evidence artifact `11780871819`. The PR head above identifies the application changes under review.
- Final candidate run: [38324815345](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38324815345), **passed** the native/build/browser/database rehearsal. Review and production promotion remain separate gates.
- Previously passed expanded candidate: `3225d94882c06afd35e424da5f5dc01e6af761a6`, [run38323935159](https://github.com/Jodi1time/dojo-odoo19/actions/runs/38323935159).
- Pinned Odoo runtime: `d40b7c8c5de81ce8d1528a6b31608468f5f30d4d`.

No code was force-pushed or reset destructively. No AI coauthor was added. PR26/27 functionality was preserved, and PR25's recording improvements were reused after inspection. No original licensed template package was committed, and no duplicate vendor member database was created.

## Actual changes

1. **Native instructor workspace**: individual Odoo identity, existing instructor/employee link and rate-limited hashed PIN; explicit clock direction; actor/site idempotency; native HR persistence; reviewed manager corrections to closed draft shifts with version checking and before/after audit receipts.
2. **Operations**: date-bound native attendance and workload reports, current belt and household summaries, exact authorized session rosters and printable output. Unrecorded attendance remains pending, never inferred absent. Hard limits fail explicitly rather than returning misleading partial totals.
3. **Public-mode privacy**: kiosk entry clears both staff-cookie authorities before the public journey mounts, attempts native session revocation, clears other open staff tabs, and prevents browser Back from restoring staff content. An uncertain reset blocks public use and offers retry.
4. **Kiosk**: school-timezone current-day sessions, actual location/instructor metadata, Classes entry into the existing session-first journey, neutral Dojang labels, preserved removal of UFT crest effects. No AI is required for attendance or today's schedule.
5. **Verification**: native regression tests, contract tests, connected browser/SQL checks, five sanitized recording segments, report printing and compact/tablet/wide checks. These extend the same application and native records.

## Tests and evidence

Baseline before changes: 101 gateway/domain tests, 4 navigation/state tests, 4 member-context tests, TypeScript and 32 tooling tests passed. The baseline native application already had connected evidence from PR26. The first new native candidate failed four tests; these were corrected and rerun, not ignored.

| Layer | Final candidate result | Boundary |
| --- | --- | --- |
| Native Odoo | 112 tests, zero failures/errors | Hub/Kiosk and installed dependencies including native HR; not the entire purchased-addon matrix |
| Frontend contracts/state | 107 gateway/domain + 4 navigation + 4 member-context tests | Contract/state checks are not exhaustive rendered-component coverage |
| Tooling | 32 tests passed | Provider payload/adapter safety, not proof of live customer delivery |
| TypeScript / Next build | Passed | Production Next build against real synthetic Odoo |
| Lint | Zero errors; eight pre-existing warnings | Warnings are recorded, not called clean |
| Dependencies | Production audit: zero vulnerabilities | Development-tool install reports five high-severity findings; not silently upgraded here |
| Connected browser | Kiosk, staff/Member360, Hub, CRM, instructor, reports, printing and privacy passed | Disposable synthetic records; explicit fault injection for recovery |
| Persistence | Native SQL assertions passed | This is not the production school database |

The exact code revision passed the connected native/build/browser run above and a separate local rerun of the 115 frontend/domain/state tests, TypeScript, lint, production dependency audit and 32 tooling tests. Later report-only commits do not alter this tested application code.

SQL assertions after the final rehearsal:

```text
attendance_rows=2
review_receipts=1
approved_followups=1
resolved_hub_messages=1
future_class_bookings=1
checkouts=1
native_instructor_shifts=2
open_instructor_shifts=0
clock_receipts=2
correction_receipts=1
```

The two shifts comprise the clocked shift and a seeded closed draft used for correction. The two clock receipts prove clock-in/out execution, not two instructor clock-ins. Replayed uncertain requests do not create additional operations.

### Defects found and fixed

- Odoo's native `no_validation` HR policy creates auto-validated shifts, including open ones. The initial implementation incorrectly blocked the actor's own clock-out. Closing the exact open non-refused/non-derived shift is now allowed; historical corrections remain limited to closed draft records. The native payroll validation policy was not bypassed.
- A future-session fixture inherited a non-open state and was excluded by native scheduling. The fixture now explicitly creates an open future session; production session filtering was retained.
- Direct public navigation retained staff authorities. The new reset endpoint and rendering gate remove both cookies, preserve kiosk pairing, notify other staff tabs and handle Back/forward caching. Native revocation failure remains distinguishable from successful local-cookie clearing.
- Visual PDF inspection found that dark page styling remained below a readable roster. The final candidate adds a white HTML/body print canvas, safe page margins and a browser print regression assertion.

## Runnable build and walkthrough

The same integration branch is runnable; no standalone mockup is needed. Use a clean checkout, Docker, Node22, Python and the pinned Odoo source:

```bash
git clone --branch feat/integrated-operations https://github.com/Jodi1time/dojo-odoo19.git dojang-review
cd dojang-review
git init odoo
git -C odoo fetch --depth=1 https://github.com/odoo/odoo.git d40b7c8c5de81ce8d1528a6b31608468f5f30d4d
git -C odoo checkout --detach FETCH_HEAD
npm --prefix companion ci
DOJANG_INTERACTIVE_DEMO=1 bash tools/run_rehearsal.sh
```

The runner refuses an existing `companion/.env.local` or occupied application ports. It creates disposable isolated Odoo/Postgres resources and a production Next server at `http://localhost:3000`; Ctrl-C cleans up only that rehearsal. Operator-only pairing and synthetic login details are printed as private **file locations**, not for screen sharing. Never publish those files. For automated browser verification, follow `.github/workflows/browser-rehearsal.yml`, including its pinned browser installation.

Walkthrough order:

1. Pair the public synthetic device. Open Classes or ask today's schedule; select an actual session, identify a synthetic member, submit attendance and see native confirmation.
2. Exercise an already-present member, a dropped response and offline reconciliation; verify the original operation key and no duplicate attendance.
3. Open the same member in staff mode. Verify the attendance readback and authorized Member360 sections; review a contact edit, booking and internal follow-up.
4. In Sales, review a lead edit, recover an uncertain response, save an internal note and complete a native task. Trial/conversion preparation does **not** claim a booking or conversion occurred.
5. Sign in as an individual instructor and supply the masked PIN. Review clock-in/out; refresh for native readback. As an authorized manager, review a closed draft timesheet correction.
6. Open Reports, choose the school-date range and compare native attendance and workload totals. Open the authorized class roster and print it. Do not treat scheduled hours as worked hours or pending attendance as absence.
7. Return to public mode. Test a failed privacy reset followed by retry; confirm private staff routes and other tabs are cleared, while kiosk pairing survives.

The recordings document actual test-browser interactions and are slowed/captioned for readability. They use synthetic records, not real customer information. The complete required feature matrix accompanies them; the recording is not a claim of total product acceptance.

## Remaining work and decisions

**Implementation still required:** full student intake/archive/photo and bulk class assignment; audited attendance reversal and modern manual-attendance controls; public authenticated family selection; trial reservation/conversion; mass messaging and automation management/testing; bulk promotions/reversals and saved testing rosters; complete waiver/document signing and uploads; payment settlement/refunds; full facility/adaptive-workspace acceptance. These are real implementation gaps, not all external blockers.

**External/integration gates:** migrated Odoo20 EB-Gym addon and full vendor compatibility; approved template license/deployment mapping; definitive native member/membership authority and partner/company mapping; licensed provider and consent/quiet-hours delivery acceptance; production tenant/device/guardian identity and MFA requirements; matching frontend/backend release authority.

The connected Vercel account exposes the VNext design project, not the verified production application. Justin's repository is read-only through the present connection. Existing companion release workflows validate builds but contain no production deployment. The stale Odoo19 deployment prompt was not executed, and the design application was not overwritten. Production rollout of this revision is therefore **unverified and blocked by deployment-owner access**, despite the user's earlier deployment of a prior revision.

Justin's specific review decision: retain `integration/monday-release` as the sole integration target and the preserved `c108399` React / `e54466e9` migration lineage; supply or identify the migrated EB-Gym addon and confirm native membership mapping before deploying the paired revisions. Do not merge PR28 blindly or switch business-record authority implicitly.
