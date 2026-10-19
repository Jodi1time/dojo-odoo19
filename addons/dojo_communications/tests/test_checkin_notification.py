from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCheckinNotification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        company = cls.env.company
        company.name = "My Company"
        cls.guardian = cls.env["res.partner"].create({"name": "Grace Lee", "phone": "+15555550100", "company_id": company.id})
        household = cls.env["res.partner"].create({"name": "Lee Household", "is_household": True,
                                                    "primary_guardian_id": cls.guardian.id, "company_id": company.id})
        child = cls.env["res.partner"].create({"name": "Jordan Lee", "parent_id": household.id, "company_id": company.id})
        cls.member = cls.env["dojo.member"].create({"partner_id": child.id, "company_id": company.id, "membership_state": "active"})
        template = cls.env["dojo.class.template"].create({"name": "PEE WEE", "company_id": company.id})
        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({"template_id": template.id, "company_id": company.id, "state": "open",
                                                            "start_datetime": now, "end_datetime": now + timedelta(hours=1)})

    def test_guardian_gets_a_plain_text_sms_on_check_in(self):
        sms_model = type(self.env["sms.sms"])
        with patch.object(sms_model, "send", autospec=True) as send:
            log = self.env["dojo.attendance.log"].create({"session_id": self.session.id, "member_id": self.member.id, "status": "present"})
        sms = self.env["sms.sms"].search([("partner_id", "=", self.guardian.id)])
        self.assertEqual(len(sms), 1)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(sms.body.startswith("Jordan Lee checked in to PEE WEE at "), sms.body)
        self.assertNotIn("{{", sms.body)
        self.assertNotIn("<", sms.body)
        self.assertTrue(log.notification_sent)

    def test_email_shows_the_real_student_class_and_time(self):
        with patch.object(type(self.env["sms.sms"]), "send", autospec=True):
            log = self.env["dojo.attendance.log"].create({"session_id": self.session.id, "member_id": self.member.id, "status": "late"})
        template = self.env.ref("dojo_communications.mail_template_checkin_email")
        subject = template._render_field("subject", [log.id])[log.id]
        body = str(template._render_field("body_html", [log.id])[log.id])
        self.assertEqual(subject, "\u2705 Jordan Lee just checked in")
        for text in ("Jordan Lee", "PEE WEE", "Late", "My Company"):
            self.assertIn(text, body)
        self.assertNotIn("{{", body)

    def test_absence_sends_nothing(self):
        sms_model = type(self.env["sms.sms"])
        with patch.object(sms_model, "send", autospec=True):
            log = self.env["dojo.attendance.log"].create({"session_id": self.session.id, "member_id": self.member.id, "status": "absent"})
        self.assertFalse(self.env["sms.sms"].search_count([("partner_id", "=", self.guardian.id)]))
        self.assertFalse(log.notification_sent)
