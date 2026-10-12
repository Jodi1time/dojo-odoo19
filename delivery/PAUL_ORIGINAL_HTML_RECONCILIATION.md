# Paul's original Martial Arts HTML: integration baseline

Source provided by Paul: [Kiosk-responsive-2.html](https://drive.google.com/file/d/1DtApwWuht8MToF34hR3e7u6EVPrUrkmY/view?usp=share_link)

Status: **source inspected; local run, backend connectivity, and client acceptance NOT YET VERIFIED**. Do not treat source-code presence as operational proof.

## Mandatory first action: reproduce original

1. Download and preserve an unmodified copy of `Kiosk-responsive-2.html` outside the repository. Do not publish source or device credentials.
2. Locate the **matching old backend** that implements the HTML's `/api/auth/*`, `/api/display/*`, and `/api/admin/*` paths. The HTML alone is not a standalone functioning Odoo application.
3. Run the old backend in an isolated, synthetic-data environment, then serve the HTML on the same origin or configure a controlled proxy for `/api`.
4. Verify kiosk display loads without exposing a staff login; verify staff login separately. Capture screen recording and timestamp, backend revision, and errors.
5. Share screenshots/recording with Paul and Justin before visual modifications.

## Original HTML screen inventory (verified from source)

| Section | Source functions / interactions | Acceptance |
|---|---|---|
| Dashboard | `renderDashboard` | Totals and recent activity agree with native records |
| Students | `renderStudents`, `saveStudent`, `deleteStudent`, photo upload, attendance history | Scoped CRUD and errors work |
| Check-in | `renderCheckin`, `toggleCheckin`, `selectClass` | Correct session, roster, mark and undo |
| Email | `sendMassEmail`, `sendIndividualEmail`, preview and history | Authorized sends and delivery status, no simulated success |
| Automation | `saveAutomation`, `testAutomation` | Actual save and execution/test outcome |
| Timesheets | `renderTimesheets`, `saveTimesheetCorrection` | Instructor records and authorized corrections |
| Reports | daily, by class, by instructor, peak times, workload, eligibility | Native data with filter and empty-state handling |
| Kiosk display | `loadDisplaySessions`, `loadDisplayRoster`, `selectDisplaySession`, PIN, clock-in/out, check-in | Session-first public flow and private-context clearing |

Additional original workflows: mass promotion and undo, promotion history, inactive-student follow-up, contact/family reports, test rosters, saved/printable rosters.

## Original HTML endpoint inventory (observed requests, not proof of server implementation)

- Public: `/api/display/sessions`, `/api/display/roster?session_id`, `/api/display/checkin`, `/api/display/instructor/verify-pin`, `/api/display/instructor/clock-in`, `/api/display/instructor/clock-out`.
- Authentication: `/api/auth/me`, `/api/auth/login`, `/api/auth/logout`.
- Admin: `/api/admin/students`, `/api/admin/students/save`, `/api/admin/students/delete`, `/api/admin/checkin/sessions`, `/api/admin/checkin/roster?session_id`, `/api/admin/checkin/mark`, `/api/admin/checkin/undo`, `/api/admin/attendance/student?student_id`.
- Communications and workflows: `/api/admin/email/mass`, `/api/admin/email/individual`, `/api/admin/email/history?limit`, `/api/admin/email/followup`, `/api/admin/automations`, `/api/admin/automations/test?kind`.
- Instructor, reports, promotions, test rosters: `/api/admin/instructor-timesheets`, `/api/admin/reports/attendance`, `/api/admin/reports/instructor-workload`, `/api/admin/reports/promotion-eligibility`, `/api/admin/promotions/promote`, `/api/admin/promotions/history?limit`, `/api/admin/promotions/undo`, `/api/admin/test-rosters`.

## Critical compatibility finding

Current Odoo kiosk V2 exposes **JSON-RPC POST** routes such as `/kiosk/v2/sessions`, `/kiosk/v2/session/roster`, and `/kiosk/v2/attendance/check-ins`. These are **not interchangeable** with the original HTML's `/api/display/*` endpoints. Build an explicitly authenticated adapter or migrate UI callers after validating field and error contracts. Do not silently alias the paths or make the public kiosk a generic ORM client.

## Acceptance matrix (all unchecked until observed)

- [ ] Original HTML running with matching backend, recorded and shared
- [ ] New kiosk shows correct session-first flow
- [ ] Actual session roster and authorized family selection
- [ ] Eligibility and duplicate-check-in guards
- [ ] Check-in persisted and read back from Odoo / Member 360
- [ ] Staff PIN, staff login, public/private isolation, idle reset
- [ ] Offline attendance replay with idempotency, if specified for release
- [ ] Member 360 sections display scoped real records
- [ ] CRM notes, owner changes, follow-up tasks persist and survive refresh
- [ ] AI Companion action requires appropriate approval and yields verified receipt
- [ ] Payments, customer messaging and waiver signing either provider-verified or visibly unavailable
- [ ] Adaptive workspace, role-specific navigation and map presentation reconciled with current written specification
- [ ] Full browser rehearsal against matched backend and frontend commits
- [ ] Paul/Justin review screenshot and approve scope for client walkthrough

## Rules for release

1. Odoo is the operational source of truth. Never represent mock data as a connected result.
2. Original HTML is the **visual and feature baseline**, not a substitute for newer session-first, security and governance requirements.
3. Use an explicit request/response mapping per action; protect device and staff credentials; log traceable result receipts.
4. Keep one integrated release branch. Avoid parallel competing kiosk shells.
5. Each acceptance row requires test evidence, owner, source revision, and pass/fail result.
