# Dojang Friday / Monday Demo Readiness

> Historical planning baseline. Later meeting decisions and current implementation/acceptance evidence are consolidated in [ROSTER-COMPANION-INTEGRATION.md](ROSTER-COMPANION-INTEGRATION.md). The original status table and questions below are not the current task list.

Baseline:
- Source branch: jDelille/dojo-odoo19 migration/odoo20
- Baseline commit: 0566576641b23e24ddf8da5dd9abe4170d107b81
- Odoo source target: odoo/odoo 20.0 at d40b7c8c5de81ce8d1528a6b31608468f5f30d4d
- Companion UI: jDelille/companion
- Immediate goal: credible tested demo slice, not broad feature coverage

## Demo slice 1: Kiosk session-first check-in

Workflow:
1. Show live sessions
2. Select one live session
3. Load that session's actual roster
4. Identify the correct student within that context
5. Validate eligibility
6. Submit attendance command
7. Show backend-confirmed receipt
8. Clear private member context

Required evidence:
- Selected session remains fixed through identification and check-in
- Wrong-company or unauthorized member is rejected
- Non-roster member is rejected
- Inactive or ineligible membership is rejected
- Successful attendance exists in Odoo after confirmation
- Duplicate/replayed submission does not create a second attendance record
- Backend unavailable produces a clear error
- Private member state clears after completion and idle timeout
- AI unavailable does not block check-in

## Demo slice 2: Member 360 / CRM Companion

Current UI owner: Justin
Current state: responsive UI exists with mock data and basic Companion behavior

Minimum integration goal:
- Open a known synthetic member
- Load identity from the integration boundary
- Show status/training/attendance data from the same selected member
- Keep mock-only fields clearly marked until connected
- Demonstrate one bounded Companion read or suggestion only if the backend contract is ready

Do not claim live mutation if the action is still mocked.

## Work split, pending team confirmation

| Area | Proposed owner | Current state | Next evidence |
| --- | --- | --- | --- |
| Member 360 responsive UI | Justin | UI exists, mock data | Screen review + integration contract |
| K01-K07 kiosk UI | Justin | Not started at last sync | Responsive session-first flow |
| Odoo 20 runtime baseline | Justin + Jodi | Odoo 20 source pinned, addon migration in progress | Clean addon install/test result |
| Live session + roster reads | Jodi | Backend models exist | Contract + runtime query |
| Eligibility command | Jodi | Existing rules identified | Accepted/rejected runtime cases |
| Attendance command + receipt | Jodi | Review implementation prepared | Native Odoo tests + UI call |
| Privacy/offline states | Together | Requirements defined | End-to-end rehearsal |
| CRM Companion live action | Together | UI/basic AI only | Confirm one Monday-required task with Paul |

## Friday checkpoint

Show:
- repository + branch + commit
- working URL/build for completed UI portion
- exact live vs mocked matrix
- tests that actually ran
- known failures
- blockers and owners
- Monday plan
- scheduled joint rehearsal

## Monday client gate

Before calling a journey ready:
- clean starting data
- real interaction
- visible backend result
- understandable error path
- no exposed credentials
- authorized synthetic/test records only
- known working build preserved
- backup screenshots/recording available

## Open questions for Paul

1. Which exact kiosk journey must be interactive Monday?
2. Which exact CRM Companion action must be live?
3. Is Monday browser-only, Electron, or both?
4. Is map/WebGL required Monday or broader direction?
5. Which integrations/credentials are approved for the demo?
6. What is the Monday meeting time zone?
