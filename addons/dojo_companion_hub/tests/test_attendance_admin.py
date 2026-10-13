from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestAttendanceAdministration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Attendance program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Attendance class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        now = fields.Datetime.now()
        cls.sessions = {}
        for key, hours in (("yesterday", -26), ("future", 48), ("old", -40 * 24)):
            cls.sessions[key] = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
                "start_datetime": now + timedelta(hours=hours), "end_datetime": now + timedelta(hours=hours + 1),
                "state": "open", "capacity": 10})
        plan = cls.env["dojo.subscription.plan"].create({"name": "Attendance plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Attendance active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].create({"name": "Attendance price", "currency_id": cls.company.currency_id.id})
        cls.members, cls.enrollments, cls.subscriptions = {}, {}, {}
        for key in ("paid", "second", "unpaid"):
            partner = cls.env["res.partner"].create({"name": "Attendance " + key, "company_id": cls.company.id})
            member = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": "active"})
            cls.members[key] = member
            if key != "unpaid":
                cls.subscriptions[key] = cls.env["sale.subscription"].create({"partner_id": partner.id, "company_id": cls.company.id,
                    "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id,
                    "member_id": member.id, "plan_id": plan.id})
            for session in ("yesterday", "future", "old"):
                cls.enrollments[(key, session)] = cls.env["dojo.class.enrollment"].with_context(skip_subscription_check=True).create(
                    {"session_id": cls.sessions[session].id, "member_id": member.id})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Attendance kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Attendance test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "14"})
        cls.users = {}
        for role in ("manager", "instructor", "guardian"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Attendance " + role,
                "login": "attendance-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role,
                "session_ids": [(6, 0, [cls.sessions["yesterday"].id])]})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def act(self, payload, role="manager", key=None):
        if key is None:
            self.keys += 1
            key = "attendance-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), "mark_attendance", payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def row(self, key, session="yesterday", role="manager"):
        sheet = self.read("attendance_sheet", {"sessionId": str(self.sessions[session].id)}, role=role)
        return next(r for r in sheet["rows"] if r["memberId"] == str(self.members[key].id))

    def mark(self, key, status, session="yesterday", role="manager", reason="Paper sign-in sheet", request_key=None):
        row = self.row(key, session)
        return self.act({"sessionId": str(self.sessions[session].id), "memberId": str(self.members[key].id), "status": status,
            "expectedVersion": row["version"], "reason": reason}, role=role, key=request_key)

    def log(self, key, session="yesterday"):
        return self.env["dojo.attendance.log"].search([("session_id", "=", self.sessions[session].id), ("member_id", "=", self.members[key].id)])

    def test_sheet_and_session_list_are_role_scoped(self):
        sheet = self.read("attendance_sheet", {"sessionId": str(self.sessions["yesterday"].id)})
        self.assertTrue(sheet["session"]["open"])
        self.assertTrue(sheet["canReverse"])
        self.assertEqual({r["status"] for r in sheet["rows"]}, {"pending"})
        self.assertEqual(len(sheet["rows"]), 3)
        instructor = self.read("attendance_sheet", {"sessionId": str(self.sessions["yesterday"].id)}, role="instructor")
        self.assertFalse(instructor["canReverse"])
        self.assertEqual(self.read("attendance_sheet", {"sessionId": str(self.sessions["future"].id)}, role="instructor")["error"]["code"], "SESSION_UNAVAILABLE")
        listed = self.read("attendance_sessions", {}, role="instructor")["sessions"]
        self.assertEqual([s["id"] for s in listed], [str(self.sessions["yesterday"].id)])
        self.assertNotIn(str(self.sessions["future"].id), [s["id"] for s in self.read("attendance_sessions", {})["sessions"]])
        self.assertEqual(self.read("attendance_sheet", {"sessionId": str(self.sessions["yesterday"].id)}, role="guardian")["error"]["status"], 403)

    def test_manual_present_creates_one_native_log_and_replays(self):
        first = self.mark("paid", "present", request_key="attendance-present-0001")
        self.assertEqual((first["state"], first["before"], first["after"], first["reversal"]), ("attendance_recorded", "pending", "present", False), first)
        log = self.log("paid")
        self.assertEqual(len(log), 1)
        self.assertEqual(log.status, "present")
        self.assertIn("Paper sign-in sheet", log.note)
        self.assertIn("Attendance manager", log.note)
        self.assertEqual(self.enrollments[("paid", "yesterday")].attendance_state, "present")
        replay = self.act({"sessionId": str(self.sessions["yesterday"].id), "memberId": str(self.members["paid"].id), "status": "present",
            "expectedVersion": 0, "reason": "Paper sign-in sheet"}, key="attendance-present-0001")
        self.assertEqual(replay["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.log("paid")), 1)
        details = self.read("member_details", {"memberId": str(self.members["paid"].id)})
        self.assertIn(str(log.id), [a["id"] for a in details["attendance"]])
        instructor = self.mark("second", "late", role="instructor")
        self.assertEqual(instructor["after"], "late", instructor)

    def test_same_request_replays_original_receipt(self):
        row = self.row("paid")
        payload = {"sessionId": str(self.sessions["yesterday"].id), "memberId": str(self.members["paid"].id), "status": "present",
            "expectedVersion": row["version"], "reason": "Late paper sheet"}
        first = self.act(payload, key="attendance-replay-0001")
        second = self.act(payload, key="attendance-replay-0001")
        self.assertTrue(second["replayed"])
        self.assertEqual(second["logId"], first["logId"])
        self.assertEqual(len(self.log("paid")), 1)

    def test_eligibility_and_roster_rules_apply_before_writing(self):
        result = self.mark("unpaid", "present")
        self.assertEqual(result["error"]["code"], "ELIGIBILITY_REVIEW_REQUIRED")
        self.assertFalse(self.log("unpaid"))
        self.assertEqual(self.mark("unpaid", "absent")["after"], "absent")
        stranger = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Not booked",
            "company_id": self.company.id}).id, "company_id": self.company.id, "membership_state": "active"})
        self.assertEqual(self.act({"sessionId": str(self.sessions["yesterday"].id), "memberId": str(stranger.id), "status": "present",
            "expectedVersion": 0, "reason": "x"})["error"]["code"], "NOT_ON_ROSTER")
        other = self.env["res.company"].create({"name": "Foreign attendance school"})
        foreign = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Foreign attendance",
            "company_id": other.id}).id, "company_id": other.id})
        self.assertEqual(self.act({"sessionId": str(self.sessions["yesterday"].id), "memberId": str(foreign.id), "status": "present",
            "expectedVersion": 0, "reason": "x"})["error"]["code"], "MEMBER_UNAVAILABLE")

    def test_reversal_is_manager_only_keeps_record_and_audit(self):
        self.mark("paid", "present")
        log = self.log("paid")
        self.assertEqual(self.mark("paid", "absent", role="instructor")["error"]["code"], "REVERSAL_REQUIRES_MANAGER")
        self.assertEqual(self.mark("paid", "late", role="instructor")["error"]["code"], "CORRECTION_REQUIRES_MANAGER")
        reversed_ = self.mark("paid", "absent", reason="Signed in by mistake")
        self.assertTrue(reversed_["reversal"], reversed_)
        self.assertEqual(self.log("paid"), log)
        self.assertEqual(log.status, "absent")
        self.assertEqual(self.enrollments[("paid", "yesterday")].attendance_state, "absent")
        history = self.row("paid")["corrections"]
        self.assertEqual([(h["before"], h["after"], h["reversal"]) for h in history], [("present", "absent", True), ("pending", "present", False)])
        self.assertEqual(history[0]["reason"], "Signed in by mistake")
        details = self.read("member_details", {"memberId": str(self.members["paid"].id)})
        self.assertNotIn(str(log.id), [a["id"] for a in details["attendance"]])

    def test_stale_unchanged_and_closed_windows_are_rejected(self):
        row = self.row("paid")
        self.mark("paid", "present")
        stale = self.act({"sessionId": str(self.sessions["yesterday"].id), "memberId": str(self.members["paid"].id),
            "status": "late", "expectedVersion": row["version"] - 1, "reason": "Stale"})
        self.assertEqual(stale["error"]["code"], "VERSION_CONFLICT")
        self.assertEqual(self.mark("paid", "present")["error"]["code"], "ATTENDANCE_UNCHANGED")
        self.assertEqual(self.mark("paid", "present", session="future")["error"]["code"], "ATTENDANCE_WINDOW_CLOSED")
        self.assertEqual(self.mark("paid", "present", session="old")["error"]["code"], "ATTENDANCE_WINDOW_CLOSED")
        self.assertEqual(self.act({"sessionId": str(self.sessions["yesterday"].id), "memberId": str(self.members["second"].id),
            "status": "sick", "expectedVersion": 0, "reason": "x"})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.mark("second", "present", reason="   ")["error"]["code"], "INVALID_COMMAND")

    def test_credit_hold_is_confirmed_on_attendance_and_not_refunded_by_reversal(self):
        if "dojo.credit.transaction" not in self.env:
            self.skipTest("Credits addon not installed")
        enrollment = self.enrollments[("paid", "yesterday")]
        hold = self.env["dojo.credit.transaction"].sudo().search([("enrollment_id", "=", enrollment.id), ("transaction_type", "=", "hold")])
        if not hold:
            hold = self.env["dojo.credit.transaction"].sudo().create({"transaction_type": "hold", "amount": -1, "status": "pending",
                "subscription_id": self.subscriptions["paid"].id, "member_id": self.members["paid"].id,
                "enrollment_id": enrollment.id, "session_id": self.sessions["yesterday"].id})
        hold.status = "pending"
        self.mark("paid", "absent")
        self.assertEqual(hold.status, "pending")
        result = self.mark("paid", "present", reason="Sheet corrected")
        self.assertEqual(result["creditHold"], {"before": "pending", "after": "confirmed"}, result)
        reversed_ = self.mark("paid", "absent", reason="Mistake")
        self.assertEqual(reversed_["creditHold"], {"before": "confirmed", "after": "confirmed"})
