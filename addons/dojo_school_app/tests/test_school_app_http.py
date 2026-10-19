import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import SchoolFixture

PAGE = str(Path(__file__).resolve().parent / "fixtures" / "page.html")


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
