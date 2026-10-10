"""Authenticated ingress and a durable, approval-gated WhatsMax outbox.

The vendor send endpoint has no proven idempotency guarantee. A dispatch is
committed BEFORE networking, and uncertain attempts are never auto-retried.
"""
import hashlib
import hmac
import json
import time
from datetime import timedelta

import requests
from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import record_id, session_version
from .service import HubProblem, exact, fingerprint, text


def verify_signature(secret, timestamp, signature, body, now=None):
    now = time.time() if now is None else now
    try:
        valid_time = isinstance(timestamp, str) and timestamp.isdigit() and abs(now - int(timestamp)) <= 300
    except (TypeError, ValueError, OverflowError):
        return False
    if not secret or not valid_time or not isinstance(signature, str) or len(body) > 16384:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + expected, signature)


class HubIngress(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _ingest(self, site_id, body, timestamp, signature):
        site = self.env["dojo.hub.site"].sudo().search([("id", "=", record_id(site_id)), ("active", "=", True)], limit=1)
        if not site or not verify_signature(site._secret("webhook_secret_env"), timestamp, signature, body):
            raise HubProblem("INVALID_SIGNATURE", 403)
        try:
            event = json.loads(body)
        except (ValueError, UnicodeError):
            raise HubProblem("INVALID_EVENT", 400)
        if not isinstance(event, dict) or event.get("workspaceRef") != site.workspace_ref:
            raise HubProblem("WORKSPACE_MISMATCH", 403)
        kind = event.get("type")
        if kind == "message.received":
            exact(event, "type workspaceRef eventRef channel contactRef text")
            if event["channel"] not in ("sms", "whatsapp"):
                raise HubProblem("UNSUPPORTED_CHANNEL", 422)
            reference, contact, source = text(event["eventRef"], 128), text(event["contactRef"], 128), text(event["text"], 1500)
            self.env["dojo.kiosk.service"]._v2_lock("hub-inbound:%s:%s" % (site.id, reference))
            Message = self.env["dojo.hub.message"].sudo()
            existing = Message.search([("site_id", "=", site.id), ("event_ref", "=", reference)], limit=1)
            if existing:
                if existing.fingerprint != fingerprint(event):
                    raise HubProblem("IDEMPOTENCY_CONFLICT")
                return {"accepted": True, "replayed": True}
            binding = self.env["dojo.hub.guardian"].sudo().search([("site_id", "=", site.id), ("channel", "=", event["channel"]),
                ("contact_ref", "=", contact), ("active", "=", True)], limit=1)
            Message.create({"site_id": site.id, "event_ref": reference, "fingerprint": fingerprint(event), "channel": event["channel"],
                "contact_ref": contact, "source_text": source, "binding_id": binding.id if binding else False, "state": "received" if binding else "unmatched"})
            return {"accepted": True, "replayed": False}
        if kind == "delivery.status":
            exact(event, "type workspaceRef eventRef providerRef status")
            if event["status"] not in ("delivered", "failed"):
                raise HubProblem("INVALID_EVENT", 400)
            reference, provider = text(event["eventRef"], 128), text(event["providerRef"], 200)
            self.env["dojo.kiosk.service"]._v2_lock("hub-callback:%s:%s" % (site.id, reference))
            Event = self.env["dojo.hub.provider.event"].sudo()
            existing = Event.search([("site_id", "=", site.id), ("event_ref", "=", reference)], limit=1)
            if existing:
                if existing.fingerprint != fingerprint(event):
                    raise HubProblem("IDEMPOTENCY_CONFLICT")
                return {"accepted": True, "replayed": True}
            row = Event.create({"site_id": site.id, "event_ref": reference, "provider_ref": provider, "fingerprint": fingerprint(event), "status": event["status"]})
            self._apply_provider_event(row)
            return {"accepted": True, "replayed": False}
        raise HubProblem("INVALID_EVENT", 400)

    def _apply_provider_event(self, event):
        delivery = self.env["dojo.hub.delivery"].sudo().search([("site_id", "=", event.site_id.id), ("provider_ref", "=", event.provider_ref)], limit=1)
        if not delivery:
            return  # Persist early/out-of-order events until the send result arrives.
        self._lock(delivery)
        if delivery.state in ("accepted", "failed", "uncertain"):
            delivery.write({"state": event.status, "result_code": "authenticated_provider_status"})
        event.delivery_id = delivery


class HubDeliveryWorker(models.Model):
    _inherit = "dojo.hub.delivery"

    def _validate_approval(self):
        self.ensure_one()
        site, binding, message = self.site_id, self.binding_id, self.message_id
        if not site.active or not site.outbound_enabled or not site.provider_origin or not site._secret("provider_token_env"):
            return "provider_not_configured"
        if site.workspace_ref != self.workspace_ref or site.provider_origin != self.provider_origin:
            return "provider_configuration_changed"
        grant = self.env["dojo.hub.grant"].sudo().search([("site_id", "=", site.id), ("user_id", "=", self.approved_by.id), ("active", "=", True)], limit=1)
        if not grant or grant.role not in ("owner", "manager", "instructor") or not self.approved_by.active or site.company_id not in self.approved_by.company_ids:
            return "approver_access_revoked"
        if not binding.active or not binding.allow_reply or binding.site_id != site or binding.contact_ref != self.contact_ref or binding.channel != self.channel or message.member_id not in binding.member_ids:
            return "guardian_authority_changed"
        if str(session_version(binding)) != self.binding_version:
            return "guardian_verification_changed"
        if message.member_id.company_id != site.company_id or not message.member_id.active or message.session_id.company_id != site.company_id or self.approved_revision != message.revision:
            return "approved_context_changed"
        if grant.role == "instructor" and message.session_id not in grant.session_ids:
            return "approver_scope_revoked"
        try:
            service = self.env["dojo.hub.service"].with_user(self.approved_by)
            service._member(site, grant, str(message.member_id.id))
            service._session(site, grant, str(message.session_id.id))
        except HubProblem:
            return "approver_scope_revoked"
        return None

    def _send_once(self):
        """Called only for a previously committed dispatching row. No auto retry."""
        self.ensure_one()
        if self.state != "dispatching":
            return
        failure = self._validate_approval()
        if failure:
            self.write({"state": "cancelled", "result_code": failure})
            return
        try:
            profile = requests.get(self.site_id.provider_origin + "/api/v1/auth/me",
                headers={"Authorization": "Bearer " + self.site_id._secret("provider_token_env")}, timeout=(5, 10), allow_redirects=False)
            if profile.status_code != 200 or len(profile.content) > 16384:
                self.write({"state": "cancelled", "result_code": "provider_identity_not_verified"})
                return
            identity = profile.json()
            if not isinstance(identity, dict) or str(identity.get("workspace_id")) != self.workspace_ref or identity.get("demo_mode") is not False:
                self.write({"state": "cancelled", "result_code": "provider_workspace_mismatch"})
                return
            response = requests.post(self.site_id.provider_origin + "/api/v1/messages/send",
                headers={"Authorization": "Bearer " + self.site_id._secret("provider_token_env"), "Content-Type": "application/json"},
                json={"contact_id": int(self.contact_ref), "channel": self.channel, "body": self.body},
                timeout=(5, 20), allow_redirects=False)
            # The vendor may have sent before an HTTP error/timeout. Never retry blindly.
            if not 200 <= response.status_code < 300 or len(response.content) > 16384:
                self.write({"state": "uncertain", "result_code": "provider_result_requires_reconciliation"})
                return
            result = response.json()
            provider_ref = result.get("provider_message_id") if isinstance(result, dict) else None
            if not isinstance(provider_ref, str) or not 1 <= len(provider_ref.strip()) <= 200:
                self.write({"state": "uncertain", "result_code": "provider_receipt_missing"})
                return
            self.write({"state": "accepted", "provider_ref": provider_ref, "result_code": "accepted_not_delivery_confirmed"})
            for event in self.env["dojo.hub.provider.event"].sudo().search([("site_id", "=", self.site_id.id), ("provider_ref", "=", provider_ref)], order="id"):
                self.env["dojo.hub.service"]._apply_provider_event(event)
        except (requests.RequestException, ValueError, TypeError, OverflowError):
            self.write({"state": "uncertain", "result_code": "provider_result_requires_reconciliation"})

    def _cron_dispatch(self):
        # This cron is disabled at installation and must be enabled by the operator.
        # The committed dispatch marker prevents another worker/restart from resending.
        self.env.cr.execute("SELECT id FROM dojo_hub_delivery WHERE state = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
        row = self.sudo().browse([v[0] for v in self.env.cr.fetchall()])
        if row:
            failure = row._validate_approval()
            row.write({"state": "cancelled" if failure else "dispatching", "result_code": failure or "dispatch_started", "dispatched_at": fields.Datetime.now()})
            self.env.cr.commit()
            if not failure:
                row._send_once()
                self.env.cr.commit()
        # A crashed request is ambiguous, never automatically eligible for resend.
        stale = self.sudo().search([("state", "=", "dispatching"), ("dispatched_at", "<", fields.Datetime.now() - timedelta(minutes=5))])
        stale.write({"state": "uncertain", "result_code": "worker_interrupted_reconcile_before_resend"})
