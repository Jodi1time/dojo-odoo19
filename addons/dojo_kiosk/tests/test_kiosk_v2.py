from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestKioskSessionFirstV2(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

        cls.program = cls.env["dojo.program"].create({
            "name": "Kids Program",
            "company_id": cls.company.id,
        })

        cls.partner = cls.env["res.partner"].create({
            "name": "Maya Test",
            "email": "maya@example.test",
        })
        cls.member = cls.env["dojo.member"].create({
            "partner_id": cls.partner.id,
            "company_id": cls.company.id,
            "membership_state": "active",
        })

        cls.class_template = cls.env["dojo.class.template"].create({
            "name": "Children Advanced",
            "company_id": cls.company.id,
            "program_id": cls.program.id,
            "course_member_ids": [(6, 0, [cls.member.id])],
        })

        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({
            "template_id": cls.class_template.id,
            "company_id": cls.company.id,
            "start_datetime": now - timedelta(minutes=5),
            "end_datetime": now + timedelta(minutes=55),
            "state": "open",
            "capacity": 1,
        })

        cls.plan = cls.env["dojo.subscription.plan"].create({
            "name": "Kids Unlimited",
            "company_id": cls.company.id,
            "currency_id": cls.currency.id,
            "price": 100.0,
            "plan_type": "program",
            "program_ids": [(6, 0, [cls.program.id])],
        })

        cls.stage = cls.env["sale.subscription.stage"].create({
            "name": "Kiosk Test Active",
            "type": "in_progress",
            "in_progress": True,
        })

        cls.pricelist = cls.env["product.pricelist"].search([
            ("currency_id", "=", cls.currency.id),
        ], limit=1)
        if not cls.pricelist:
            cls.pricelist = cls.env["product.pricelist"].create({
                "name": "Kiosk Test Pricelist",
                "currency_id": cls.currency.id,
            })

        cls.subscription = cls.env["sale.subscription"].create({
            "partner_id": cls.partner.id,
            "company_id": cls.company.id,
            "template_id": cls.plan.template_id.id,
            "pricelist_id": cls.pricelist.id,
            "stage_id": cls.stage.id,
            "member_id": cls.member.id,
            "plan_id": cls.plan.id,
        })

        cls.enrollment = cls.env["dojo.class.enrollment"].create({
            "session_id": cls.session.id,
            "member_id": cls.member.id,
            "status": "registered",
            "attendance_state": "pending",
        })

        cls.kiosk = cls.env["dojo.kiosk.config"].create({
            "name": "Lobby Test Kiosk",
            "pin_code": "123456",
            "company_id": cls.company.id,
        })

        cls.service = cls.env["dojo.kiosk.service"].sudo()

    def _check_in(self, key="kiosk-test-key-0001", correlation="corr-test-key-0001"):
        return self.service.session_first_checkin(
            token=self.kiosk.kiosk_token,
            session_id=self.session.id,
            member_id=self.member.id,
            idempotency_key=key,
            correlation_id=correlation,
        )

    def test_registered_member_can_check_in_when_session_capacity_is_full(self):
        self.assertEqual(self.session.seats_taken, 1)
        self.assertEqual(self.session.capacity, 1)

        result = self._check_in()

        self.assertTrue(result["success"])
        self.assertEqual(result["receipt"]["member_id"], self.member.id)
        self.assertEqual(result["receipt"]["session_id"], self.session.id)
        self.assertEqual(
            self.env["dojo.attendance.log"].search_count([
                ("session_id", "=", self.session.id),
                ("member_id", "=", self.member.id),
            ]),
            1,
        )

    def test_same_idempotency_key_replays_without_duplicate_attendance(self):
        first = self._check_in()
        second = self._check_in(correlation="corr-test-key-0002")

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertTrue(second["replayed"])
        self.assertEqual(
            first["receipt"]["attendance_id"],
            second["receipt"]["attendance_id"],
        )
        self.assertEqual(
            self.env["dojo.attendance.log"].search_count([
                ("session_id", "=", self.session.id),
                ("member_id", "=", self.member.id),
            ]),
            1,
        )

    def test_member_not_on_selected_session_roster_is_rejected(self):
        partner = self.env["res.partner"].create({"name": "Not On Roster"})
        member = self.env["dojo.member"].create({
            "partner_id": partner.id,
            "company_id": self.company.id,
            "membership_state": "active",
        })

        result = self.service.session_first_checkin(
            token=self.kiosk.kiosk_token,
            session_id=self.session.id,
            member_id=member.id,
            idempotency_key="kiosk-test-key-0003",
            correlation_id="corr-test-key-0003",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "NOT_ON_ROSTER")

    def test_closed_session_is_rejected(self):
        self.session.write({"state": "done"})

        result = self._check_in(
            key="kiosk-test-key-0004",
            correlation="corr-test-key-0004",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "SESSION_UNAVAILABLE")

    def test_idempotency_key_cannot_be_reused_for_different_action(self):
        first = self._check_in(
            key="kiosk-test-key-0005",
            correlation="corr-test-key-0005",
        )
        self.assertTrue(first["success"])

        other_template = self.env["dojo.class.template"].create({
            "name": "Second Session",
            "company_id": self.company.id,
            "program_id": self.program.id,
            "course_member_ids": [(6, 0, [self.member.id])],
        })
        now = fields.Datetime.now()
        other_session = self.env["dojo.class.session"].create({
            "template_id": other_template.id,
            "company_id": self.company.id,
            "start_datetime": now,
            "end_datetime": now + timedelta(hours=1),
            "state": "open",
        })
        self.env["dojo.class.enrollment"].create({
            "session_id": other_session.id,
            "member_id": self.member.id,
            "status": "registered",
            "attendance_state": "pending",
        })

        result = self.service.session_first_checkin(
            token=self.kiosk.kiosk_token,
            session_id=other_session.id,
            member_id=self.member.id,
            idempotency_key="kiosk-test-key-0005",
            correlation_id="corr-test-key-0006",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "IDEMPOTENCY_CONFLICT")
