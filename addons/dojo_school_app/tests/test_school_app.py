import json
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from ..models.school_app import SchoolAppProblem
from .common import SchoolFixture


@tagged("post_install", "-at_install")
class TestSchoolAppService(SchoolFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.build_school()

    def app(self):
        return self.env["dojo.school.app"].sudo()

    def ctx(self, user):
        return self.app()._resolve(user)

    def problem(self, code, action):
        with self.assertRaises(SchoolAppProblem) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_each_person_gets_their_own_role_pages_and_home(self):
        expected = {
            self.admin: ("admin", "/dojang/app", "reports"),
            self.front_desk: ("front_desk", "/dojang/app", "checkin"),
            self.instructor: ("instructor", "/dojang/app", "timesheets"),
        }
        for user, (role, home, page) in expected.items():
            me = self.app().me(self.ctx(user))
            self.assertEqual((me["role"], me["home"]), (role, home))
            self.assertIn(page, me["pages"])
        parent = self.app().me(self.ctx(self.parent_user))
        self.assertEqual(parent["role"], "parent")
        self.assertEqual(parent["pages"], [])
        self.assertTrue(parent["home"].startswith("/my"))
        self.assertNotIn("reports", self.app().me(self.ctx(self.front_desk))["pages"])
        self.assertNotIn("dashboard", self.app().me(self.ctx(self.instructor))["pages"])
        self.assertIsNone(self.ctx(self.nobody))
        self.assertIsNone(self.ctx(self.env.ref("base.public_user")))

    def test_hub_grants_map_to_the_same_roles(self):
        site = self.env["dojo.hub.site"].create({"name": "School site", "company_id": self.company.id, "kiosk_config_id": self.kiosk.id,
            "workspace_ref": "7", "provider_origin": "https://provider.example.invalid", "webhook_secret_env": "DOJANG_HUB_SCHOOL_TEST_IN",
            "provider_token_env": "DOJANG_HUB_SCHOOL_TEST_OUT"})
        manager = self.make_user("hub-manager", ["base.group_portal"])
        coach = self.make_user("hub-coach", ["base.group_portal"])
        self.env["dojo.hub.grant"].create({"site_id": site.id, "user_id": manager.id, "role": "manager"})
        self.env["dojo.hub.grant"].create({"site_id": site.id, "user_id": coach.id, "role": "instructor",
                                           "session_ids": [(6, 0, [self.today_session.id])], "member_ids": [(6, 0, [self.maya.id])]})
        self.assertEqual(self.ctx(manager).role, "admin")
        coach_ctx = self.ctx(coach)
        self.assertEqual(coach_ctx.role, "instructor")
        self.assertEqual([row["id"] for row in self.app().checkin_sessions(coach_ctx)["sessions"]], [self.today_session.id])
        self.problem("NOT_FOUND", lambda: self.app().checkin_roster(coach_ctx, self.later_session.id))

    def test_role_limits_are_enforced_by_the_server(self):
        instructor, front_desk, parent = self.ctx(self.instructor), self.ctx(self.front_desk), self.ctx(self.parent_user)
        self.problem("NOT_ALLOWED", lambda: self.app().save_student(instructor, {"name": "New Student"}))
        self.problem("NOT_ALLOWED", lambda: self.app().archive_student(front_desk, self.maya.id))
        self.problem("NOT_ALLOWED", lambda: self.app().save_student(front_desk, {"id": self.maya.id, "status": "cancelled"}))
        self.problem("NOT_ALLOWED", lambda: self.app().dashboard(instructor))
        self.problem("NOT_ALLOWED", lambda: self.app().students(parent))
        self.assertEqual(self.problem("SIGN_IN_REQUIRED", lambda: self.app().students(None)).status, 401)

    def test_students_are_school_scoped_and_leave_out_private_notes(self):
        rows = self.app().students(self.ctx(self.front_desk))["students"]
        names = [row["name"] for row in rows]
        self.assertEqual(names, ["Devon Reed", "Jordan Lee", "Maya Chen"])
        self.assertNotIn("Private Foreign", json.dumps(rows))
        self.assertNotIn("Synthetic peanut", json.dumps(rows))
        self.assertNotIn("Synthetic medical", json.dumps(rows))
        jordan = next(row for row in rows if row["id"] == self.jordan.id)
        self.assertEqual((jordan["first_name"], jordan["last_name"], jordan["initials"]), ("Jordan", "Lee", "JL"))
        self.assertEqual((jordan["belt"], jordan["belt_color"], jordan["status"]), ("White", "#ffffff", "active"))
        self.assertEqual(jordan["guardian"]["name"], self.parent_user.name)
        self.assertEqual(jordan["date_of_birth"], "2015-03-04")
        self.assertEqual(self.app().students(self.ctx(self.admin), query="maya")["students"][0]["id"], self.maya.id)

    def test_staff_can_add_and_edit_students_with_validation(self):
        ctx = self.ctx(self.front_desk)
        created = self.app().save_student(ctx, {"first_name": "Sarah", "last_name": "Kim", "email": "sarah@example.invalid",
                                                "phone": "555-0100", "date_of_birth": "2016-01-02"})
        self.assertTrue(created["created"])
        member = self.env["dojo.member"].browse(created["student"]["id"])
        self.assertEqual((member.name, member.email, member.company_id), ("Sarah Kim", "sarah@example.invalid", self.company))
        updated = self.app().save_student(ctx, {"id": member.id, "phone": "555-0199"})
        self.assertEqual(updated["student"]["phone"], "555-0199")
        self.problem("INVALID", lambda: self.app().save_student(ctx, {"name": "Bad Email", "email": "not-an-email"}))
        future = str(fields.Date.today() + timedelta(days=3))
        self.problem("INVALID", lambda: self.app().save_student(ctx, {"name": "Future Child", "date_of_birth": future}))
        self.problem("INVALID", lambda: self.app().save_student(ctx, {"email": "x@example.invalid"}))
        self.problem("NOT_FOUND", lambda: self.app().save_student(ctx, {"id": self.foreign_member.id, "phone": "1"}))
        admin = self.ctx(self.admin)
        self.app().save_student(admin, {"id": member.id, "status": "trial"})
        self.assertEqual(member.membership_state, "trial")
        self.app().archive_student(admin, member.id)
        self.assertFalse(member.active)

    def test_dashboard_reflects_todays_real_records(self):
        self.app().checkin_mark(self.ctx(self.front_desk), self.today_session.id, self.maya.id)
        board = self.app().dashboard(self.ctx(self.admin))
        self.assertEqual(board["total_students"], 2)
        self.assertEqual(board["todays_checkins"], 1)
        self.assertEqual(board["recent_checkins"][0]["name"], "Maya Chen")
        belts = {row["belt"]: row["count"] for row in board["belt_distribution"]}
        self.assertEqual(belts, {"White": 1, "Yellow": 1})
        self.assertNotIn("Private Foreign", json.dumps(board))

    def test_staff_check_in_marks_and_undoes_with_an_audit_note(self):
        ctx = self.ctx(self.instructor)
        sessions = self.app().checkin_sessions(ctx)["sessions"]
        self.assertEqual([row["id"] for row in sessions], [self.today_session.id])
        self.assertEqual(sessions[0]["booked"], 3)
        marked = self.app().checkin_mark(ctx, self.today_session.id, self.jordan.id)
        self.assertTrue(marked["student"]["checked_in"])
        self.assertEqual(marked["warnings"], [])
        log = self.env["dojo.attendance.log"].search([("session_id", "=", self.today_session.id), ("member_id", "=", self.jordan.id)])
        self.assertEqual(len(log), 1)
        self.assertIn("Synthetic instructor", log.note)
        paused = self.app().checkin_mark(ctx, self.today_session.id, self.devon.id)
        self.assertEqual(paused["warnings"], ["MEMBERSHIP_INACTIVE"])
        roster = {row["student_id"]: row for row in self.app().checkin_roster(ctx, self.today_session.id)["roster"]}
        self.assertTrue(roster[self.jordan.id]["checked_in"])
        self.assertFalse(roster[self.maya.id]["checked_in"])
        undone = self.app().checkin_undo(ctx, self.today_session.id, self.jordan.id)
        self.assertTrue(undone["undone"])
        self.assertFalse(log.exists())
        self.assertIn("Check-in undone", self.jordan.message_ids[0].body)
        self.problem("INVALID", lambda: self.app().checkin_mark(ctx, self.today_session.id, self.maya.id, "teleported"))
        self.problem("NOT_FOUND", lambda: self.app().checkin_mark(ctx, self.foreign_session.id, self.maya.id))

    def test_only_admins_undo_another_days_check_in(self):
        self.app().checkin_mark(self.ctx(self.admin), self.later_session.id, self.maya.id)
        self.problem("UNDO_WINDOW", lambda: self.app().checkin_undo(self.ctx(self.front_desk), self.later_session.id, self.maya.id))
        self.assertTrue(self.app().checkin_undo(self.ctx(self.admin), self.later_session.id, self.maya.id)["undone"])

    def test_student_attendance_history(self):
        self.app().checkin_mark(self.ctx(self.admin), self.today_session.id, self.maya.id, "late")
        history = self.app().student_attendance(self.ctx(self.front_desk), self.maya.id)
        self.assertEqual(history["total_attended"], 1)
        self.assertEqual(history["attendance"][0]["status"], "late")
        self.assertEqual(history["attendance"][0]["class"], "Kids Taekwondo")

    def test_kiosk_shows_todays_classes_and_short_names_only(self):
        sessions = self.app().display_sessions(self.kiosk)["sessions"]
        self.assertEqual([row["id"] for row in sessions], [self.today_session.id])
        roster = self.app().display_roster(self.kiosk, self.today_session.id)["roster"]
        self.assertEqual([row["name"] for row in roster], ["Devon R.", "Jordan L.", "Maya C."])
        self.assertNotIn("jordan@example.invalid", json.dumps(roster))
        self.assertNotIn("Lee", json.dumps(roster))
        self.problem("NOT_FOUND", lambda: self.app().display_roster(self.kiosk, self.foreign_session.id))
        self.problem("NOT_FOUND", lambda: self.app().display_roster(self.kiosk, self.later_session.id))
        self.problem("KIOSK_NOT_PAIRED", lambda: self.app()._kiosk("not-a-real-kiosk-token"))

    def test_kiosk_check_in_uses_native_eligibility(self):
        result = self.app().display_checkin(self.kiosk, self.today_session.id, self.maya.id)
        self.assertTrue(result["checked_in"])
        self.assertFalse(result["already"])
        self.assertEqual((result["name"], result["class"]), ("Maya", "Kids Taekwondo"))
        self.assertTrue(self.app().display_checkin(self.kiosk, self.today_session.id, self.maya.id)["already"])
        self.assertEqual(self.env["dojo.attendance.log"].search_count([("session_id", "=", self.today_session.id),
                                                                      ("member_id", "=", self.maya.id)]), 1)
        self.problem("SEE_FRONT_DESK", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, self.devon.id))
        stranger = self.make_member("Walk In", "active", subscribed=True)
        self.problem("NOT_ON_ROSTER", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, stranger.id))
        self.problem("NOT_FOUND", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, self.foreign_member.id))

    def test_instructor_pin_identifies_one_instructor_and_locks_out(self):
        verified = self.app().display_instructor_verify(self.kiosk, "246810")
        self.assertEqual(verified["instructor"]["name"], "Master Kim")
        self.assertFalse(verified["clocked_in"])
        shift = self.app().display_instructor_clock(self.kiosk, "246810", "in")
        self.assertTrue(shift["clocked_in"])
        self.assertEqual(self.env["hr.attendance"].search_count([("employee_id", "=", self.profile.employee_id.id),
                                                                ("check_out", "=", False)]), 1)
        self.problem("ALREADY_CLOCKED_IN", lambda: self.app().display_instructor_clock(self.kiosk, "246810", "in"))
        self.assertFalse(self.app().display_instructor_clock(self.kiosk, "246810", "out")["clocked_in"])
        self.problem("NOT_CLOCKED_IN", lambda: self.app().display_instructor_clock(self.kiosk, "246810", "out"))
        for attempt in range(4):
            self.assertEqual(self.app().display_instructor_verify(self.kiosk, "111111")["code"], "PIN_INCORRECT")
        locked = self.app().display_instructor_verify(self.kiosk, "111111")
        self.assertEqual((locked["code"], locked["_status"]), ("PIN_LOCKED", 429))
        self.assertEqual(self.app().display_instructor_verify(self.kiosk, "246810")["code"], "PIN_LOCKED")

    def test_shared_instructor_pins_are_refused(self):
        other = self.make_user("second-coach", ["base.group_user", "dojo_core.group_dojo_instructor"])
        profile = self.env["dojo.instructor.profile"].create({"name": "Sarah Park", "user_id": other.id,
                                                              "partner_id": other.partner_id.id, "company_id": self.company.id})
        profile.employee_id.sudo().pin = "246810"
        self.assertEqual(self.app().display_instructor_verify(self.kiosk, "246810")["code"], "PIN_SHARED")
        self.assertEqual(self.app().display_instructor_verify(self.kiosk, "246810", profile.id)["instructor"]["name"], "Sarah Park")


    def test_timesheets_show_own_shifts_to_instructors_and_all_to_admins(self):
        other = self.make_user("coach-two", ["base.group_user", "dojo_core.group_dojo_instructor"])
        second = self.env["dojo.instructor.profile"].create({"name": "Sarah Park", "user_id": other.id,
                                                             "partner_id": other.partner_id.id, "company_id": self.company.id})
        self.company.attendance_validation = "manual_validation"
        now = fields.Datetime.now()
        mine = self.env["hr.attendance"].create({"employee_id": self.profile.employee_id.id,
                                                 "check_in": now - timedelta(hours=3), "check_out": now - timedelta(hours=1)})
        self.env["hr.attendance"].create({"employee_id": second.employee_id.id,
                                          "check_in": now - timedelta(hours=5), "check_out": now - timedelta(hours=4)})
        own = self.app().timesheets(self.ctx(self.instructor))
        self.assertEqual([row["instructor"] for row in own["shifts"]], ["Master Kim"])
        self.assertFalse(own["can_correct"])
        everyone = self.app().timesheets(self.ctx(self.admin))
        self.assertEqual(sorted(row["instructor"] for row in everyone["shifts"]), ["Master Kim", "Sarah Park"])
        self.problem("NOT_ALLOWED", lambda: self.app().correct_timesheet(self.ctx(self.instructor), {"id": mine.id, "reason": "x"}))
        self.problem("NOT_ALLOWED", lambda: self.app().timesheets(self.ctx(self.front_desk)))
        fixed_out = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        corrected = self.app().correct_timesheet(self.ctx(self.admin), {"id": mine.id, "check_out": fixed_out, "reason": "Forgot to clock out"})
        self.assertEqual(corrected["reason"], "Forgot to clock out")
        self.assertAlmostEqual(mine.worked_hours, 2.5, places=1)
        self.assertIn("Forgot to clock out", self.profile.employee_id.message_ids[0].body)
        self.problem("INVALID", lambda: self.app().correct_timesheet(self.ctx(self.admin), {"id": mine.id, "reason": ""}))

    def test_reports_count_real_attendance(self):
        self.today_session.instructor_profile_id = self.profile
        admin = self.ctx(self.admin)
        self.app().checkin_mark(admin, self.today_session.id, self.maya.id)
        self.app().checkin_mark(admin, self.today_session.id, self.jordan.id, "late")
        report = self.app().attendance_report(admin)
        self.assertEqual(report["totals"], {"sessions": 1, "attended": 2, "students": 2})
        self.assertEqual(report["by_class"][0], {"name": "Kids Taekwondo", "sessions": 1, "attended": 2, "average": 2.0})
        self.assertEqual(report["by_instructor"][0]["name"], "Master Kim")
        self.assertEqual(sum(row["attended"] for row in report["peak_times"]), 2)
        self.assertEqual(len(report["daily"]), 30)
        workload = self.app().instructor_workload(admin)
        kim = next(row for row in workload["instructors"] if row["name"] == "Master Kim")
        self.assertEqual((kim["classes"], kim["students_taught"]), (1, 2))
        self.problem("NOT_ALLOWED", lambda: self.app().attendance_report(self.ctx(self.front_desk)))
        self.problem("INVALID", lambda: self.app().attendance_report(admin, "2026-02-01", "2026-01-01"))

    def test_promotions_follow_belt_order_and_undo_only_the_latest(self):
        self.yellow.attendance_threshold = 1
        admin = self.ctx(self.admin)
        self.app().checkin_mark(admin, self.today_session.id, self.jordan.id)
        rows = {row["student_id"]: row for row in self.app().promotion_eligibility(admin)["students"]}
        self.assertEqual((rows[self.jordan.id]["status"], rows[self.jordan.id]["next_belt"]), ("eligible", "Yellow"))
        self.assertEqual(rows[self.maya.id]["status"], "top_rank")
        result = self.app().promote(admin, {"student_ids": [self.jordan.id, self.maya.id]})
        self.assertEqual([(row["name"], row["from"], row["to"]) for row in result["promoted"]], [("Jordan Lee", "White", "Yellow")])
        self.assertEqual(result["skipped"], [{"student_id": self.maya.id, "name": "Maya Chen", "reason": "TOP_RANK"}])
        self.assertEqual(self.jordan.current_rank_id, self.yellow)
        history = self.app().promotion_history(admin, student_id=self.jordan.id)["history"]
        self.assertEqual([(row["belt"], row["can_undo"]) for row in history], [("Yellow", True), ("White", False)])
        self.problem("UNDO_NOT_LATEST", lambda: self.app().promotion_undo(admin, {"id": history[1]["id"]}))
        undone = self.app().promotion_undo(admin, {"id": history[0]["id"], "reason": "Promoted by mistake"})
        self.assertEqual((undone["from"], undone["to"]), ("Yellow", "White"))
        self.assertEqual(self.jordan.current_rank_id, self.white)
        self.assertTrue(self.app().promotion_history(admin, student_id=self.jordan.id)["history"][0]["undone"])
        self.problem("NOT_ALLOWED", lambda: self.app().promote(self.ctx(self.front_desk), {"student_id": self.jordan.id}))
        self.problem("INVALID", lambda: self.app().promote(admin, {"student_ids": [self.jordan.id, self.maya.id], "rank_id": self.yellow.id}))


@tagged("post_install", "-at_install")
class TestSchoolAppHttp(SchoolFixture, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.build_school()

    def call(self, path, payload=None, headers=None):
        if payload is None:
            response = self.url_open(path, headers=headers)
        else:
            response = self.url_open(path, data=json.dumps(payload), headers={"Content-Type": "application/json", **(headers or {})})
        return response.status_code, response.json()

    def test_login_me_and_logout_round_trip(self):
        status, body = self.call("/api/auth/me")
        self.assertEqual((status, body["authenticated"]), (200, False))
        status, body = self.call("/api/auth/login", {"login": "school-frontdesk@example.invalid", "password": "wrong"})
        self.assertEqual((status, body["code"]), (401, "LOGIN_FAILED"))
        status, body = self.call("/api/auth/login", {"login": "school-frontdesk@example.invalid", "password": "Synthetic-frontdesk-pass1"})
        self.assertEqual((status, body["role"]), (200, "front_desk"))
        status, body = self.call("/api/auth/me")
        self.assertEqual(body["role"], "front_desk")
        status, body = self.call("/api/admin/students")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["students"]), 3)
        status, body = self.call("/api/auth/logout", {})
        self.assertEqual(status, 200)
        status, body = self.call("/api/admin/students")
        self.assertEqual((status, body["code"]), (401, "SIGN_IN_REQUIRED"))

    def test_accounts_without_a_dojang_role_are_turned_away(self):
        status, body = self.call("/api/auth/login", {"login": "school-nobody@example.invalid", "password": "Synthetic-nobody-pass1"})
        self.assertEqual((status, body["code"]), (403, "NO_ROLE"))
        self.assertFalse(self.call("/api/auth/me")[1]["authenticated"])

    def test_writes_require_json_from_the_same_site(self):
        self.authenticate("school-admin@example.invalid", "Synthetic-admin-pass1")
        form = self.url_open("/api/admin/students/save", data={"name": "Form Post"})
        self.assertEqual(form.status_code, 415)
        status, body = self.call("/api/admin/students/save", {"name": "Cross Site"}, {"Origin": "https://evil.example.invalid"})
        self.assertEqual(status, 403)
        status, body = self.call("/api/admin/students/save", {"name": "Same Site"})
        self.assertEqual((status, body["created"]), (200, True))

    def test_kiosk_pairing_cookie_drives_the_public_screens(self):
        status, body = self.call("/api/display/sessions")
        self.assertEqual((status, body["code"]), (401, "KIOSK_NOT_PAIRED"))
        paired = self.url_open("/dojang/kiosk/" + self.kiosk.kiosk_token, allow_redirects=False)
        self.assertEqual(paired.status_code, 303)
        self.assertTrue(paired.headers["Location"].endswith("/dojang/kiosk"))
        self.assertIn("HttpOnly", paired.headers["Set-Cookie"])
        status, body = self.call("/api/display/sessions")
        self.assertEqual([row["id"] for row in body["sessions"]], [self.today_session.id])
        status, body = self.call("/api/display/checkin", {"session_id": self.today_session.id, "student_id": self.maya.id})
        self.assertEqual((status, body["checked_in"], body["name"]), (200, True, "Maya"))
        status, body = self.call("/api/display/instructor/verify-pin", {"pin": "000000"})
        self.assertEqual((status, body["code"]), (403, "PIN_INCORRECT"))
        attempt = self.env["dojo.kiosk.pin.attempt"].sudo().search([("config_id", "=", self.kiosk.id)])
        self.assertEqual(attempt.failed_attempts, 1)

    def test_kiosk_mode_signs_staff_out_before_showing_the_kiosk(self):
        self.authenticate("school-frontdesk@example.invalid", "Synthetic-frontdesk-pass1")
        started = self.url_open("/dojang/kiosk/start", allow_redirects=False)
        self.assertEqual(started.status_code, 303)
        self.assertIn("dojang_kiosk=", started.headers["Set-Cookie"])
        self.assertFalse(self.call("/api/auth/me")[1]["authenticated"])
        status, body = self.call("/api/display/sessions")
        self.assertEqual(status, 200)

    def test_landing_sends_each_role_home(self):
        landing = self.url_open("/dojang", allow_redirects=False)
        self.assertTrue(landing.headers["Location"].endswith("/dojang/app"))
        self.authenticate("school-parent@example.invalid", "Synthetic-parent-pass1")
        landing = self.url_open("/dojang", allow_redirects=False)
        self.assertIn("/my", landing.headers["Location"])

    def test_timesheet_correction_and_promotion_routes(self):
        self.company.attendance_validation = "manual_validation"
        now = fields.Datetime.now()
        shift = self.env["hr.attendance"].create({"employee_id": self.profile.employee_id.id,
                                                  "check_in": now - timedelta(hours=2), "check_out": now - timedelta(hours=1)})
        self.authenticate("school-admin@example.invalid", "Synthetic-admin-pass1")
        response = self.url_open("/api/admin/instructor-timesheets", data=json.dumps({"id": shift.id, "reason": "Checked"}),
                                 headers={"Content-Type": "application/json"}, method="PATCH")
        self.assertEqual(response.status_code, 200)
        status, body = self.call("/api/admin/instructor-timesheets?from=" + str((now - timedelta(hours=2)).date()))
        self.assertEqual((status, len(body["shifts"])), (200, 1))
        status, body = self.call("/api/admin/promotions/promote", {"student_id": self.jordan.id})
        self.assertEqual((status, body["promoted"][0]["to"]), (200, "Yellow"))
        status, body = self.call("/api/admin/reports/promotion-eligibility")
        self.assertEqual(status, 200)
