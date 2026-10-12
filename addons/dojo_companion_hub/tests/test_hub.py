"""Real Odoo transactions with synthetic users; all provider I/O is mocked."""
import hashlib
import hmac
import json
import os
import time
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import session_version
from ..models.delivery import verify_signature
from ..models.service import HubProblem


@tagged("post_install", "-at_install")
class TestCompanionHub(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Hub program", "company_id": cls.company.id})
        cls.partner = cls.env["res.partner"].create({"name": "Hub Student", "company_id": cls.company.id})
        cls.member = cls.env["dojo.member"].create({"partner_id": cls.partner.id, "company_id": cls.company.id, "membership_state": "active"})
        cls.template = cls.env["dojo.class.template"].create({"name": "Hub class", "company_id": cls.company.id, "program_id": cls.program.id})
        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now + timedelta(hours=48), "end_datetime": now + timedelta(hours=49), "state": "open", "capacity": 5})
        cls.plan = cls.env["dojo.subscription.plan"].create({"name": "Hub plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False, "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Hub active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].create({"name": "Hub price", "currency_id": cls.company.currency_id.id})
        cls.subscription = cls.env["sale.subscription"].create({"partner_id": cls.partner.id, "company_id": cls.company.id,
            "template_id": cls.plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": cls.member.id, "plan_id": cls.plan.id})
        cls.enrollment = cls.env["dojo.class.enrollment"].create({"session_id": cls.session.id, "member_id": cls.member.id})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Hub kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Hub test", "company_id": cls.company.id, "kiosk_config_id": cls.kiosk.id,
            "workspace_ref": "11", "provider_origin": "https://provider.example.invalid", "webhook_secret_env": "DOJANG_HUB_TEST_INBOUND",
            "provider_token_env": "DOJANG_HUB_TEST_PROVIDER", "outbound_enabled": True})
        cls.users = {}
        cls.grants = {}
        for role in ("manager", "instructor", "guardian", "unassigned"):
            user = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Synthetic " + role,
                "login": "hub-" + role + "@example.invalid", "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
                "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.users[role] = user
            if role != "unassigned":
                cls.grants[role] = cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": user.id, "role": role,
                    "member_ids": [(6, 0, [cls.member.id])], "session_ids": [(6, 0, [cls.session.id])]})
        cls.binding = cls.env["dojo.hub.guardian"].create({"site_id": cls.site.id, "partner_id": cls.users["guardian"].partner_id.id,
            "member_ids": [(6, 0, [cls.member.id])], "channel": "sms", "contact_ref": "42", "verification_ref": "synthetic-authority-evidence", "allow_reply": True})

    def setUp(self):
        super().setUp()
        self.env_patch = patch.dict(os.environ, {"DOJANG_HUB_TEST_INBOUND": "i" * 48, "DOJANG_HUB_TEST_PROVIDER": "p" * 48})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.counter = 0

    def act(self, operation, payload=None, role="manager", key=None, site=None):
        self.counter += 1
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str((site or self.site).id), operation,
            payload or {}, key or "hub-test-request-%04d" % self.counter)

    def ingest(self, event=None):
        event = event or {"type": "message.received", "workspaceRef": "11", "eventRef": "message-100", "contactRef": "42", "channel": "sms", "text": "My child cannot attend. Please help arrange a makeup."}
        body = json.dumps(event).encode()
        stamp = str(int(time.time()))
        signature = "sha256=" + hmac.new(b"i" * 48, stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        result = self.env["dojo.hub.service"]._ingest(str(self.site.id), body, stamp, signature)
        return result

    def ready(self):
        self.ingest()
        message = self.env["dojo.hub.message"].search([("site_id", "=", self.site.id)], limit=1)
        result = self.act("resolve", {"messageId": str(message.id), "memberId": str(self.member.id), "sessionId": str(self.session.id), "expectedRevision": message.revision})
        self.assertNotIn("error", result)
        result = self.act("draft", {"messageId": str(message.id), "expectedRevision": message.revision})
        self.assertNotIn("error", result)
        return message

    def queued(self):
        message = self.ready()
        result = self.act("approve_reply", {"messageId": str(message.id), "expectedRevision": message.revision, "reply": message.reply_draft})
        self.assertNotIn("error", result)
        return self.env["dojo.hub.delivery"].browse(int(result["deliveryId"]))

    def provider(self, send=None, profile=None):
        return patch.multiple(requests, get=Mock(return_value=profile or Mock(status_code=200, content=b"{}", json=lambda: {"workspace_id": 11, "demo_mode": False})),
            post=send or Mock(return_value=Mock(status_code=200, content=b"{}", json=lambda: {"provider_message_id": "synthetic-provider-100", "status": "sent"})))

    def test_signed_event_is_persisted_and_duplicate_replayed(self):
        self.assertFalse(self.ingest()["replayed"])
        self.assertTrue(self.ingest()["replayed"])
        self.assertEqual(self.env["dojo.hub.message"].search_count([("site_id", "=", self.site.id)]), 1)

    def test_signature_rejects_old_modified_or_unsigned_event(self):
        body, stamp = b"{}", "1000"
        signature = "sha256=" + hmac.new(b"i" * 48, stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature("i" * 48, stamp, signature, body, now=1000))
        self.assertFalse(verify_signature("i" * 48, stamp, signature, body, now=1400))
        self.assertFalse(verify_signature("i" * 48, stamp, signature, b"[]", now=1000))
        self.assertFalse(verify_signature("", stamp, signature, body, now=1000))

    def test_same_event_id_with_changed_message_is_rejected(self):
        self.ingest()
        with self.assertRaises(HubProblem):
            self.ingest({"type": "message.received", "workspaceRef": "11", "eventRef": "message-100", "contactRef": "42", "channel": "sms", "text": "Changed"})

    def test_cross_workspace_event_is_rejected(self):
        with self.assertRaises(HubProblem):
            self.ingest({"workspaceRef": "12"})

    def test_unmatched_sender_cannot_be_assigned_by_guessing_a_child(self):
        self.binding.active = False
        self.ingest()
        row = self.env["dojo.hub.message"].search([("site_id", "=", self.site.id)], limit=1)
        self.assertEqual(row.state, "unmatched")
        result = self.act("resolve", {"messageId": str(row.id), "memberId": str(self.member.id), "sessionId": str(self.session.id), "expectedRevision": 1})
        self.assertEqual(result["error"]["code"], "GUARDIAN_REVIEW_REQUIRED")

    def test_individual_account_and_explicit_grant_required(self):
        self.assertEqual(self.act("context", role="unassigned")["error"]["status"], 403)
        self.assertEqual(self.act("context")["principal"]["userId"], str(self.users["manager"].id))
        self.grants["manager"].active = False
        self.assertEqual(self.act("context")["error"]["status"], 403)

    def test_parent_cannot_invoke_staff_actions_or_read_private_models(self):
        self.assertEqual(self.act("checkout", {"memberId": str(self.member.id), "sessionId": str(self.session.id)}, role="guardian")["error"]["status"], 403)
        with self.assertRaises(AccessError):
            self.env["dojo.hub.delivery"].with_user(self.users["guardian"]).search([])

    def test_guardian_revocation_removes_child_from_read_scope(self):
        self.assertEqual(len(self.act("context", role="guardian")["members"]), 1)
        self.binding.active = False
        self.assertEqual(self.act("context", role="guardian")["members"], [])

    def test_instructor_sees_only_assigned_classes(self):
        self.ready()
        self.assertEqual(len(self.act("context", role="instructor")["messages"]), 1)
        self.grants["instructor"].session_ids = [(5, 0, 0)]
        data = self.act("context", role="instructor")
        self.assertEqual(data["members"], [])
        self.assertEqual(data["messages"], [])

    def test_cross_company_scope_cannot_be_granted(self):
        other = self.env["res.company"].create({"name": "Other hub company"})
        outsider = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Other student"}).id, "company_id": other.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.grants["guardian"].member_ids = [(6, 0, [outsider.id])]
        self.assertEqual(self.act("update_member", {"memberId": str(outsider.id), "expectedVersion": 0, "changes": {"name": "Wrong"}})["error"]["code"], "MEMBER_UNAVAILABLE")

    def test_parent_message_draft_approval_does_not_send_in_request(self):
        with patch.object(requests, "post") as send:
            delivery = self.queued()
            self.assertEqual(delivery.state, "queued")
            self.assertEqual(delivery.approved_by, self.users["manager"])
            send.assert_not_called()
        self.assertEqual(self.enrollment.attendance_state, "pending")

    def test_old_reply_revision_cannot_be_approved(self):
        message = self.ready()
        result = self.act("approve_reply", {"messageId": str(message.id), "expectedRevision": message.revision - 1, "reply": message.reply_draft})
        self.assertEqual(result["error"]["code"], "VERSION_CONFLICT")
        self.assertFalse(self.env["dojo.hub.delivery"].search_count([("site_id", "=", self.site.id)]))

    def test_approval_retry_and_new_key_do_not_duplicate_delivery(self):
        row = self.queued()
        p = {"messageId": str(row.message_id.id), "expectedRevision": row.approved_revision, "reply": row.body}
        first = self.act("approve_reply", p, key="hub-repeat-approval-0001")
        again = self.act("approve_reply", p, key="hub-repeat-approval-0001")
        self.assertTrue(again["replayed"])
        self.assertEqual(first["deliveryId"], again["deliveryId"])
        self.assertEqual(self.env["dojo.hub.delivery"].search_count([("site_id", "=", self.site.id)]), 1)

    def test_unconfigured_delivery_cannot_be_approved(self):
        message = self.ready()
        self.site.outbound_enabled = False
        result = self.act("approve_reply", {"messageId": str(message.id), "expectedRevision": message.revision, "reply": message.reply_draft})
        self.assertEqual(result["error"]["code"], "PROVIDER_NOT_CONFIGURED")

    def test_provider_acceptance_is_not_delivery_confirmation(self):
        row = self.queued()
        row.state = "dispatching"
        with self.provider():
            row._send_once()
        self.assertEqual(row.state, "accepted")
        self.ingest({"type": "delivery.status", "workspaceRef": "11", "eventRef": "delivery-100", "providerRef": row.provider_ref, "status": "delivered"})
        self.assertEqual(row.state, "delivered")
        self.ingest({"type": "delivery.status", "workspaceRef": "11", "eventRef": "delivery-101", "providerRef": row.provider_ref, "status": "failed"})
        self.assertEqual(row.state, "delivered")

    def test_early_status_is_applied_when_send_receipt_arrives(self):
        row = self.queued()
        self.ingest({"type": "delivery.status", "workspaceRef": "11", "eventRef": "delivery-100", "providerRef": "synthetic-provider-100", "status": "delivered"})
        row.state = "dispatching"
        with self.provider():
            row._send_once()
        self.assertEqual(row.state, "delivered")

    def test_timeout_never_automatically_resends(self):
        row = self.queued()
        row.state = "dispatching"
        send = Mock(side_effect=requests.Timeout())
        with self.provider(send=send):
            row._send_once()
            row._send_once()
        self.assertEqual(row.state, "uncertain")
        self.assertEqual(send.call_count, 1)

    def test_wrong_provider_workspace_never_sends(self):
        row = self.queued()
        row.state = "dispatching"
        send = Mock()
        profile = Mock(status_code=200, content=b"{}", json=lambda: {"workspace_id": 12, "demo_mode": False})
        with self.provider(send=send, profile=profile):
            row._send_once()
        self.assertEqual(row.state, "cancelled")
        send.assert_not_called()

    def test_revoked_approver_or_guardian_blocks_queued_send(self):
        row = self.queued()
        row.state = "dispatching"
        self.grants["manager"].active = False
        with patch.object(requests, "post") as send:
            row._send_once()
        self.assertEqual(row.state, "cancelled")
        send.assert_not_called()

    def test_booking_checks_capacity_and_subscription_atomically(self):
        target = self.session.copy({"state": "open", "start_datetime": self.session.start_datetime + timedelta(days=1), "end_datetime": self.session.end_datetime + timedelta(days=1)})
        self.assertEqual(target.state, "open")
        self.assertGreater(target.start_datetime, fields.Datetime.now())
        self.subscription.paused = True
        result = self.act("book", {"memberId": str(self.member.id), "sessionId": str(target.id), "expectedVersion": session_version(target)})
        self.assertEqual(result.get("error", {}).get("code"), "BUSINESS_RULE_REVIEW_REQUIRED", result)
        self.assertFalse(self.env["dojo.class.enrollment"].search_count([("session_id", "=", target.id)]))
        self.subscription.paused = False
        result = self.act("book", {"memberId": str(self.member.id), "sessionId": str(target.id), "expectedVersion": session_version(target)})
        self.assertNotIn("error", result, result)
        self.assertEqual(result["state"], "registered")

    def test_revoked_instructor_member_scope_blocks_queued_send(self):
        message = self.ready()
        result = self.act("approve_reply", {"messageId": str(message.id), "expectedRevision": message.revision,
            "reply": message.reply_draft}, role="instructor")
        self.assertNotIn("error", result, result)
        delivery = self.env["dojo.hub.delivery"].browse(int(result["deliveryId"]))
        self.enrollment.status = "cancelled"
        delivery.state = "dispatching"
        with patch.object(requests, "post") as send:
            delivery._send_once()
        self.assertEqual(delivery.state, "cancelled")
        send.assert_not_called()

    def test_instructor_audit_does_not_expose_another_class_for_same_child(self):
        self.ready()
        target = self.session.copy({"state": "open", "start_datetime": self.session.start_datetime + timedelta(days=1),
            "end_datetime": self.session.end_datetime + timedelta(days=1)})
        result = self.act("book", {"memberId": str(self.member.id), "sessionId": str(target.id), "expectedVersion": session_version(target)})
        self.assertNotIn("error", result, result)
        self.assertTrue(any(row["action"] == "book" for row in self.act("context")["timeline"]))
        rows = self.act("context", role="instructor")["timeline"]
        self.assertTrue(rows)
        self.assertTrue(all(row["sessionId"] == str(self.session.id) for row in rows))
        self.assertFalse(any(row["booking"] for row in rows))

    def test_saved_booking_receipt_is_readable_after_a_fresh_context(self):
        result = self.act("book", {"memberId": str(self.member.id), "sessionId": str(self.session.id),
            "expectedVersion": session_version(self.session)})
        self.assertNotIn("error", result)
        before = self.env["dojo.class.enrollment"].search_count([])
        for role in ("manager", "instructor", "guardian"):
            # Context uses the saved receipt, not a new mutation or request key replay.
            rows = self.act("context", role=role)["timeline"]
            saved = next(row["booking"] for row in rows if row["action"] == "book")
            self.assertEqual(saved, {"enrollmentId": result["enrollmentId"], "classTitle": self.template.name,
                "startsAt": self.session.start_datetime.isoformat() + "Z", "state": "registered", "attendanceState": "pending"})
        self.assertEqual(self.env["dojo.class.enrollment"].search_count([]), before)
        other_site = self.site.copy({"name": "Other site", "kiosk_config_id": self.kiosk.copy({"name": "Other kiosk"}).id})
        self.env["dojo.hub.grant"].create({"site_id": other_site.id, "user_id": self.users["manager"].id, "role": "manager"})
        self.assertEqual(self.act("context", site=other_site)["timeline"], [])

    def test_booking_receipt_reports_current_state_not_historical_success(self):
        self.act("book", {"memberId": str(self.member.id), "sessionId": str(self.session.id),
            "expectedVersion": session_version(self.session)})
        self.enrollment.status = "cancelled"
        self.session.state = "done"
        data = self.act("context")
        self.assertFalse(data["sessions"])
        self.assertEqual(data["timeline"][0]["booking"]["state"], "cancelled")

    def test_booking_receipt_is_removed_when_read_scope_is_revoked(self):
        self.act("book", {"memberId": str(self.member.id), "sessionId": str(self.session.id),
            "expectedVersion": session_version(self.session)})
        self.assertTrue(self.act("context", role="guardian")["timeline"])
        self.binding.active = False
        self.grants["instructor"].session_ids = [(5, 0, 0)]
        self.assertEqual(self.act("context", role="guardian")["timeline"], [])
        self.assertEqual(self.act("context", role="instructor")["timeline"], [])

    def test_receipt_projection_rejects_malformed_or_mismatched_enrollment(self):
        self.act("book", {"memberId": str(self.member.id), "sessionId": str(self.session.id),
            "expectedVersion": session_version(self.session)})
        receipt = self.env["dojo.hub.receipt"].search([("site_id", "=", self.site.id), ("operation", "=", "book")], limit=1)
        for raw in ("not json", "[]", "null", '{"enrollmentId": true}', '{"enrollmentId": "999999999999999999"}'):
            receipt.response_json = raw
            self.assertIsNone(self.act("context")["timeline"][0]["booking"])
        other_session = self.session.copy({"state": "open"})
        other_enrollment = self.enrollment.copy({"session_id": other_session.id})
        receipt.response_json = json.dumps({"enrollmentId": str(other_enrollment.id)})
        self.assertIsNone(self.act("context")["timeline"][0]["booking"])
        receipt.response_json = json.dumps({"enrollmentId": str(self.enrollment.id), "private": "must-not-leak"})
        data = self.act("context")
        self.assertEqual(data["timeline"][0]["booking"]["enrollmentId"], str(self.enrollment.id))
        self.assertNotIn("must-not-leak", json.dumps(data))
        receipt.operation = "update_member"
        self.assertIsNone(self.act("context")["timeline"][0]["booking"])

    def test_class_change_receipt_contains_the_destination_registration(self):
        target = self.session.copy({"state": "open", "start_datetime": self.session.start_datetime + timedelta(days=1),
            "end_datetime": self.session.end_datetime + timedelta(days=1)})
        result = self.act("change_class", {"memberId": str(self.member.id), "fromSessionId": str(self.session.id),
            "sessionId": str(target.id), "expectedVersion": session_version(target)})
        self.assertNotIn("error", result, result)
        saved = self.act("context")["timeline"][0]
        self.assertEqual(saved["sessionId"], str(target.id))
        self.assertEqual(saved["booking"]["enrollmentId"], result["enrollmentId"])
        self.assertEqual(saved["booking"]["state"], "registered")

    def test_class_change_failure_preserves_original_enrollment(self):
        target = self.session.copy({"state": "cancelled"})
        result = self.act("change_class", {"memberId": str(self.member.id), "fromSessionId": str(self.session.id), "sessionId": str(target.id), "expectedVersion": session_version(target)})
        self.assertIn("error", result)
        self.enrollment.invalidate_recordset()
        self.assertEqual(self.enrollment.status, "registered")

    def test_checkout_retries_share_one_saved_timestamp(self):
        attendance = self.env["dojo.attendance.log"].create({"member_id": self.member.id, "session_id": self.session.id, "enrollment_id": self.enrollment.id, "status": "present", "checkin_datetime": fields.Datetime.now() - timedelta(minutes=2)})
        p = {"memberId": str(self.member.id), "sessionId": str(self.session.id)}
        first, second = self.act("checkout", p), self.act("checkout", p)
        self.assertEqual(first["checkedOutAt"], second["checkedOutAt"])
        self.assertTrue(attendance.checkout_datetime)

    def test_member_edit_is_role_and_version_checked(self):
        p = {"memberId": str(self.member.id), "expectedVersion": session_version(self.member), "changes": {"name": "Updated synthetic name"}}
        self.assertEqual(self.act("update_member", p, role="instructor")["error"]["status"], 403)
        self.assertEqual(self.act("update_member", {**p, "expectedVersion": 1})["error"]["code"], "VERSION_CONFLICT")
        result = self.act("update_member", p)
        self.assertEqual(result["state"], "updated")
        self.assertEqual(self.member.name, "Updated synthetic name")

    def test_member_details_are_scoped_and_do_not_create_receipts(self):
        before = self.env["dojo.hub.receipt"].search_count([])
        result = self.act("member_details", {"memberId": str(self.member.id)})
        self.assertNotIn("error", result)
        self.assertEqual(result["member"]["id"], str(self.member.id))
        self.assertEqual(result["bookings"][0]["id"], str(self.enrollment.id))
        self.assertEqual(result["billing"]["subscriptions"][0]["id"], str(self.subscription.id))
        self.assertEqual(result["billing"]["paymentProcessing"], "odoo_portal")
        self.assertEqual(self.env["dojo.hub.receipt"].search_count([]), before)
        self.assertEqual(self.act("member_details", {"memberId": "2147483647"})["error"]["code"], "MEMBER_UNAVAILABLE")
        self.assertEqual(self.act("member_details", {"memberId": str(self.member.id)}, role="guardian")["error"]["status"], 403)

    def test_instructor_details_do_not_expose_billing_or_contact_edits(self):
        self.partner.write({"email": "private@example.invalid", "phone": "5550100"})
        result = self.act("member_details", {"memberId": str(self.member.id)}, role="instructor")
        self.assertFalse(result["canEdit"])
        self.assertIsNone(result["billing"])
        self.assertIsNone(result["member"]["email"])
        self.assertIsNone(result["member"]["phone"])
        self.assertEqual(result["ranks"], [])
        self.grants["instructor"].session_ids = False
        self.assertEqual(self.act("member_details", {"memberId": str(self.member.id)}, role="instructor")["error"]["code"], "MEMBER_UNAVAILABLE")

    def test_details_checkout_and_rank_history_are_read_from_saved_records(self):
        attendance = self.env["dojo.attendance.log"].create({"member_id": self.member.id, "session_id": self.session.id,
            "enrollment_id": self.enrollment.id, "status": "present", "checkin_datetime": fields.Datetime.now() - timedelta(minutes=2)})
        rank = self.env["dojo.belt.rank"].create({"name": "Details Yellow", "company_id": self.company.id, "max_stripes": 4})
        self.assertNotIn("error", self.act("rank", {"memberId": str(self.member.id), "rankId": str(rank.id),
            "expectedVersion": session_version(self.member), "stripes": 1, "reason": "Synthetic verified assessment"}))
        checkout = self.act("checkout", {"memberId": str(self.member.id), "sessionId": str(self.session.id)})
        self.assertNotIn("error", checkout)
        result = self.act("member_details", {"memberId": str(self.member.id)})
        self.assertEqual(result["attendance"][0]["id"], str(attendance.id))
        self.assertEqual(result["attendance"][0]["checkedOutAt"], checkout["checkedOutAt"])
        self.assertEqual(result["rankHistory"][0]["rank"], "Details Yellow")
        self.assertEqual(result["member"]["stripes"], 1)

    def test_contact_edit_checks_partner_version_and_can_clear_optional_fields(self):
        self.partner.write({"email": "synthetic@example.invalid", "phone": "5550100"})
        details = self.act("member_details", {"memberId": str(self.member.id)})
        payload = {"memberId": str(self.member.id), "expectedVersion": details["member"]["version"],
            "expectedPartnerVersion": details["member"]["partnerVersion"], "changes": {"email": "", "phone": ""}}
        self.assertEqual(self.act("update_member", {**payload, "expectedPartnerVersion": 1})["error"]["code"], "VERSION_CONFLICT")
        result = self.act("update_member", payload, key="contact-clear-request-01")
        self.assertEqual(result["state"], "updated")
        replay = self.act("update_member", payload, key="contact-clear-request-01")
        self.assertTrue(replay["replayed"])
        refreshed = self.act("member_details", {"memberId": str(self.member.id)})
        self.assertEqual(refreshed["member"]["email"], "")
        self.assertEqual(refreshed["member"]["phone"], "")
        self.assertEqual(self.act("update_member", {"memberId": str(self.member.id), "expectedVersion": session_version(self.member), "changes": {"name": " "}})["error"]["code"], "INVALID_COMMAND")

    def test_billing_uses_explicit_links_and_marks_household_totals(self):
        income = self.env["account.account"].create({"name": "Hub test income", "code": "HUBI1", "account_type": "income", "company_ids": [(6, 0, [self.company.id])]})
        receivable = self.env["account.account"].create({"name": "Hub test receivable", "code": "HUBR1", "account_type": "asset_receivable", "reconcile": True, "company_ids": [(6, 0, [self.company.id])]})
        journal = self.env["account.journal"].create({"name": "Hub test sales", "code": "HBTS", "type": "sale", "company_id": self.company.id, "default_account_id": income.id})
        self.partner.with_company(self.company).property_account_receivable_id = receivable
        def invoice(amount, linked=False):
            record = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": self.partner.id,
                "company_id": self.company.id, "journal_id": journal.id, "invoice_date": fields.Date.today(),
                "subscription_id": self.subscription.id if linked else False,
                "invoice_line_ids": [(0, 0, {"name": "Synthetic tuition", "quantity": 1, "price_unit": amount, "account_id": income.id, "tax_ids": [(5, 0, 0)]})]})
            record.action_post()
            return record
        own, shared, unrelated = invoice(30, True), invoice(80), invoice(99)
        self.subscription.household_invoice_ids = [(4, shared.id)]
        billing = self.act("member_details", {"memberId": str(self.member.id)})["billing"]
        rows = {i["id"]: i for i in billing["invoices"]}
        self.assertEqual(set(rows), {str(own.id), str(shared.id)})
        self.assertNotIn(str(unrelated.id), rows)
        self.assertEqual(rows[str(own.id)]["scope"], "member")
        self.assertEqual(rows[str(shared.id)]["scope"], "household")
        self.assertEqual(rows[str(shared.id)]["total"], 80)

    def test_contact_edit_cannot_silently_change_a_shared_member_identity(self):
        duplicate = self.env["dojo.member"].create({"partner_id": self.partner.id, "company_id": self.company.id})
        before = duplicate.name
        details = self.act("member_details", {"memberId": str(self.member.id)})
        self.assertFalse(details["canEditContact"])
        result = self.act("update_member", {"memberId": str(self.member.id), "expectedVersion": session_version(self.member), "changes": {"name": "Must not change"}})
        self.assertEqual(result["error"]["code"], "SHARED_CONTACT_REVIEW_REQUIRED")
        self.assertEqual(duplicate.name, before)

    def test_belt_awards_keep_history_and_latest_same_day_rank(self):
        ranks = [self.env["dojo.belt.rank"].create({"name": name, "company_id": self.company.id}) for name in ("White", "Yellow")]
        for rank in ranks:
            result = self.act("rank", {"memberId": str(self.member.id), "rankId": str(rank.id), "expectedVersion": session_version(self.member), "stripes": 0, "reason": "Instructor verified assessment"})
            self.assertEqual(result["state"], "awarded")
        self.assertEqual(self.member.current_rank_id, ranks[1])
        self.assertEqual(len(self.member.rank_history_ids), 2)

    def test_onboarding_creates_real_member_account_without_sending_invitation(self):
        p = {"name": "New synthetic student", "login": "new-hub-student@example.invalid", "role": "member", "memberIds": [], "sessionIds": [], "verificationRef": "staff-record-100"}
        with patch.object(type(self.env["mail.mail"]), "send") as send:
            result = self.act("provision", p, key="onboard-unique-10001")
            again = self.act("provision", p, key="onboard-unique-10001")
        self.assertEqual(result["state"], "password_setup_required")
        self.assertEqual(result["userId"], again["userId"])
        member = self.env["dojo.member"].browse(int(result["memberId"]))
        self.assertEqual(member.membership_state, "lead")
        self.assertEqual(member.partner_id, self.env["res.users"].browse(int(result["userId"])).partner_id)
        send.assert_not_called()

    def test_onboarding_cannot_claim_an_existing_user_or_escalate_owner(self):
        p = {"name": "Claim", "login": self.users["manager"].login, "role": "member", "memberIds": [], "sessionIds": [], "verificationRef": "staff-record"}
        self.assertEqual(self.act("provision", p)["error"]["code"], "EXISTING_USER_REQUIRES_ADMIN_LINK")
        self.assertEqual(self.act("provision", {**p, "role": "owner"})["error"]["code"], "INVALID_COMMAND")

    def test_guardian_onboarding_requires_prior_verified_child_binding(self):
        partner = self.env["res.partner"].create({"name": "Verified new guardian", "company_id": self.company.id})
        p = {"name": partner.name, "login": "verified-new-guardian@example.invalid", "role": "guardian",
             "partnerId": str(partner.id), "memberIds": [str(self.member.id)], "sessionIds": [], "verificationRef": "guardian-record-200"}
        self.assertEqual(self.act("provision", p)["error"]["code"], "VERIFIED_GUARDIAN_REQUIRED")
        self.env["dojo.hub.guardian"].create({"site_id": self.site.id, "partner_id": partner.id,
            "member_ids": [(6, 0, [self.member.id])], "channel": "sms", "contact_ref": "43", "verification_ref": p["verificationRef"]})
        result = self.act("provision", p)
        self.assertNotIn("error", result, result)
        user = self.env["res.users"].browse(int(result["userId"]))
        self.assertEqual(user.partner_id, partner)
        context = self.env["dojo.hub.service"].with_user(user)._dispatch(str(self.site.id), "context", {})
        self.assertEqual(context["principal"]["role"], "guardian")
        self.assertEqual([m["id"] for m in context["members"]], [str(self.member.id)])

    def test_guardian_onboarding_cannot_reuse_an_existing_user_partner(self):
        p = {"name": "Claim guardian", "login": "different-guardian@example.invalid", "role": "guardian",
             "partnerId": str(self.users["guardian"].partner_id.id), "memberIds": [str(self.member.id)],
             "sessionIds": [], "verificationRef": self.binding.verification_ref}
        self.assertEqual(self.act("provision", p)["error"]["code"], "VERIFIED_GUARDIAN_REQUIRED")

    def new_lead(self):
        result = self.act("create_lead", {"name": "Synthetic CRM lead", "contact": "Synthetic Contact", "email": "crm@example.invalid", "phone": "555-0110"})
        self.assertNotIn("error", result, result)
        return self.env["crm.lead"].browse(int(result["leadId"]))

    def test_crm_scope_roles_and_cross_company(self):
        lead = self.new_lead()
        for role in ("guardian", "instructor", "unassigned"):
            self.assertEqual(self.act("crm_context", {"query": ""}, role=role)["error"]["status"], 403)
            self.assertEqual(self.act("lead_details", {"leadId": str(lead.id)}, role=role)["error"]["status"], 403)
        other = self.env["res.company"].create({"name": "Other CRM company"})
        outsider = self.env["crm.lead"].create({"name": "Other lead", "company_id": other.id})
        data = self.act("crm_context", {"query": ""})
        self.assertIn(str(lead.id), [l["id"] for l in data["leads"]])
        self.assertNotIn(str(outsider.id), [l["id"] for l in data["leads"]])
        self.assertEqual(self.act("lead_details", {"leadId": str(outsider.id)})["error"]["code"], "LEAD_UNAVAILABLE")

    def test_crm_lead_creation_replay_and_validation(self):
        p = {"name": "One lead", "contact": "One contact", "email": "one@example.invalid", "phone": ""}
        first = self.act("create_lead", p, key="crm-lead-create-0001")
        again = self.act("create_lead", p, key="crm-lead-create-0001")
        self.assertEqual(first["leadId"], again["leadId"])
        self.assertTrue(again["replayed"])
        self.assertEqual(self.act("create_lead", {**p, "email": "invalid"})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.act("create_lead", {**p, "company_id": self.company.id})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.act("crm_context", {"query": "One lead"})["leads"][0]["id"], first["leadId"])
        self.grants["manager"].role = "instructor"
        self.assertEqual(self.act("create_lead", p, key="crm-lead-create-0001")["error"]["status"], 403)

    def test_crm_lead_update_exact_identity_conflict_and_linked_partner(self):
        lead = self.new_lead()
        p = {"leadId": str(lead.id), "expectedVersion": session_version(lead), "changes": {"phone": "555-0199"}}
        self.assertEqual(self.act("update_lead", {**p, "expectedVersion": 1})["error"]["code"], "VERSION_CONFLICT")
        self.assertNotIn("error", self.act("update_lead", p))
        self.assertEqual(lead.phone, "555-0199")
        self.assertNotEqual(self.member.phone, lead.phone)
        lead.partner_id = self.partner
        details = self.act("lead_details", {"leadId": str(lead.id)})
        self.assertFalse(details["lead"]["canEditContact"])
        result = self.act("update_lead", {**p, "expectedVersion": session_version(lead)})
        self.assertEqual(result["error"]["code"], "SHARED_CONTACT_REVIEW_REQUIRED")

    def test_crm_native_task_receipt_completion_and_history_no_notifications(self):
        lead = self.new_lead()
        p = {"relatedType": "lead", "relatedId": str(lead.id), "summary": "Review trial options", "due": str(fields.Date.today())}
        # Follower notifications are suppressed for this internal-only path.
        lead.message_subscribe(partner_ids=[self.users["guardian"].partner_id.id])
        with patch.object(type(self.env["mail.activity"]), "action_notify") as notify, patch.object(type(lead), "_notify_thread_by_email") as email:
            first = self.act("create_task", p, key="crm-task-create-0001")
            self.assertNotIn("error", first, first)
            again = self.act("create_task", p, key="crm-task-create-0001")
            self.assertEqual(first["taskId"], again["taskId"])
            task = self.env["mail.activity"].browse(int(first["taskId"]))
            self.assertEqual(task.res_model, "crm.lead")
            self.assertEqual(task.res_id, lead.id)
            self.assertEqual(task.user_id, self.users["manager"])
            done = {"taskId": str(task.id), "expectedVersion": session_version(task), "feedback": "Reviewed availability; no message sent"}
            self.assertNotIn("error", self.act("complete_task", done, key="crm-task-done-00001"))
            self.assertTrue(self.act("complete_task", done, key="crm-task-done-00001")["replayed"])
            self.assertFalse(task.active)
            self.assertEqual(task.feedback, done["feedback"])
            notify.assert_not_called()
            email.assert_not_called()
        detail = self.act("lead_details", {"leadId": str(lead.id)})
        self.assertEqual(detail["tasks"][0]["state"], "done")
        self.assertEqual(detail["tasks"][0]["feedback"], done["feedback"])
        self.assertTrue(any(a["action"] == "complete_task" for a in detail["timeline"]))

    def test_crm_task_scope_and_automated_completion_guard(self):
        lead = self.new_lead()
        p = {"relatedType": "lead", "relatedId": str(lead.id), "summary": "Review", "due": str(fields.Date.today())}
        created = self.act("create_task", p)
        task = self.env["mail.activity"].browse(int(created["taskId"]))
        task.automated = True
        done = {"taskId": str(task.id), "expectedVersion": session_version(task), "feedback": "No"}
        self.assertEqual(self.act("complete_task", done)["error"]["code"], "AUTOMATED_TASK_REVIEW_REQUIRED")
        self.assertTrue(task.active)
        self.assertEqual(self.act("create_task", {**p, "due": "2026-02-30"})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.act("create_task", {**p, "relatedType": "res.users"})["error"]["code"], "INVALID_COMMAND")
        other = self.env["res.company"].create({"name": "Other task company"})
        lead.company_id = other
        self.assertEqual(self.act("complete_task", done)["error"]["code"], "LEAD_UNAVAILABLE")

    def test_crm_stage_proposal_does_not_mutate_stage_or_trial_attendance(self):
        lead = self.new_lead()
        original = lead.stage_id
        target = self.env["crm.stage"].create({"name": "Synthetic evaluation", "sequence": 90})
        p = {"leadId": str(lead.id), "expectedVersion": session_version(lead), "stageId": str(target.id), "reason": "Review readiness", "due": str(fields.Date.today())}
        result = self.act("propose_stage", p)
        self.assertNotIn("error", result, result)
        self.assertEqual(result["state"], "proposed")
        self.assertFalse(result["stageChanged"])
        self.assertEqual(lead.stage_id, original)
        self.assertEqual(self.env["mail.activity"].browse(int(result["taskId"])).res_id, lead.id)

    def test_crm_member_linked_task_preserves_exact_member(self):
        p = {"relatedType": "member", "relatedId": str(self.member.id), "summary": "Review member booking", "due": str(fields.Date.today())}
        result = self.act("create_task", p)
        self.assertNotIn("error", result, result)
        task = self.env["mail.activity"].browse(int(result["taskId"]))
        self.assertEqual(task.res_model, "dojo.member")
        self.assertEqual(task.res_id, self.member.id)
        done = self.act("complete_task", {"taskId": str(task.id), "expectedVersion": session_version(task), "feedback": "Reviewed"})
        self.assertNotIn("error", done, done)
        self.assertEqual(done["memberId"], str(self.member.id))

    def test_member_workspace_private_tabs_are_exact_and_role_scoped(self):
        self.member.write({"allergies": "Synthetic allergy", "medical_notes": "Private care note"})
        household = self.env["res.partner"].create({"name": "Synthetic household", "is_household": True, "company_id": self.company.id})
        self.partner.parent_id = household
        sibling_partner = self.env["res.partner"].create({"name": "Synthetic sibling", "parent_id": household.id, "company_id": self.company.id})
        sibling = self.env["dojo.member"].create({"partner_id": sibling_partner.id, "company_id": self.company.id})
        contact = self.env["dojo.emergency.contact"].create({"member_id": self.member.id, "name": "Synthetic emergency contact", "relationship": "Guardian", "phone": "555-0131"})
        attachment = self.env["ir.attachment"].create({"name": "Synthetic waiver.txt", "datas": "c3ludGhldGlj", "res_model": "dojo.member", "res_id": self.member.id, "company_id": self.company.id})
        self.env["ir.attachment"].create({"name": "Sibling private file.txt", "datas": "c3ludGhldGlj", "res_model": "dojo.member", "res_id": sibling.id, "company_id": self.company.id})
        result = self.act("member_workspace", {"memberId": str(self.member.id)})
        self.assertNotIn("error", result, result)
        self.assertEqual(result["member"]["id"], str(self.member.id))
        self.assertEqual(result["safety"]["medicalNotes"], "Private care note")
        self.assertEqual(result["safety"]["contacts"][0]["id"], str(contact.id))
        self.assertIn(str(sibling.id), [m["id"] for m in result["family"]["members"]])
        self.assertEqual([f["id"] for f in result["documents"]["files"]], [str(attachment.id)])
        self.assertNotIn("datas", result["documents"]["files"][0])
        self.assertNotIn("contact_ref", json.dumps(result))
        instructor = self.act("member_workspace", {"memberId": str(self.member.id)}, role="instructor")
        self.assertNotIn("error", instructor, instructor)
        for section in ("family", "safety", "documents", "billing"):
            self.assertIsNone(instructor[section])
        for role in ("guardian", "unassigned"):
            self.assertEqual(self.act("member_workspace", {"memberId": str(self.member.id)}, role=role)["error"]["status"], 403)
        other = self.env["res.company"].create({"name": "Foreign private tabs"})
        outside_partner = self.env["res.partner"].create({"name": "Foreign member", "company_id": other.id})
        outsider = self.env["dojo.member"].create({"partner_id": outside_partner.id, "company_id": other.id})
        self.assertEqual(outsider.company_id, other)
        self.assertEqual(self.act("member_workspace", {"memberId": str(outsider.id)})["error"]["code"], "MEMBER_UNAVAILABLE")

    def test_crm_evidence_uses_recorded_facts_and_due_tasks(self):
        lead = self.new_lead()
        context = self.act("crm_context", {"query": lead.name})
        evidence = next(l["evidence"] for l in context["leads"] if l["id"] == str(lead.id))
        self.assertEqual(evidence["score"], sum(r["points"] for r in evidence["reasons"]))
        self.assertEqual(sum(r["available"] for r in evidence["reasons"]), 100)
        self.assertFalse(evidence["trialLinked"])
        self.assertFalse(evidence["memberLinked"])
        created = self.act("create_task", {"relatedType": "lead", "relatedId": str(lead.id), "summary": "Overdue evidence", "due": str(fields.Date.today())})
        self.env["mail.activity"].browse(int(created["taskId"])).date_deadline = fields.Date.today() - timedelta(days=1)
        after = self.act("lead_details", {"leadId": str(lead.id)})["lead"]["evidence"]
        self.assertEqual(after["priority"], "Now")
        self.assertEqual(after["overdueTasks"], 1)
        self.assertEqual(after["score"], evidence["score"] + 15)
        self.assertEqual([a["id"] for a in context["assignees"]], [str(self.users["manager"].id)])

    def test_crm_internal_note_persists_once_without_notifications(self):
        lead = self.new_lead()
        lead.message_subscribe(partner_ids=[self.users["guardian"].partner_id.id])
        payload = {"leadId": str(lead.id), "expectedVersion": session_version(lead), "note": "Internal <script>literal</script> & review"}
        with patch.object(type(lead), "_notify_thread_by_email") as email:
            first = self.act("add_lead_note", payload, key="crm-note-request-001")
            self.assertNotIn("error", first, first)
            again = self.act("add_lead_note", payload, key="crm-note-request-001")
            self.assertEqual(first["noteId"], again["noteId"])
            self.assertTrue(again["replayed"])
            self.assertFalse(first["messageSent"])
            email.assert_not_called()
        notes = self.act("lead_details", {"leadId": str(lead.id)})["notes"]
        self.assertEqual([n["text"] for n in notes if n["id"] == first["noteId"]], [payload["note"]])
        self.grants["manager"].role = "instructor"
        self.assertEqual(self.act("add_lead_note", payload, key="crm-note-request-001")["error"]["status"], 403)

    def test_crm_preparations_create_native_review_tasks_only(self):
        lead = self.new_lead()
        stage = lead.stage_id
        members = self.env["dojo.member"].search_count([])
        bookings = self.env["dojo.class.enrollment"].search_count([])
        for kind in ("follow_up", "trial", "reactivation", "conversion"):
            payload = {"leadId": str(lead.id), "expectedVersion": session_version(lead), "kind": kind, "reason": "Synthetic staff review", "due": str(fields.Date.today())}
            key = "crm-prepare-" + kind + "-0001"
            first = self.act("prepare_lead_action", payload, key=key)
            self.assertNotIn("error", first, first)
            again = self.act("prepare_lead_action", payload, key=key)
            self.assertEqual(first["taskId"], again["taskId"])
            self.assertTrue(again["replayed"])
            task = self.env["mail.activity"].browse(int(first["taskId"]))
            self.assertEqual(task.res_id, lead.id)
            self.assertIn(payload["reason"], task.note)
            for field in ("messageSent", "bookingCreated", "membershipCreated"):
                self.assertFalse(first[field])
            self.assertEqual(self.act("prepare_lead_action", payload, role="instructor")["error"]["status"], 403)
        self.assertEqual(lead.stage_id, stage)
        self.assertEqual(self.env["dojo.member"].search_count([]), members)
        self.assertEqual(self.env["dojo.class.enrollment"].search_count([]), bookings)
        self.assertEqual(self.act("prepare_lead_action", {**payload, "kind": {}})["error"]["code"], "INVALID_COMMAND")

    def test_crm_owner_assignment_requires_current_site_grant(self):
        lead = self.new_lead()
        lead.write({"user_id": False, "company_id": self.company.id})
        self.assertEqual(lead.company_id, self.company)
        payload = {"leadId": str(lead.id), "expectedVersion": session_version(lead), "ownerId": str(self.users["manager"].id)}
        grants = self.env["dojo.hub.grant"].search_count([])
        self.assertEqual(self.act("assign_lead", {**payload, "ownerId": str(self.users["unassigned"].id)})["error"]["status"], 403)
        saved = self.act("assign_lead", payload, key="crm-owner-request-001")
        self.assertNotIn("error", saved, saved)
        self.assertEqual(lead.user_id, self.users["manager"])
        self.assertTrue(self.act("assign_lead", payload, key="crm-owner-request-001")["replayed"])
        self.assertEqual(self.env["dojo.hub.grant"].search_count([]), grants)
        other = self.env["res.company"].create({"name": "Assignee other main company"})
        self.users["manager"].write({"company_ids": [(4, other.id)], "company_id": other.id})
        payload["expectedVersion"] = session_version(lead)
        self.assertNotIn("error", self.act("assign_lead", payload))
        self.assertEqual(lead.company_id, self.company)

    def _checkout_invoice(self):
        income = self.env['account.account'].create({'name': 'Checkout income', 'code': 'HBPI', 'account_type': 'income', 'company_ids': [(6, 0, [self.company.id])]})
        receivable = self.env['account.account'].create({'name': 'Checkout receivable', 'code': 'HBPR', 'account_type': 'asset_receivable', 'reconcile': True, 'company_ids': [(6, 0, [self.company.id])]})
        journal = self.env['account.journal'].create({'name': 'Checkout sales', 'code': 'HBPS', 'type': 'sale', 'company_id': self.company.id, 'default_account_id': income.id})
        self.partner.with_company(self.company).property_account_receivable_id = receivable
        invoice = self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'company_id': self.company.id, 'journal_id': journal.id, 'invoice_date': fields.Date.today(), 'subscription_id': self.subscription.id,
            'invoice_line_ids': [(0, 0, {'name': 'Synthetic tuition', 'quantity': 1, 'price_unit': 30, 'account_id': income.id, 'tax_ids': [(5, 0, 0)]})]})
        invoice.action_post()
        self.env['ir.config_parameter'].sudo().set_str('web.base.url', 'https://odoo.example.test')
        self.env['ir.config_parameter'].sudo().set_bool('account_payment.enable_portal_payment', True)
        invoice.flush_recordset()
        return invoice, {'memberId': str(self.member.id), 'invoiceId': str(invoice.id), 'expectedVersion': session_version(invoice), 'expectedAmount': 30}

    def test_checkout_uses_native_signed_invoice_and_creates_no_payment(self):
        from urllib.parse import urlsplit, parse_qs
        invoice, payload = self._checkout_invoice()
        before = self.env['payment.transaction'].search_count([])
        with patch.object(type(self.env['dojo.hub.service']), '_payment_availability', return_value=None):
            result = self.act('payment_link', payload, key='checkout-native-request-01')
            self.assertNotIn('error', result)
            query = parse_qs(urlsplit(result['checkoutUrl']).query)
            self.assertEqual(query['move_id'], [str(invoice.id)])
            self.assertEqual(query['amount'], ['30.0'])
            self.assertTrue(query['access_token'][0])
            self.assertTrue(query['payment_token'][0])
            replay = self.act('payment_link', payload, key='checkout-native-request-01')
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['checkoutUrl'], result['checkoutUrl'])
            self.assertEqual(invoice.amount_residual, 30)
            self.assertEqual(self.env['payment.transaction'].search_count([]), before)
            # An old request key must not retain financial access after demotion.
            self.grants['manager'].role = 'instructor'
            self.assertEqual(self.act('payment_link', payload, key='checkout-native-request-01')['error']['code'], 'FORBIDDEN')

    def test_checkout_rejects_changed_balances_versions_and_unlinked_invoices(self):
        invoice, payload = self._checkout_invoice()
        self.assertEqual(self.act('payment_link', {**payload, 'expectedAmount': 29})['error']['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.act('payment_link', {**payload, 'expectedVersion': 1})['error']['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.act('payment_link', {**payload, 'expectedAmount': True})['error']['code'], 'INVALID_COMMAND')
        invoice.subscription_id = False
        self.assertEqual(self.act('payment_link', payload)['error']['code'], 'INVOICE_UNAVAILABLE')

    def test_checkout_requires_online_configuration_and_rechecks_replay_scope(self):
        invoice, payload = self._checkout_invoice()
        self.env['ir.config_parameter'].sudo().set_bool('account_payment.enable_portal_payment', False)
        self.assertEqual(self.act('payment_link', payload)['error']['code'], 'PAYMENT_SETUP_REQUIRED')
        self.assertEqual(self.act('payment_link', payload, role='instructor')['error']['code'], 'FORBIDDEN')
        with patch.object(type(self.env['dojo.hub.service']), '_payment_availability', return_value=None):
            self.assertNotIn('error', self.act('payment_link', payload, key='checkout-scope-request-001'))
            invoice.subscription_id = False
            self.assertEqual(self.act('payment_link', payload, key='checkout-scope-request-001')['error']['code'], 'INVOICE_UNAVAILABLE')

    def test_checkout_checks_odoo20_published_provider_and_active_methods(self):
        invoice, payload = self._checkout_invoice()
        self.env['payment.provider'].sudo().search([]).write({'is_published': False})
        self.assertEqual(self.act('payment_link', payload)['error']['code'], 'PAYMENT_PROVIDER_UNAVAILABLE')
        # A placeholder provider must never be presented as working payments.
        self.env['payment.provider'].sudo().create({'name': 'Placeholder only', 'code': 'none', 'company_id': self.company.id, 'is_published': True})
        self.assertEqual(self.act('payment_link', payload)['error']['code'], 'PAYMENT_PROVIDER_UNAVAILABLE')

    def test_ai_activation_requires_owner_and_a_real_successful_draft(self):
        self.grants['manager'].role = 'owner'
        self.env['ir.config_parameter'].sudo().set_str('elevenlabs_connector.ai_provider', 'openai')
        with patch.dict(os.environ, {'DOJANG_OPENAI_API_KEY': 'synthetic-key-not-a-real-secret', 'DOJANG_OPENAI_CHAT_MODEL': 'gpt-4o-mini'}):
            settings = self.act('ai_settings')
            self.assertTrue(settings['configured'])
            self.assertNotIn('synthetic-key', json.dumps(settings))
            payload = {'expectedSiteVersion': settings['siteVersion'], 'expectedConfigVersion': settings['configVersion']}
            with patch('odoo.addons.ai_assistant.models.openai_conversation.complete', return_value=json.dumps({'summary': 'Guardian asks staff to review makeup options.', 'reply': 'Staff will review the available options and eligibility.'})) as provider:
                result = self.act('enable_ai', payload, key='ai-enable-test-request-01')
                self.assertEqual(result['state'], 'ai_enabled')
                self.assertTrue(self.site.ai_enabled)
                self.assertTrue(self.kiosk.integration_companion_ai_enabled)
                self.assertTrue(self.act('enable_ai', payload, key='ai-enable-test-request-01')['replayed'])
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(self.act('ai_settings')['lastCheck']['model'], 'gpt-4o-mini')
            self.grants['manager'].role = 'manager'
            self.assertEqual(self.act('enable_ai', payload, key='ai-enable-test-request-01')['error']['code'], 'FORBIDDEN')
        self.assertEqual(self.act('ai_settings', role='instructor')['error']['code'], 'FORBIDDEN')

    def test_ai_failure_rolls_back_flags_and_never_reports_template_as_ai(self):
        self.grants['manager'].role = 'owner'
        self.env['ir.config_parameter'].sudo().set_str('elevenlabs_connector.ai_provider', 'openai')
        with patch.dict(os.environ, {'DOJANG_OPENAI_API_KEY': 'synthetic-key-not-a-real-secret'}):
            settings = self.act('ai_settings')
            payload = {'expectedSiteVersion': settings['siteVersion'], 'expectedConfigVersion': settings['configVersion']}
            with patch('odoo.addons.ai_assistant.models.openai_conversation.complete', return_value='not valid draft JSON'):
                self.assertEqual(self.act('enable_ai', payload)['error']['code'], 'AI_CHECK_FAILED')
            self.assertFalse(self.site.ai_enabled)
            self.assertFalse(self.kiosk.integration_companion_ai_enabled)
            self.assertIsNone(self.act('ai_settings')['lastCheck'])
        with patch.dict(os.environ, {'DOJANG_OPENAI_API_KEY': ''}):
            self.assertFalse(self.act('ai_settings')['configured'])
            self.assertEqual(self.act('enable_ai', payload)['error']['code'], 'AI_CONFIGURATION_REQUIRED')
