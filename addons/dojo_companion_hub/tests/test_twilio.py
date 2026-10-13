import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

ACCOUNT = "AC" + "a" * 32
TOKEN = "b" * 32


def reply(status, body):
    response = MagicMock(status_code=status, content=b"x" * 10)
    response.json.return_value = body
    return response


@tagged("post_install", "-at_install")
class TestTwilioTestSending(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.company.partner_id.tz = "UTC"
        cls.program = cls.env["dojo.program"].create({"name": "Twilio program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Twilio class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        cls.members = {}
        for key, phone in (("one", "512-555-0161"), ("two", "(512) 555-0162"), ("bad", "555-01")):
            partner = cls.env["res.partner"].create({"name": "Twilio " + key, "company_id": cls.company.id, "phone": phone})
            cls.members[key] = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": "active"})
        cls.template.course_member_ids = [(6, 0, [m.id for m in cls.members.values()])]
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Twilio kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Twilio test", "company_id": cls.company.id, "kiosk_config_id": cls.kiosk.id,
            "workspace_ref": "22", "twilio_account_sid_env": "DOJANG_TEST_TWILIO_SID", "twilio_auth_token_env": "DOJANG_TEST_TWILIO_TOKEN",
            "twilio_from_number": "+15005550006"})
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Twilio manager", "login": "twilio-manager@example.invalid",
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.user.id, "role": "manager"})

    def setUp(self):
        super().setUp()
        self.keys = 0
        self.enterContext(patch.dict(os.environ, {"DOJANG_TEST_TWILIO_SID": ACCOUNT, "DOJANG_TEST_TWILIO_TOKEN": TOKEN}))
        self.enterContext(self.registry_test_mode())
        self.enterContext(patch.object(type(self.env["dojo.hub.service"]), "_quiet_schedule", return_value=None))

    def act(self, op, payload, key=None):
        if key is None:
            self.keys += 1
            key = "twilio-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.user)._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload):
        return self.env["dojo.hub.service"].with_user(self.user)._dispatch(str(self.site.id), op, payload, None)

    def approved(self, channel="sms"):
        payload = {"channel": channel, "purpose": "service", "audience": "template:%s" % self.template.id, "body": "Class moved to 6pm."}
        preview = self.read("broadcast_preview", payload)
        created = self.act("create_broadcast", {**payload, "name": "Moved class", "reason": "Schedule change", "reviewToken": preview["reviewToken"]})
        approved = self.act("approve_broadcast", {"broadcastId": created["broadcastId"], "expectedVersion": created["version"], "reason": "Checked"})
        self.assertEqual(approved["state"], "broadcast_approved", approved)
        return approved

    def dispatch(self, approved, key=None):
        return self.act("dispatch_broadcast", {"broadcastId": approved["broadcastId"], "expectedVersion": approved["version"], "reason": "Send now"}, key=key)

    def recipients(self, approved):
        rows = self.env["dojo.hub.broadcast.recipient"].sudo().search([("broadcast_id", "=", int(approved["broadcastId"]))])
        rows.invalidate_recordset()
        return {r.partner_id.name: r for r in rows}

    def test_only_test_sender_and_env_credentials_are_accepted(self):
        self.assertEqual(self.read("broadcast_context", {})["provider"], "twilio_test")
        with self.assertRaises(ValidationError):
            self.site.twilio_from_number = "+15125550100"
        with self.assertRaises(ValidationError):
            self.site.twilio_account_sid_env = ACCOUNT
        with patch.dict(os.environ, {"DOJANG_TEST_TWILIO_TOKEN": "not-a-token"}):
            self.assertEqual(self.read("broadcast_context", {})["provider"], "not_configured")

    def test_accepted_messages_keep_twilio_receipts_and_are_never_resent(self):
        approved = self.approved()
        sid = "SM" + "c" * 32
        with patch("odoo.addons.dojo_companion_hub.models.twilio.requests.post", return_value=reply(201, {"sid": sid, "status": "queued"})) as post:
            sent = self.dispatch(approved, key="twilio-dispatch-0001")
            self.assertEqual((sent["state"], sent["accepted"], sent["excluded"], sent["realMessagesSent"]), ("broadcast_dispatched", 2, 1, False), sent)
            self.assertEqual(post.call_count, 2)
            args = post.call_args
            self.assertEqual(args.args[0], "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % ACCOUNT)
            self.assertEqual(args.kwargs["auth"], (ACCOUNT, TOKEN))
            self.assertEqual(args.kwargs["data"]["From"], "+15005550006")
            self.assertIn(args.kwargs["data"]["To"], ("+15125550161", "+15125550162"))
            self.assertTrue(self.dispatch(approved, key="twilio-dispatch-0001")["replayed"])
            self.assertEqual(self.dispatch(approved)["error"]["code"], "BROADCAST_NOTHING_QUEUED")
            self.assertEqual(post.call_count, 2)
        rows = self.recipients(approved)
        self.assertEqual((rows["Twilio one"].state, rows["Twilio one"].provider_ref), ("accepted", sid))
        self.assertEqual((rows["Twilio bad"].state, rows["Twilio bad"].reason), ("excluded", "invalid_number"))

    def test_rejected_and_ambiguous_results_are_not_success(self):
        approved = self.approved()
        responses = [reply(400, {"code": 21211, "message": "Invalid To"}), requests.Timeout()]
        with patch("odoo.addons.dojo_companion_hub.models.twilio.requests.post", side_effect=responses):
            sent = self.dispatch(approved)
        self.assertEqual((sent["accepted"], sent["failed"], sent["uncertain"]), (0, 1, 1), sent)
        states = sorted((r.state, r.reason) for r in self.recipients(approved).values())
        self.assertIn(("failed", "twilio_21211"), states)
        self.assertIn(("uncertain", "provider_result_requires_reconciliation"), states)

    def test_quiet_hours_channel_and_consent_are_rechecked(self):
        approved = self.approved()
        row = self.env["dojo.hub.broadcast"].sudo().browse(int(approved["broadcastId"]))
        row.scheduled_at = fields.Datetime.now() + timedelta(hours=2)
        current = self.read("broadcast_context", {})["broadcasts"][0]
        self.assertEqual(self.dispatch({"broadcastId": approved["broadcastId"], "version": current["version"]})["error"]["code"], "BROADCAST_NOT_DUE")
        row.scheduled_at = False
        current = self.read("broadcast_context", {})["broadcasts"][0]
        self.act("record_consent", {"memberId": str(self.members["one"].id), "partnerId": str(self.members["one"].partner_id.id), "channel": "sms",
            "purpose": "service", "state": "withdrawn", "source": "reply_stop", "evidence": "Replied STOP"})
        with patch("odoo.addons.dojo_companion_hub.models.twilio.requests.post", return_value=reply(201, {"sid": "SM" + "d" * 32})) as post:
            sent = self.dispatch({"broadcastId": approved["broadcastId"], "version": current["version"]})
        self.assertEqual((sent["accepted"], sent["excluded"]), (1, 2), sent)
        self.assertEqual(post.call_count, 1)
