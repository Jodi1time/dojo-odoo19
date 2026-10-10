"""Run only with the license holder's privately installed EB Gym addon."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestGymBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        company = cls.env.company
        cls.member = cls.env["dojo.member"].create({"name": "Synthetic bridge student", "company_id": company.id, "membership_state": "active"})
        cls.template = cls.env["dojo.class.template"].create({"name": "Bridge class", "company_id": company.id, "auto_enroll_members": False})
        cls.session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": company.id,
            "start_datetime": fields.Datetime.now() + timedelta(days=2), "end_datetime": fields.Datetime.now() + timedelta(days=2, hours=1), "state": "open"})
        plan = cls.env["product.template"].create({"name": "Synthetic gym plan", "type": "service", "company_id": company.id,
            "is_gym_membership_plan": True, "gym_duration_value": 30, "gym_duration_uom": "days"})
        cls.membership = cls.env["gym.membership"].create({"partner_id": cls.member.partner_id.id, "plan_id": plan.id,
            "company_id": company.id, "start_date": fields.Date.today(), "state": "active"})
        cls.link = cls.env["dojo.gym.link"].create({"member_id": cls.member.id, "membership_id": cls.membership.id,
            "template_ids": [(6, 0, [cls.template.id])]})

    def attendance(self):
        return self.env["dojo.attendance.log"].create({"member_id": self.member.id, "session_id": self.session.id,
            "checkin_datetime": fields.Datetime.now() - timedelta(minutes=5), "status": "present"})

    def test_mapping_requires_same_real_partner(self):
        other = self.env["dojo.member"].create({"name": "Different student", "company_id": self.env.company.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.link.member_id = other

    def test_membership_state_date_and_class_are_enforced(self):
        self.link._eligible(self.session)
        self.membership.state = "on_hold"
        with self.assertRaises(ValidationError):
            self.link._eligible(self.session)
        self.membership.state = "active"
        self.link.template_ids = [(5, 0, 0)]
        with self.assertRaises(ValidationError):
            self.link._eligible(self.session)
        self.link.template_ids = [(6, 0, [self.template.id])]
        later = self.session.start_datetime + timedelta(days=100)
        self.session.write({"start_datetime": later, "end_datetime": later + timedelta(hours=1)})
        with self.assertRaises(ValidationError):
            self.link._eligible(self.session)

    def test_checkin_and_checkout_share_gym_record(self):
        row = self.attendance()
        self.assertTrue(row.gym_attendance_id)
        self.assertEqual(row.gym_attendance_id.partner_id, self.member.partner_id)
        self.assertEqual(row.gym_attendance_id.check_in, row.checkin_datetime)
        stamp = fields.Datetime.now()
        row.checkout_datetime = stamp
        row.checkout_datetime = stamp
        self.assertEqual(row.gym_attendance_id.check_out, stamp)
        self.assertEqual(self.env["gym.attendance"].search_count([("membership_id", "=", self.membership.id)]), 1)

    def test_gym_rejection_rolls_back_dojang_attendance(self):
        self.membership.state = "on_hold"
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.attendance()
        self.assertFalse(self.env["dojo.attendance.log"].search_count([("member_id", "=", self.member.id)]))

    def test_conflicting_gym_checkout_requires_reconciliation(self):
        row = self.attendance()
        stamp = fields.Datetime.now()
        row.gym_attendance_id.check_out = stamp
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            row.checkout_datetime = stamp + timedelta(minutes=1)
        self.assertFalse(row.checkout_datetime)
        self.assertEqual(row.gym_attendance_id.check_out, stamp)
