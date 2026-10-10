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
