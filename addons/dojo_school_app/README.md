# Dojang School App

Serves Paul's original school app (`Kiosk-responsive-2.html`) from Odoo and answers every `/api/*` call it makes from real Odoo records. One page is both the lobby kiosk and the staff app, as in Paul's design. After signing in, each person sees the screens for their role.

| Role | Comes from | Sees |
|---|---|---|
| Owner / Admin | `Dojo Admin` group, or a Hub grant of owner or manager | Everything |
| Front Desk | `Dojang Front Desk` group | Dashboard, Students, Check-In, Email Center |
| Instructor | `Dojo Instructor` group, or a Hub instructor grant | Check-In, Students (read-only), own Timesheets |
| Parent | Portal guardian | Redirected to the member portal (`/my/dojo`) |

The server enforces these limits on every call, not only in the menu.

## Install

1. Install the `dojo_school_app` module.
2. Make Paul's HTML available to Odoo. Use **one** of these:
   - set the environment variable `DOJANG_SCHOOL_APP_HTML=/path/to/Kiosk-responsive-2.html` on the Odoo process
   - set the system parameter `dojo_school_app.html_path` to that path
   - place the file at `addons/dojo_school_app/static/app/school-app.html` (only if the repository is private)
3. Open `/dojang` to sign in.

The page is served unmodified apart from three things done at request time:
- the old kiosk token built into the file is removed
- the school time zone comes from the Odoo company
- a small adapter script is added after Paul's code, which fills the belt and class lists from Odoo, applies role menus, and fixes a crash on Check-In when Odoo has classes today

## Kiosk tablet

Open `/dojang/kiosk/<kiosk token>` once on the tablet; the token is in Odoo under Kiosk Settings. This stores the pairing in an HttpOnly cookie and drops the token from the address bar. Alternatively, sign in as staff on the tablet and open `/dojang/kiosk-start`, which pairs the device to the school's kiosk and signs staff out.

Staff can sign in on the kiosk with **Staff Login**. **Kiosk Mode**, **Log Out** and the 15-minute idle timer all return the tablet to the lobby display.

## Instructor clock-in

Each instructor uses **their own PIN**, set in Employees → PIN. After a correct PIN, the clock-in list shows only that instructor, and the server remembers who verified for 3 minutes. A PIN shared by two instructors is refused. Five wrong tries lock the kiosk for 15 minutes.

## Email and automations

- Email sends through Odoo's mail queue. The guardian's email is used for children, and the student's own email for adults.
- If no outgoing mail server is set up, sending is refused with a clear message instead of pretending to succeed.
- Automations (birthday, membership ending, welcome, new belt, absence) are stored per school and sent by the hourly job "School app: send automated emails". Each automation is sent at most once per student and occasion.
- Note: `dojo_subscriptions` already sends its own expiry reminders 30 and 7 days before a membership ends. If both are on, families get both.

## Timesheets

Lists flagged shifts: no clock-out after 12 hours, longer than 12 hours, or closed automatically.

Admins can correct a shift, and every correction is noted on the employee. Shifts that Odoo approved automatically ("Worked days are automatically approved") can be corrected. Shifts a person approved under manual approval stay locked.

## Demo data

```
DOJANG_DEMO_SEED=1 odoo-bin shell -d <demo db> < tools/seed_school_app_demo.py
```

This creates Paul's belts, his 11 classes on his weekly timetable, families and students, five weeks of sessions, and sign-ins for each role. It refuses to run without `DOJANG_DEMO_SEED=1`, and only runs once per database.
