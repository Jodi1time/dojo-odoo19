"""Native Odoo regression tests. Run on a disposable synthetic database only."""
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestKioskSessionFirstV2(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Synthetic integration program", "company_id": cls.company.id})
        cls.partner = cls.env["res.partner"].create({"name": "Maya Test", "email": "synthetic@example.invalid"})
        cls.member = cls.env["dojo.member"].create({"partner_id": cls.partner.id, "company_id": cls.company.id, "membership_state": "active"})
        cls.template = cls.env["dojo.class.template"].create({"name": "Synthetic class", "company_id": cls.company.id,
            "program_id": cls.program.id, "course_member_ids": [(6, 0, [cls.member.id])]})
        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now - timedelta(minutes=5), "end_datetime": now + timedelta(minutes=55), "state": "open", "capacity": 1})
        cls.plan = cls.env["dojo.subscription.plan"].create({"name": "Synthetic plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Synthetic active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].search([("currency_id", "=", cls.company.currency_id.id)], limit=1)
        if not pricelist:
            pricelist = cls.env["product.pricelist"].create({"name": "Synthetic pricelist", "currency_id": cls.company.currency_id.id})
        cls.subscription = cls.env["sale.subscription"].create({"partner_id": cls.partner.id,
            "company_id": cls.company.id, "template_id": cls.plan.template_id.id, "pricelist_id": pricelist.id,
            "stage_id": stage.id, "member_id": cls.member.id, "plan_id": cls.plan.id})
        cls.enrollment = cls.env["dojo.class.enrollment"].create({"session_id": cls.session.id, "member_id": cls.member.id,
            "status": "registered", "attendance_state": "pending"})
        cls.gateway_key = "synthetic-kiosk-credential-00000000000001"
        cls.staff_key = "synthetic-staff-credential-00000000000002"
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Synthetic kiosk", "pin_code": "123456",
            "company_id": cls.company.id, "integration_enabled": True, "integration_tenant_ref": "synthetic-school",
            "integration_key_hash": hashlib.sha256(cls.gateway_key.encode()).hexdigest(),
            "integration_staff_key_hash": hashlib.sha256(cls.staff_key.encode()).hexdigest(),
            "integration_member_ids": [(6, 0, [cls.member.id])], "integration_session_ids": [(6, 0, [cls.session.id])]})
        cls.service = cls.env["dojo.kiosk.service"]

    def command(self, **overrides):
        body = {"idempotencyKey": "test-request-key-0001", "correlationId": "test-correlation-0001",
                "payload": {"memberId": str(self.member.id), "sessionId": str(self.session.id)}}
        body.update(overrides)
        return body

    def call(self, action="checkin", params=None, gateway=None):
        return self.service._v2_dispatch(action, self.kiosk.kiosk_token,
            self.gateway_key if gateway is None else gateway, {"command": self.command()} if params is None else params)

    def attendance_count(self):
        return self.env["dojo.attendance.log"].search_count([("session_id", "=", self.session.id), ("member_id", "=", self.member.id)])

    def test_registered_member_can_check_in_when_session_capacity_is_full(self):
        self.assertEqual(self.session.seats_taken, self.session.capacity)
        result = self.call()
        self.assertNotIn("problem", result)
        self.assertEqual(result["receipt"]["memberId"], str(self.member.id))
        self.assertEqual(self.attendance_count(), 1)

    def test_same_idempotency_key_replays_without_duplicate_attendance(self):
        first = self.call()
        second = self.call(params={"command": self.command(correlationId="test-correlation-0002")})
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertTrue(second["replayed"])
        self.assertEqual(second["correlationId"], "test-correlation-0002")
        self.assertEqual(self.attendance_count(), 1)

    def test_member_not_on_selected_session_roster_is_rejected(self):
        self.enrollment.write({"status": "cancelled"})
        self.assertEqual(self.call()["problem"]["code"], "NOT_ON_ROSTER")
        self.assertEqual(self.attendance_count(), 0)

    def test_closed_session_is_rejected(self):
        self.session.write({"state": "done"})
        self.assertEqual(self.call()["problem"]["code"], "SESSION_NOT_OPEN")

    def test_idempotency_key_cannot_be_reused_for_different_action(self):
        self.call()
        self.assertEqual(self.call(params={"command": self.command(expectedVersion=1)})["problem"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_missing_gateway_credential_is_rejected(self):
        self.assertEqual(self.call(gateway="")["problem"]["code"], "FORBIDDEN")
        self.assertEqual(self.attendance_count(), 0)

    def test_staff_and_kiosk_credentials_are_separate(self):
        self.assertEqual(self.call("member", {"memberId": str(self.member.id)})["problem"]["code"], "FORBIDDEN")
        self.assertNotIn("problem", self.call("member", {"memberId": str(self.member.id)}, gateway=self.staff_key))
        self.assertEqual(self.call(gateway=self.staff_key)["problem"]["code"], "FORBIDDEN")

    def test_disabled_capability_is_rejected(self):
        self.kiosk.integration_enabled = False
        self.assertEqual(self.call()["problem"]["code"], "CAPABILITY_DISABLED")

    def test_scope_is_revalidated_before_replaying_receipt(self):
        self.call()
        self.kiosk.integration_member_ids = [(5, 0, 0)]
        self.assertEqual(self.call()["problem"]["code"], "MEMBER_UNAVAILABLE")

    def test_missing_session_scope_is_rejected(self):
        self.kiosk.integration_session_ids = [(5, 0, 0)]
        self.assertEqual(self.call()["problem"]["code"], "SESSION_UNAVAILABLE")

    def test_member_from_other_company_is_rejected(self):
        other = self.env["res.company"].create({"name": "Other synthetic tenant"})
        self.member.company_id = other
        self.assertEqual(self.call()["problem"]["code"], "MEMBER_UNAVAILABLE")

    def test_client_authority_fields_are_rejected(self):
        self.assertEqual(self.call(params={"command": self.command(companyId=999)})["problem"]["code"], "INVALID_COMMAND")

    def test_version_conflict_does_not_record_attendance(self):
        self.assertEqual(self.call(params={"command": self.command(expectedVersion=1)})["problem"]["code"], "VERSION_CONFLICT")
        self.assertEqual(self.attendance_count(), 0)

    def test_existing_absence_is_not_reported_as_success(self):
        self.env["dojo.attendance.log"].create({"session_id": self.session.id, "member_id": self.member.id,
            "enrollment_id": self.enrollment.id, "status": "absent"})
        self.assertEqual(self.call()["problem"]["code"], "ATTENDANCE_REVIEW_REQUIRED")

    def test_new_key_returns_existing_attendance_and_one_outbox_event(self):
        first = self.call()
        second = self.call(params={"command": self.command(idempotencyKey="test-request-key-0002")})
        self.assertEqual(first["receipt"]["attendanceId"], second["receipt"]["attendanceId"])
        self.assertTrue(second["receipt"]["alreadyRecorded"])
        self.assertEqual(self.env["dojo.kiosk.outbox"].sudo().search_count([("attendance_id", "=", int(first["receipt"]["attendanceId"]))]), 1)

    def test_staff_member_view_reflects_actual_attendance(self):
        before = self.call("member", {"memberId": str(self.member.id)}, gateway=self.staff_key)
        self.assertEqual(before["attendance"]["lastSevenDays"], 0)
        checkin = self.call()
        after = self.call("member", {"memberId": str(self.member.id)}, gateway=self.staff_key)
        self.assertEqual(after["attendance"]["lastSevenDays"], 1)
        self.assertEqual(after["attendance"]["latest"]["checkedInAt"], checkin["receipt"]["checkedInAt"])
        self.assertEqual(after["member"]["tenantId"], "synthetic-school")

    def test_public_payload_excludes_financial_and_contact_fields(self):
        serialized = json.dumps([self.call("roster", {"sessionId": str(self.session.id)}), self.call()])
        for private in (self.gateway_key, self.kiosk.kiosk_token, "synthetic@example.invalid", "credit_balance", "phone", "medical"):
            self.assertNotIn(private, serialized)

    def test_paused_subscription_is_rejected(self):
        self.subscription.paused = True
        self.assertEqual(self.call()["problem"]["code"], "ELIGIBILITY_REVIEW_REQUIRED")

    def test_outbox_failure_rolls_back_attendance_and_receipt(self):
        Outbox = type(self.env["dojo.kiosk.outbox"])
        with patch.object(Outbox, "create", side_effect=ValidationError("Synthetic failure")):
            self.assertIn("problem", self.call())
        self.assertEqual(self.attendance_count(), 0)
        self.assertEqual(self.env["dojo.kiosk.command.receipt"].sudo().search_count([("config_id", "=", self.kiosk.id)]), 0)

    def followup(self, operation="prepare", payload=None, gateway=None):
        self.kiosk.integration_companion_followup_enabled = True
        command = {"memberId": str(self.member.id), "sessionId": str(self.session.id),
                   "text": "My child cannot attend. Could we arrange a makeup?",
                   "idempotencyKey": "followup-request-key-0001", "correlationId": "followup-correlation-0001"}
        return self.service._companion_dispatch(self.kiosk.kiosk_token,
            self.staff_key if gateway is None else gateway, operation, command if payload is None else payload)

    def approve_followup(self, plan, **updates):
        command = {"memberId": str(self.member.id), "suggestionId": plan["suggestion"]["id"],
                   "idempotencyKey": "followup-approval-key-0001", "correlationId": "followup-correlation-0002"}
        command.update(updates)
        return self.followup("approve", command)

    def test_followup_prepare_does_not_change_attendance_or_publish_to_queue(self):
        result = self.followup()
        self.assertEqual(result["outcome"], "newTask")
        self.assertEqual(result["suggestion"]["capability"], "followup.save_internal")
        self.assertEqual(self.attendance_count(), 0)
        self.assertEqual(self.followup("context", {"memberId": str(self.member.id)})["followUps"], [])

    def test_followup_approval_updates_same_member_and_class_queue_once(self):
        plan = self.followup()
        approved = self.approve_followup(plan)
        self.assertNotIn("problem", approved)
        repeated = self.approve_followup(plan)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(approved["receipt"], repeated["receipt"])
        for scope in ({"memberId": str(self.member.id)}, {"sessionId": str(self.session.id)}, {}):
            rows = self.followup("context", scope)["followUps"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["memberId"], str(self.member.id))
            self.assertEqual(rows[0]["sessionId"], str(self.session.id))
        self.assertEqual(self.attendance_count(), 0)

    def test_followup_prepare_retry_reuses_plan(self):
        first, second = self.followup(), self.followup()
        self.assertEqual(first["suggestion"]["id"], second["suggestion"]["id"])
        self.assertEqual(self.env["dojo.companion.followup"].sudo().search_count([("config_id", "=", self.kiosk.id)]), 1)

    def test_followup_public_credential_is_rejected(self):
        self.assertEqual(self.followup(gateway=self.gateway_key)["problem"]["code"], "FORBIDDEN")

    def test_followup_scope_revoked_before_approval_is_rejected(self):
        plan = self.followup()
        self.kiosk.integration_member_ids = [(5, 0, 0)]
        self.assertEqual(self.approve_followup(plan)["problem"]["code"], "MEMBER_UNAVAILABLE")

    def test_followup_attendance_changed_before_approval_requires_new_plan(self):
        plan = self.followup()
        self.call()
        self.assertEqual(self.approve_followup(plan)["problem"]["code"], "VERSION_CONFLICT")

    def test_followup_without_registered_enrollment_rejected(self):
        self.enrollment.status = "cancelled"
        self.assertEqual(self.followup()["problem"]["code"], "NOT_ON_ROSTER")

    def test_followup_never_calls_model_without_explicit_enablement(self):
        Processor = type(self.env["ai.processor"])
        with patch.object(Processor, "_process_conversational_openai", side_effect=AssertionError("Model must not run")):
            plan = self.followup()
        self.assertIn("AI not enabled", str(plan["suggestion"]["preview"]))

    def test_followup_ai_is_only_drafting_and_cannot_execute(self):
        self.kiosk.integration_companion_ai_enabled = True
        Processor = type(self.env["ai.processor"])
        with patch.object(Processor, "_get_provider", return_value="openai"), patch.object(Processor, "_process_conversational_openai", return_value="Thanks for letting us know. We will review makeup options."):
            plan = self.followup()
        self.assertEqual(plan["suggestion"]["capability"], "followup.save_internal")
        self.assertIn("AI-assisted", str(plan["suggestion"]["preview"]))
        self.assertEqual(self.attendance_count(), 0)

    def test_followup_disabled_is_not_silently_enabled(self):
        self.kiosk.integration_companion_followup_enabled = False
        result = self.service._companion_dispatch(self.kiosk.kiosk_token, self.staff_key, "prepare", {})
        self.assertEqual(result["problem"]["code"], "CAPABILITY_DISABLED")

    def test_followup_request_key_cannot_change_report(self):
        self.followup()
        changed = {"memberId": str(self.member.id), "sessionId": str(self.session.id),
                   "text": "A different report", "idempotencyKey": "followup-request-key-0001",
                   "correlationId": "followup-correlation-0003"}
        self.assertEqual(self.followup(payload=changed)["problem"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_followup_old_proposal_requires_preparation_again(self):
        plan = self.followup()
        record = self.env["dojo.companion.followup"].sudo().browse(int(plan["suggestion"]["id"].split(":")[1]))
        record.create_date = fields.Datetime.now() - timedelta(hours=1)
        self.assertEqual(self.approve_followup(plan)["problem"]["code"], "VERSION_CONFLICT")

    def test_followup_ai_error_returns_explicit_template(self):
        from odoo.exceptions import UserError
        self.kiosk.integration_companion_ai_enabled = True
        Processor = type(self.env["ai.processor"])
        with patch.object(Processor, "_get_provider", return_value="openai"), patch.object(Processor, "_process_conversational_openai", side_effect=UserError("synthetic provider unavailable")):
            plan = self.followup()
        self.assertIn("AI unavailable", str(plan["suggestion"]["preview"]))
