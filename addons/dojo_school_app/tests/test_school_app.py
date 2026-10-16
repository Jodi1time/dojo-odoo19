import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from ..models.school_app import SchoolAppProblem
from .common import SchoolFixture

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
PAGE = str(Path(__file__).resolve().parent / "fixtures" / "page.html")


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

    def mail_server(self):
        self.env["ir.mail_server"].create({"name": "Synthetic SMTP", "smtp_host": "smtp.example.invalid"})

    def test_each_person_gets_their_own_pages_and_home(self):
        for user, role, page, missing in ((self.admin, "admin", "reports", None), (self.front_desk, "front_desk", "email", "reports"),
                                          (self.instructor, "instructor", "timesheets", "dashboard")):
            me = self.app().me(self.ctx(user))
            self.assertEqual((me["role"], me["home"]), (role, "/dojang/app"))
            self.assertIn(page, me["pages"])
            if missing:
                self.assertNotIn(missing, me["pages"])
        parent = self.app().me(self.ctx(self.parent_user))
        self.assertEqual((parent["role"], parent["pages"]), ("parent", []))
        self.assertTrue(parent["home"].startswith("/my"))
        self.assertIsNone(self.ctx(self.nobody))
        self.assertIsNone(self.ctx(self.env.ref("base.public_user")))
        self.assertEqual(self.problem("SIGN_IN_REQUIRED", lambda: self.app().me(None)).status, 401)

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
        self.problem("NOT_ALLOWED", lambda: self.app().save_student(instructor, {"first": "New", "last": "Student"}))
        self.problem("NOT_ALLOWED", lambda: self.app().archive_student(front_desk, self.maya.id))
        self.problem("BELT_ADMIN_ONLY", lambda: self.app().save_student(front_desk, {"id": self.maya.id, "first": "Maya", "last": "Chen", "belt": "White"}))
        self.problem("NOT_ALLOWED", lambda: self.app().email_history(instructor))
        for action in (self.app().automations, self.app().attendance_report, self.app().promotion_history, self.app().test_rosters):
            self.problem("NOT_ALLOWED", lambda: action(front_desk))
        self.problem("NOT_ALLOWED", lambda: self.app().students(parent))
        self.assertEqual(self.problem("SIGN_IN_REQUIRED", lambda: self.app().students(None)).status, 401)

    def test_students_match_the_page_and_leave_out_private_notes(self):
        rows = self.app().students(self.ctx(self.front_desk))["students"]
        self.assertEqual([row["first"] + " " + row["last"] for row in rows], ["Devon Reed", "Jordan Lee", "Maya Chen"])
        payload = json.dumps(rows)
        for hidden in ("Private Foreign", "Synthetic peanut", "Synthetic medical"):
            self.assertNotIn(hidden, payload)
        jordan = next(row for row in rows if row["id"] == str(self.jordan.id))
        self.assertEqual((jordan["belt"], jordan["class"], jordan["dob"]), ("White", ["PEE WEE"], "2015-03-04"))
        self.assertEqual((jordan["parent_name"], jordan["parent_email"]), (self.parent_user.name, "grace@example.invalid"))
        self.assertIsNone(jordan["photo_url"])
        self.assertEqual(self.app().catalog(self.ctx(self.instructor)), {"belts": ["White", "Yellow"], "classes": ["BLACK BELT", "PEE WEE"]})

    def test_staff_add_and_edit_students_like_the_old_form(self):
        ctx = self.ctx(self.front_desk)
        saved = self.app().save_student(ctx, {"first": "Sarah", "last": "Kim", "dob": "2016-01-02", "belt": "Yellow", "class": "PEE WEE, BLACK BELT",
                                              "phone": "555-0101", "email": "", "parent_name": "Jin Kim", "parent_phone": "555-0102",
                                              "parent_email": "jin@example.invalid", "emergency_name": "Ana Kim", "emergency_phone": "555-0103",
                                              "expdate": None, "notes": "Prefers morning classes"})
        self.assertTrue(saved["created"])
        member = self.env["dojo.member"].browse(int(saved["student"]["id"]))
        self.assertEqual((member.name, member.current_rank_id, member.company_id), ("Sarah Kim", self.yellow, self.company))
        self.assertTrue(member.partner_id.is_student)
        self.assertEqual(sorted(saved["student"]["class"]), ["BLACK BELT", "PEE WEE"])
        household = member.partner_id.parent_id
        self.assertTrue(household.is_household and household.primary_guardian_id.is_guardian)
        self.assertEqual(household.primary_guardian_id.email, "jin@example.invalid")
        self.assertEqual((saved["student"]["emergency_name"], saved["student"]["notes"]), ("Ana Kim", "Prefers morning classes"))
        edited = self.app().save_student(ctx, {"id": member.id, "first": "Sarah", "last": "Kim", "belt": "Yellow", "class": ["PEE WEE"],
                                               "phone": "555-0199", "parent_name": "Jin Kim"})
        self.assertEqual((edited["student"]["phone"], edited["student"]["class"]), ("555-0199", ["PEE WEE"]))
        self.problem("UNKNOWN_BELT", lambda: self.app().save_student(ctx, {"first": "A", "last": "B", "belt": "Plaid"}))
        self.problem("UNKNOWN_CLASS", lambda: self.app().save_student(ctx, {"first": "A", "last": "B", "class": "Underwater"}))
        self.problem("INVALID", lambda: self.app().save_student(ctx, {"first": "A", "last": "B", "email": "nope"}))
        future = str(fields.Date.today() + timedelta(days=3))
        self.problem("INVALID", lambda: self.app().save_student(ctx, {"first": "A", "last": "B", "dob": future}))
        self.problem("NOT_FOUND", lambda: self.app().save_student(ctx, {"id": self.foreign_member.id, "first": "A", "last": "B"}))
        admin = self.ctx(self.admin)
        self.app().save_student(admin, {"id": member.id, "first": "Sarah", "last": "Kim", "belt": "White"})
        self.assertEqual(member.current_rank_id, self.white)
        self.app().archive_student(admin, str(member.id))
        self.assertFalse(member.active)

    def test_membership_end_date_follows_the_active_subscription(self):
        ctx = self.ctx(self.admin)
        end = str(fields.Date.today() + timedelta(days=20))
        saved = self.app().save_student(ctx, {"id": self.maya.id, "first": "Maya", "last": "Chen", "belt": "Yellow", "expdate": end})
        self.assertEqual(saved["student"]["expdate"], end)
        self.assertEqual(saved["warnings"], [])

    def test_photos_upload_and_serve(self):
        result = self.app().save_student_photo(self.ctx(self.front_desk), {"id": self.maya.id, "image": PNG})
        self.assertTrue(result["photo_url"].startswith("/api/admin/students/photo?id=%d" % self.maya.id))
        self.assertTrue(self.app().student_photo(self.ctx(self.instructor), self.maya.id).content.startswith(b"\x89PNG"))
        self.problem("BAD_IMAGE", lambda: self.app().save_student_photo(self.ctx(self.front_desk), {"id": self.maya.id, "image": "data:text/html;base64,PGI+"}))
        self.problem("NOT_ALLOWED", lambda: self.app().save_student_photo(self.ctx(self.instructor), {"id": self.maya.id, "image": PNG}))

    def test_staff_check_in_marks_and_undoes_with_an_audit_note(self):
        ctx = self.ctx(self.instructor)
        sessions = self.app().checkin_sessions(ctx)["sessions"]
        self.assertEqual([(row["id"], row["template_name"]) for row in sessions], [(self.today_session.id, "PEE WEE")])
        self.assertEqual(sessions[0]["start"], fields.Datetime.to_string(self.today_session.start_datetime))
        self.assertTrue(self.app().checkin_mark(ctx, self.today_session.id, str(self.jordan.id))["checked_in"])
        log = self.env["dojo.attendance.log"].search([("session_id", "=", self.today_session.id), ("member_id", "=", self.jordan.id)])
        self.assertIn("Synthetic instructor", log.note)
        self.assertEqual(self.app().checkin_mark(ctx, self.today_session.id, self.devon.id)["warnings"], ["MEMBERSHIP_INACTIVE"])
        roster = {row["member_id"]: row for row in self.app().checkin_roster(ctx, self.today_session.id)["students"]}
        self.assertTrue(roster[self.jordan.id]["checked_in"])
        self.assertFalse(roster[self.maya.id]["checked_in"])
        self.assertTrue(self.app().checkin_undo(ctx, self.today_session.id, self.jordan.id)["undone"])
        self.assertFalse(log.exists())
        self.assertIn("Check-in undone", self.jordan.message_ids[0].body)
        self.problem("NOT_FOUND", lambda: self.app().checkin_mark(ctx, self.foreign_session.id, self.maya.id))
        self.app().checkin_mark(self.ctx(self.admin), self.later_session.id, self.maya.id)
        self.problem("UNDO_WINDOW", lambda: self.app().checkin_undo(self.ctx(self.front_desk), self.later_session.id, self.maya.id))

    def test_attendance_history_last_and_range(self):
        admin = self.ctx(self.admin)
        self.app().checkin_mark(admin, self.today_session.id, self.maya.id)
        history = self.app().student_attendance(self.ctx(self.front_desk), self.maya.id, "10")["attendance"]
        self.assertEqual((history[0]["class"], history[0]["student_id"]), ("PEE WEE", str(self.maya.id)))
        self.assertTrue(history[0]["time"] and history[0]["class_time"])
        self.assertIn(str(self.maya.id), self.app().attendance_last(admin)["lastCheckins"])
        since = str(fields.Date.today() - timedelta(days=1))
        self.assertEqual(len(self.app().attendance_range(admin, since, "5000")["attendance"]), 1)

    def test_kiosk_shows_today_and_first_names_only(self):
        sessions = self.app().display_sessions(self.kiosk)["sessions"]
        self.assertEqual([row["id"] for row in sessions], [self.today_session.id])
        students = self.app().display_roster(self.kiosk, self.today_session.id)["students"]
        self.assertEqual([row["name"] for row in students], ["Devon R.", "Jordan L.", "Maya C."])
        self.assertNotIn("maya@example.invalid", json.dumps(students))
        self.problem("NOT_FOUND", lambda: self.app().display_roster(self.kiosk, self.foreign_session.id))
        self.problem("NOT_FOUND", lambda: self.app().display_roster(self.kiosk, self.later_session.id))
        self.problem("KIOSK_NOT_PAIRED", lambda: self.app()._kiosk("not-a-real-kiosk-token"))

    def test_kiosk_check_in_uses_native_eligibility(self):
        result = self.app().display_checkin(self.kiosk, self.today_session.id, str(self.maya.id))
        self.assertEqual((result["name"], result["already"]), ("Maya", False))
        self.assertTrue(self.app().display_checkin(self.kiosk, self.today_session.id, self.maya.id)["already"])
        self.problem("SEE_FRONT_DESK", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, self.devon.id))
        stranger = self.make_member("Walk In", "active", subscribed=True)
        self.problem("NOT_ON_ROSTER", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, stranger.id))
        self.problem("NOT_FOUND", lambda: self.app().display_checkin(self.kiosk, self.today_session.id, self.foreign_member.id))

    def test_instructor_pin_identifies_one_person_and_locks_out(self):
        profile, payload = self.app().display_verify_pin(self.kiosk, "246810")
        self.assertEqual((profile, payload), (self.profile, {"verified": True}))
        listed = self.app().display_instructors(self.kiosk, profile.id)["instructors"]
        self.assertEqual([(row["id"], row["currently_clocked_in"]) for row in listed], [(self.profile.id, False)])
        self.assertEqual(self.app().display_instructors(self.kiosk, None)["instructors"], [])
        self.assertTrue(self.app().display_clock(self.kiosk, profile.id, profile.id, "in")["clocked_in"])
        self.problem("ALREADY_CLOCKED_IN", lambda: self.app().display_clock(self.kiosk, profile.id, profile.id, "in"))
        self.assertFalse(self.app().display_clock(self.kiosk, profile.id, str(profile.id), "out")["clocked_in"])
        self.problem("PIN_AGAIN", lambda: self.app().display_clock(self.kiosk, None, profile.id, "in"))
        self.problem("PIN_AGAIN", lambda: self.app().display_clock(self.kiosk, profile.id, profile.id + 999, "in"))
        for attempt in range(4):
            self.assertEqual(self.app().display_verify_pin(self.kiosk, "111111")[1]["_status"], 401)
        locked = self.app().display_verify_pin(self.kiosk, "111111")[1]
        self.assertEqual((locked["code"], locked["_status"]), ("PIN_LOCKED", 429))
        self.assertEqual(self.app().display_verify_pin(self.kiosk, "246810")[1]["code"], "PIN_LOCKED")

    def test_shared_instructor_pins_are_refused(self):
        other = self.make_user("second-coach", ["base.group_user", "dojo_core.group_dojo_instructor"])
        twin = self.env["dojo.instructor.profile"].create({"name": "Sarah Park", "user_id": other.id,
                                                           "partner_id": other.partner_id.id, "company_id": self.company.id})
        twin.employee_id.sudo().pin = "246810"
        profile, payload = self.app().display_verify_pin(self.kiosk, "246810")
        self.assertEqual((profile, payload["code"]), (None, "PIN_SHARED"))

    def test_timesheets_flag_problem_shifts_and_follow_approval_rules(self):
        now = fields.Datetime.now()
        Attendance = self.env["hr.attendance"]
        forgot = Attendance.create({"employee_id": self.profile.employee_id.id, "check_in": now - timedelta(hours=20)})
        rows = self.app().timesheets(self.ctx(self.instructor))["timesheets"]
        self.assertEqual([row["id"] for row in rows], [forgot.id])
        self.assertEqual(rows[0]["instructor_name"], "Master Kim")
        self.problem("NOT_ALLOWED", lambda: self.app().correct_timesheet(self.ctx(self.instructor), forgot.id, {"check_out": "x"}))
        self.problem("NOT_ALLOWED", lambda: self.app().timesheets(self.ctx(self.front_desk)))
        closing = (now - timedelta(hours=14)).strftime("%Y-%m-%d %H:%M:%S")
        fixed = self.app().correct_timesheet(self.ctx(self.admin), forgot.id, {"check_out": closing})["timesheet"]
        self.assertAlmostEqual(fixed["worked_hours"], 6, places=1)
        self.assertIn("Shift corrected", self.profile.employee_id.message_ids[0].body)
        self.company.attendance_validation = "manual_validation"
        approved = Attendance.create({"employee_id": self.profile.employee_id.id, "check_in": now - timedelta(hours=40),
                                      "check_out": now - timedelta(hours=38)})
        approved.sudo().write({"state": "validated"})
        self.problem("TIMESHEET_REVIEW", lambda: self.app().correct_timesheet(self.ctx(self.admin), approved.id, {"check_out": closing}))

    def test_reports_count_real_attendance(self):
        self.today_session.instructor_profile_id = self.profile
        admin = self.ctx(self.admin)
        self.app().checkin_mark(admin, self.today_session.id, self.maya.id)
        self.app().checkin_mark(admin, self.today_session.id, self.jordan.id)
        report = self.app().attendance_report(admin)
        self.assertEqual(report["by_class"], [{"name": "PEE WEE", "count": 2}])
        self.assertEqual(report["by_instructor"], [{"name": "Master Kim", "count": 2}])
        self.assertEqual(sum(row["count"] for row in report["daily"]), 2)
        self.assertEqual(sum(row["count"] for row in report["peak_times"]), 2)
        workload = self.app().instructor_workload(admin)["instructors"]
        self.assertEqual([(row["name"], row["classes_taught"]) for row in workload], [("Master Kim", 1)])
        self.yellow.attendance_threshold = 1
        eligible = self.app().promotion_eligibility(admin)["eligible"]
        self.assertEqual([(row["member_name"], row["target_rank"]) for row in eligible], [("Jordan Lee", "Yellow")])

    def test_promotions_run_in_batches_and_undo_together(self):
        admin = self.ctx(self.admin)
        result = self.app().promote(admin, {"from_belt": "White", "to_belt": "Yellow", "member_ids": [self.jordan.id, self.devon.id, self.maya.id]})
        self.assertEqual((result["promoted"], result["skipped"]), (2, ["Maya Chen"]))
        self.assertEqual((self.jordan.current_rank_id, self.devon.current_rank_id), (self.yellow, self.yellow))
        history = self.app().promotion_history(admin, "20")["history"]
        self.assertEqual((history[0]["from_belt"], history[0]["to_belt"], history[0]["promoted_count"]), ("White", "Yellow", 2))
        self.assertEqual(sorted(history[0]["student_names"]), ["Devon Reed", "Jordan Lee"])
        self.assertEqual(self.app().promotion_undo(admin, {"batch_id": history[0]["id"]})["reverted"], 2)
        self.assertEqual(self.jordan.current_rank_id, self.white)
        self.assertTrue(self.app().promotion_history(admin)["history"][0]["undone"])
        self.problem("ALREADY_UNDONE", lambda: self.app().promotion_undo(admin, {"batch_id": history[0]["id"]}))
        self.problem("UNKNOWN_BELT", lambda: self.app().promote(admin, {"from_belt": "White", "to_belt": "Plaid", "member_ids": [self.jordan.id]}))

    def test_test_rosters_are_saved_and_listed(self):
        admin = self.ctx(self.admin)
        saved = self.app().save_test_roster(admin, {"member_ids": [self.jordan.id, self.maya.id], "belt_filter": "All Belts", "class_filter": "PEE WEE"})
        self.assertEqual(saved["student_count"], 2)
        listed = self.app().test_rosters(admin, "20")["rosters"]
        self.assertEqual((listed[0]["class_filter"], listed[0]["student_names"]), ("PEE WEE", ["Jordan Lee", "Maya Chen"]))

    def test_email_needs_a_mail_server_and_reaches_the_family(self):
        ctx = self.ctx(self.front_desk)
        payload = {"member_ids": [self.jordan.id, self.maya.id, self.devon.id], "subject": "Hello {name}", "body": "Hi {name}, see you in {class}."}
        self.problem("EMAIL_NOT_SET_UP", lambda: self.app().email_mass(ctx, payload))
        self.mail_server()
        self.assertEqual(self.app().email_mass(ctx, payload), {"sent": 2, "failed": 1})
        mails = self.env["mail.mail"].search([("subject", "in", ["Hello Jordan", "Hello Maya"])])
        self.assertEqual(sorted(mails.mapped("email_to")), ["grace@example.invalid", "maya@example.invalid"])
        self.assertIn("PEE WEE", mails.filtered(lambda mail: mail.email_to == "maya@example.invalid").body_html)
        self.assertEqual(self.app().email_individual(ctx, {"member_id": self.maya.id, "subject": "Hi", "body": "Note"}), {"sent": 1, "failed": 0})
        self.assertTrue(self.app().email_followup(ctx, {"member_id": self.devon.id, "subject": "We miss you", "body": "Come back"})["recorded"])
        history = self.app().email_history(ctx, "50")["history"]
        self.assertEqual([(row["type"], row["recipient_count"]) for row in history[:3]][::-1], [("mass", 3), ("individual", 1), ("individual", 1)])
        self.assertEqual(history[0]["recipient_name"], "Devon Reed")

    def test_automation_settings_save_and_send_once(self):
        admin = self.ctx(self.admin)
        self.assertFalse(self.app().automations(admin)["bday_enabled"])
        self.problem("INVALID", lambda: self.app().save_automations(admin, {"exp_days": 500}))
        self.problem("INVALID", lambda: self.app().save_automations(admin, {"bday_time": "25:99"}))
        saved = self.app().save_automations(admin, {"bday_enabled": True, "bday_time": "00:00", "bday_subject": "Happy birthday {name}"})
        self.assertTrue(saved["bday_enabled"])
        today = self.app()._today(self.company)
        try:
            born = today.replace(year=today.year - 8)
        except ValueError:
            born = today.replace(year=today.year - 4)
        self.maya.date_of_birth = born
        self.assertEqual(self.app()._cron_automations(), 0)
        self.mail_server()
        self.assertEqual(self.app()._cron_automations(), 1)
        self.assertEqual(self.app()._cron_automations(), 0)
        self.assertTrue(self.env["mail.mail"].search_count([("subject", "=", "Happy birthday Maya"), ("email_to", "=", "maya@example.invalid")]))
        self.assertTrue(self.app().test_automation(admin, "bday")["sent"])
        self.problem("INVALID", lambda: self.app().test_automation(admin, "fireworks"))


@tagged("post_install", "-at_install")
class TestSchoolAppHttp(SchoolFixture, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.build_school()

    def setUp(self):
        super().setUp()
        page = patch.dict(os.environ, {"DOJANG_SCHOOL_APP_HTML": PAGE})
        page.start()
        self.addCleanup(page.stop)

    def call(self, path, payload=None, headers=None, method=None):
        if payload is None and method is None:
            response = self.url_open(path, headers=headers)
        else:
            response = self.url_open(path, data=json.dumps(payload or {}), method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        return response.status_code, response.json()

    def test_login_me_and_logout_round_trip(self):
        status, body = self.call("/api/auth/me")
        self.assertEqual((status, body["detail"]), (401, "Please sign in."))
        status, body = self.call("/api/auth/login", {"login": "school-frontdesk@example.invalid", "password": "wrong"})
        self.assertEqual((status, body["detail"]), (401, "Invalid credentials."))
        status, body = self.call("/api/auth/login", {"login": "school-frontdesk@example.invalid", "password": "Synthetic-frontdesk-pass1"})
        self.assertEqual((status, body["role"]), (200, "front_desk"))
        status, body = self.call("/api/admin/students")
        self.assertEqual((status, len(body["students"])), (200, 3))
        self.assertEqual(self.call("/api/auth/logout", {})[0], 200)
        self.assertEqual(self.call("/api/admin/students")[0], 401)

    def test_accounts_without_a_dojang_role_are_turned_away(self):
        status, body = self.call("/api/auth/login", {"login": "school-nobody@example.invalid", "password": "Synthetic-nobody-pass1"})
        self.assertEqual((status, body["code"]), (403, "NO_ROLE"))
        self.assertEqual(self.call("/api/auth/me")[0], 401)

    def test_writes_require_json_from_the_same_site(self):
        self.authenticate("school-admin@example.invalid", "Synthetic-admin-pass1")
        self.assertEqual(self.url_open("/api/admin/students/save", data={"first": "Form"}).status_code, 415)
        status, body = self.call("/api/admin/students/save", {"first": "Cross", "last": "Site"}, {"Origin": "https://evil.example.invalid"})
        self.assertEqual(status, 403)
        status, body = self.call("/api/admin/students/save", {"first": "Same", "last": "Site"})
        self.assertEqual((status, body["created"]), (200, True))
        status, body = self.call("/api/admin/students/save", {"first": "Bad", "last": "Belt", "belt": "Plaid"})
        self.assertEqual((status, body["detail"]), (422, "That belt isn't set up in Odoo."))

    def test_kiosk_pairing_and_instructor_pin_cookie(self):
        self.assertEqual(self.call("/api/display/sessions")[0], 401)
        status, body = self.call("/api/display/sessions", headers={"X-Device-Token": self.kiosk.kiosk_token})
        self.assertEqual((status, len(body["sessions"])), (200, 1))
        paired = self.url_open("/dojang/kiosk/" + self.kiosk.kiosk_token, allow_redirects=False)
        self.assertEqual(paired.status_code, 303)
        self.assertIn("HttpOnly", paired.headers["Set-Cookie"])
        status, body = self.call("/api/display/checkin", {"session_id": self.today_session.id, "student_id": str(self.maya.id)})
        self.assertEqual((status, body["name"]), (200, "Maya"))
        self.assertEqual(self.call("/api/display/instructors")[1]["instructors"], [])
        status, body = self.call("/api/display/instructor/verify-pin", {"pin": "000000"})
        self.assertEqual(status, 401)
        self.assertEqual(self.env["dojo.kiosk.pin.attempt"].sudo().search([("config_id", "=", self.kiosk.id)]).failed_attempts, 1)
        self.assertEqual(self.call("/api/display/instructor/verify-pin", {"pin": "246810"})[0], 200)
        instructors = self.call("/api/display/instructors")[1]["instructors"]
        self.assertEqual([row["name"] for row in instructors], ["Master Kim"])
        status, body = self.call("/api/display/instructor/clock-in", {"instructor_id": instructors[0]["id"]})
        self.assertEqual((status, body["clocked_in"]), (200, True))
        self.assertEqual(self.call("/api/display/instructor/clock-out", {"instructor_id": instructors[0]["id"]})[0], 403)

    def test_page_is_served_with_role_boot_and_without_the_old_token(self):
        page = self.url_open("/dojang/app")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("0123456789abcdef0123456789abcdef", page.text)
        self.assertIn("get('device') || ''", page.text)
        self.assertIn('const SCHOOL_TZ = "UTC";', page.text)
        self.assertIn("window.DOJANG_BOOT=", page.text)
        self.assertIn("showAppAfterLogin", page.text)
        self.assertEqual(self.url_open("/dojang/kiosk").status_code, 401)
        self.url_open("/dojang/kiosk/" + self.kiosk.kiosk_token)
        kiosk = self.url_open("/dojang/kiosk")
        self.assertIn('"kiosk": true', kiosk.text)
        logo = self.url_open("/img/uft-logo-login.png")
        self.assertEqual(logo.status_code, 200)

    def test_landing_sends_each_role_home(self):
        self.assertTrue(self.url_open("/dojang", allow_redirects=False).headers["Location"].endswith("/dojang/app"))
        self.authenticate("school-parent@example.invalid", "Synthetic-parent-pass1")
        self.assertIn("/my", self.url_open("/dojang", allow_redirects=False).headers["Location"])

    def test_timesheet_patch_route(self):
        now = fields.Datetime.now()
        shift = self.env["hr.attendance"].create({"employee_id": self.profile.employee_id.id, "check_in": now - timedelta(hours=20)})
        self.authenticate("school-admin@example.invalid", "Synthetic-admin-pass1")
        closing = (now - timedelta(hours=15)).strftime("%Y-%m-%d %H:%M:%S")
        status, body = self.call("/api/admin/instructor-timesheets/%d" % shift.id, {"check_out": closing}, method="PATCH")
        self.assertEqual((status, body["timesheet"]["check_out"]), (200, closing))
