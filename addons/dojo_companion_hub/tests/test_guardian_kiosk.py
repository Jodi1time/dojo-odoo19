import hashlib
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestGuardianKiosk(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Guardian program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Guardian class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now + timedelta(minutes=10), "end_datetime": now + timedelta(minutes=70), "state": "open", "capacity": 10})
        plan = cls.env["dojo.subscription.plan"].create({"name": "Guardian plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Guardian active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].create({"name": "Guardian price", "currency_id": cls.company.currency_id.id})
        cls.households, cls.guardians, cls.children = {}, {}, {}
        for family in ("lee", "other"):
            household = cls.env["res.partner"].create({"name": "Family " + family, "is_household": True, "company_id": cls.company.id})
            guardian = cls.env["res.partner"].create({"name": "Parent " + family.title(), "parent_id": household.id, "is_guardian": True,
                "company_id": cls.company.id, "phone": "(512) 555-01%02d" % len(cls.guardians)})
            household.primary_guardian_id = guardian
            cls.households[family], cls.guardians[family] = household, guardian
            for child in ("Ava", "Ben"):
                partner = cls.env["res.partner"].create({"name": "%s %s" % (child, family.title()), "parent_id": household.id,
                    "company_id": cls.company.id})
                member = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": "active"})
                cls.env["sale.subscription"].create({"partner_id": guardian.id, "company_id": cls.company.id, "template_id": plan.template_id.id,
                    "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": member.id, "plan_id": plan.id})
                cls.children[(family, child)] = member
        for key in (("lee", "Ava"), ("lee", "Ben"), ("other", "Ava")):
            cls.env["dojo.class.enrollment"].create({"session_id": cls.session.id, "member_id": cls.children[key].id,
                "status": "registered", "attendance_state": "pending"})
        cls.gateway_key = "synthetic-guardian-kiosk-credential-0001"
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Guardian kiosk", "pin_code": "654321", "company_id": cls.company.id,
            "integration_enabled": True, "integration_tenant_ref": "guardian-school",
            "integration_key_hash": hashlib.sha256(cls.gateway_key.encode()).hexdigest(),
            "integration_member_ids": [(6, 0, [m.id for m in cls.children.values()])], "integration_session_ids": [(6, 0, [cls.session.id])]})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Guardian test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "17"})
        cls.users = {}
        for role in ("manager", "instructor"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Guardian " + role,
                "login": "guardian-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def hub(self, op, payload, role="manager", key=None, write=True):
        if write and key is None:
            self.keys += 1
            key = "guardian-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key if write else None)

    def device(self, action, params):
        return self.env["dojo.kiosk.service"]._v2_dispatch(action, self.kiosk.kiosk_token, self.gateway_key, params)

    def set_pin(self, family="lee", pin="482913", key=None):
        return self.hub("set_guardian_kiosk_pin", {"memberId": str(self.children[(family, "Ava")].id),
            "guardianId": str(self.guardians[family].id), "pin": pin, "reason": "Parent chose a PIN at the desk"}, key=key)

    def verify(self, phone="512-555-0100", pin="482913"):
        return self.device("guardian_verify", {"phone": phone, "pin": pin, "sessionId": str(self.session.id)})

    def check_in(self, token, member, key="guardian-checkin-key-0001"):
        return self.device("guardian_checkin", {"guardianToken": token, "command": {"idempotencyKey": key,
            "correlationId": "guardian-correlation-0001", "payload": {"memberId": str(member.id), "sessionId": str(self.session.id)}}})

    def test_staff_sets_pin_for_household_guardian_without_storing_it(self):
        access = self.hub("guardian_kiosk_access", {"memberId": str(self.children[("lee", "Ava")].id)}, write=False)
        self.assertEqual([(g["name"], g["hasPin"]) for g in access["guardians"]], [("Parent Lee", False)])
        saved = self.set_pin(key="guardian-pin-request-0001")
        self.assertEqual((saved["state"], saved["messageSent"]), ("guardian_pin_set", False), saved)
        self.assertNotIn("482913", str(saved))
        receipt = self.env["dojo.hub.receipt"].sudo().search([("request_key", "=", "guardian-pin-request-0001")])
        self.assertNotIn("482913", receipt.response_json)
        self.assertNotEqual(receipt.fingerprint, hashlib.sha256(b"482913").hexdigest())
        row = self.env["dojo.kiosk.guardian.pin"].sudo().search([("partner_id", "=", self.guardians["lee"].id)])
        self.assertTrue(row.pin_hash.startswith("$pbkdf2-sha512$"))
        self.assertTrue(self.set_pin(key="guardian-pin-request-0001")["replayed"])
        self.assertEqual(self.set_pin(pin="111111")["error"]["code"], "GUARDIAN_PIN_TOO_SIMPLE")
        self.assertEqual(self.set_pin(pin="123456")["error"]["code"], "GUARDIAN_PIN_TOO_SIMPLE")
        stranger = self.hub("set_guardian_kiosk_pin", {"memberId": str(self.children[("lee", "Ava")].id),
            "guardianId": str(self.guardians["other"].id), "pin": "482913", "reason": "x"})
        self.assertEqual(stranger["error"]["code"], "GUARDIAN_NOT_IN_HOUSEHOLD")
        self.assertEqual(self.hub("guardian_kiosk_access", {"memberId": str(self.children[("lee", "Ava")].id)}, role="instructor",
            write=False)["error"]["status"], 403)

    def test_guardian_sees_only_own_children_and_checks_in_with_replay(self):
        self.set_pin()
        verified = self.verify()
        self.assertEqual(verified["guardian"], "Parent L.")
        self.assertEqual([(s["displayName"], s["onRoster"]) for s in verified["students"]], [("Ava L.", True), ("Ben L.", True)])
        result = self.check_in(verified["guardianToken"], self.children[("lee", "Ava")])
        self.assertTrue(result["guardianVerified"])
        self.assertEqual(result["receipt"]["status"], "present", result)
        replay = self.check_in(verified["guardianToken"], self.children[("lee", "Ava")])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.env["dojo.attendance.log"].search_count([("session_id", "=", self.session.id),
            ("member_id", "=", self.children[("lee", "Ava")].id)]), 1)
        other = self.check_in(verified["guardianToken"], self.children[("other", "Ava")], key="guardian-checkin-key-0002")
        self.assertEqual(other["problem"]["code"], "NOT_YOUR_STUDENT")
        self.assertFalse(self.env["dojo.attendance.log"].search([("member_id", "=", self.children[("other", "Ava")].id)]))
        grant = self.env["dojo.kiosk.guardian.session"].sudo().search([("partner_id", "=", self.guardians["lee"].id)])
        grant.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        expired = self.check_in(verified["guardianToken"], self.children[("lee", "Ben")], key="guardian-checkin-key-0003")
        self.assertEqual(expired["problem"]["code"], "GUARDIAN_SESSION_EXPIRED")
        self.assertTrue(self.check_in(verified["guardianToken"], self.children[("lee", "Ava")])["replayed"])

    def test_wrong_pin_is_generic_counts_and_locks(self):
        self.set_pin()
        self.assertEqual(self.verify(phone="512-555-0199")["problem"]["code"], "GUARDIAN_NOT_VERIFIED")
        for _ in range(4):
            self.assertEqual(self.verify(pin="000111")["problem"]["code"], "GUARDIAN_NOT_VERIFIED")
        row = self.env["dojo.kiosk.guardian.pin"].sudo().search([("partner_id", "=", self.guardians["lee"].id)])
        self.assertEqual(row.failed_attempts, 4)
        self.assertEqual(self.verify(pin="000111")["problem"]["code"], "GUARDIAN_NOT_VERIFIED")
        self.assertTrue(row.locked_until)
        self.assertEqual(self.verify()["problem"]["code"], "GUARDIAN_LOCKED")
        access = self.hub("guardian_kiosk_access", {"memberId": str(self.children[("lee", "Ava")].id)}, write=False)
        self.assertTrue(access["guardians"][0]["locked"])
        self.set_pin(pin="482914")
        self.assertIn("guardianToken", self.verify(pin="482914"))

    def test_device_throttle_and_revocation(self):
        self.set_pin()
        for i in range(10):
            self.verify(phone="512-555-09%02d" % i)
        self.assertEqual(self.verify()["problem"]["code"], "GUARDIAN_LOCKED")
        self.env["dojo.kiosk.guardian.attempt"].sudo().search([]).unlink()
        token = self.verify()["guardianToken"]
        revoked = self.hub("revoke_guardian_kiosk_pin", {"memberId": str(self.children[("lee", "Ava")].id),
            "guardianId": str(self.guardians["lee"].id), "reason": "Parent lost the PIN"})
        self.assertEqual(revoked["state"], "guardian_pin_revoked", revoked)
        self.assertEqual(self.check_in(token, self.children[("lee", "Ava")])["problem"]["code"], "GUARDIAN_SESSION_EXPIRED")
        self.assertEqual(self.verify()["problem"]["code"], "GUARDIAN_NOT_VERIFIED")
