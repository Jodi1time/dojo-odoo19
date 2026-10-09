# -*- coding: utf-8 -*-
"""
AI Communication Service — Extends ai.assistant.service with direct email/SMS intents.

Handlers:
- send_email      — email a specific member or guardian (instructor, admin)
- send_sms        — SMS a specific member or guardian (instructor, admin)
- email_blast     — mass email to a filtered audience (admin only)
- sms_blast       — mass SMS to a filtered audience (admin only)
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ─── Membership state labels (for blast filters) ─────────────────────────────
_STATE_LABELS = {
    "active": "active members",
    "pending": "pending members",
    "paused": "paused members",
    "cancelled": "cancelled members",
    "trial": "trial members",
}


class AiCommunicationService(models.AbstractModel):
    _inherit = "ai.assistant.service"

    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _resolve_target_partner(self, params, resolved_data):
        """
        Resolve the target contact (res.partner) from intent params.

        Tries (in order):
          1. resolved_data['member_id']
          2. params['member_name']
          3. params['email']

        Returns (partner, display_name) or (None, error_message).
        """
        member_id = resolved_data.get("member_id")
        if member_id:
            member = self.env["dojo.member"].browse(int(member_id))
            if member.exists():
                # Prefer the primary guardian contact for communication
                guardian = None
                if hasattr(member, "primary_guardian_id") and member.primary_guardian_id:
                    guardian = member.primary_guardian_id
                partner = guardian if guardian else member.partner_id
                if partner and partner.exists():
                    return partner, member.name
                return None, f"No contact found for member {member.name}."

        member_name = params.get("member_name") or params.get("contact_name")
        if member_name:
            # Search dojo.member first
            member = self.env["dojo.member"].search([("name", "ilike", member_name)], limit=1)
            if member:
                guardian = None
                if hasattr(member, "primary_guardian_id") and member.primary_guardian_id:
                    guardian = member.primary_guardian_id
                partner = guardian if guardian else member.partner_id
                if partner and partner.exists():
                    return partner, member.name
            # Fall back to res.partner search
            partner = self.env["res.partner"].search([("name", "ilike", member_name)], limit=1)
            if partner:
                return partner, partner.name
            return None, f"Could not find anyone named '{member_name}'."

        email = params.get("email")
        if email:
            partner = self.env["res.partner"].search([("email", "=", email)], limit=1)
            if partner:
                return partner, partner.name
            return None, f"No contact found with email '{email}'."

        return None, "Please specify who to contact (member name or email)."

    # ═══════════════════════════════════════════════════════════════════════════
    # Handler: send_email
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_send_email(self, intent_data, resolved_data, action_log):
        """Send an email to a member or guardian via mail.thread.

        Parameters:
          - member_name / contact_name / email: who to email
          - subject: email subject (auto-generated from body if missing)
          - body / message: email body
        """
        params = intent_data.get("parameters", {}) if intent_data else {}

        partner, display_name = self._resolve_target_partner(params, resolved_data)
        if not partner:
            return {"success": False, "error": display_name}  # display_name holds error

        subject = params.get("subject") or params.get("title")
        body = params.get("body") or params.get("message") or params.get("content")

        if not body:
            return {"success": False, "error": "Please provide the email message body."}

        # Build a subject from body if not provided
        if not subject:
            subject = body[:60].rstrip(".,!?") + ("..." if len(body) > 60 else "")

        try:
            # Send via mail.thread — goes through Odoo's mail system, shows in partner chatter
            partner.sudo().message_post(
                body=body,
                subject=subject,
                message_type="email",
                subtype_xmlid="mail.mt_comment",
            )
            return {
                "success": True,
                "message": f"Email sent to {display_name} ({partner.email or partner.name}): '{subject}'",
                "data": {
                    "partner_id": partner.id,
                    "partner_name": partner.name,
                    "email": partner.email,
                    "subject": subject,
                },
            }
        except Exception as exc:
            _logger.error("AI send_email failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════════
    # Handler: send_sms
    # ═══════════════════════════════════════════════════════════════════════════
    @api.model
    def _handle_make_call(self, intent_data, resolved_data, action_log):
        """Place an outbound AI voice call to a member/guardian (ElevenLabs + Twilio).
        Falls back to a simulated call when the calling module or credentials aren't available.
        Parameters:
          - member_name / contact_name / email: who to call
          - purpose / message / reason: what the call is about
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        partner, display_name = self._resolve_target_partner(params, resolved_data)
        if not partner:
            return {"success": False, "error": display_name}
        phone = getattr(partner, "mobile", False) or partner.phone
        if not phone:
            return {"success": False, "error": f"{display_name} has no phone number on file."}
        purpose = params.get("purpose") or params.get("message") or params.get("reason") or "a quick check-in"

        # VoIP calling lives in the dojo_ai_caller module — degrade to a simulated
        # call if that module isn't installed or credentials aren't configured.
        voip_available = ("dojo.elevenlabs.caller" in self.env) and ("dojo.elevenlabs.phone" in self.env)
        api_key = False
        phone_rec = None
        agent_id = self.env["ir.config_parameter"].sudo().get_str("dojo_ai_caller.default_agent_id")
        if voip_available:
            phone_rec = self.env["dojo.elevenlabs.phone"].sudo().search([], limit=1)
            try:
                api_key = self.env["dojo.elevenlabs.caller"].sudo()._get_api_key()
            except Exception:
                api_key = False

        if not (voip_available and api_key and phone_rec and agent_id):
            _logger.info("AI make_call (mock): would call %s at %s about '%s'", display_name, phone, purpose)
            return {
                "success": True,
                "message": f"Call to {display_name} ({phone}) simulated — about '{purpose}'. "
                           f"Configure ElevenLabs + Twilio to place a real call.",
                "data": {"partner_id": partner.id, "partner_name": partner.name,
                         "phone": phone, "purpose": purpose, "mocked": True},
            }

        try:
            result = self.env["dojo.elevenlabs.caller"].sudo()._trigger_outbound_call(
                agent_id=agent_id,
                phone_number_id=phone_rec.elevenlabs_id,
                to_number=phone,
                dynamic_variables={"member_name": partner.name, "purpose": purpose},
            )
            conv_id = (result or {}).get("conversation_id")
            return {
                "success": True,
                "message": f"Calling {display_name} ({phone}) about '{purpose}'. Conversation ID: {conv_id}",
                "data": {"partner_id": partner.id, "phone": phone, "purpose": purpose, "conversation_id": conv_id},
            }
        except Exception as exc:
            _logger.error("AI make_call failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        
    @api.model
    def _handle_send_sms(self, intent_data, resolved_data, action_log):
        """Send an SMS to a member or guardian via dojo.send.message.wizard.

        Parameters:
          - member_name / contact_name: who to text
          - body / message: SMS body (max ~160 chars recommended)
        """
        params = intent_data.get("parameters", {}) if intent_data else {}

        member_id = resolved_data.get("member_id")
        member_name_param = params.get("member_name") or params.get("contact_name")

        # Try to use the dojo.send.message.wizard for consistent SMS delivery (uses Twilio)
        member = None
        if member_id:
            member = self.env["dojo.member"].browse(int(member_id))
        elif member_name_param:
            member = self.env["dojo.member"].search([("name", "ilike", member_name_param)], limit=1)

        body = params.get("body") or params.get("message") or params.get("content")
        if not body:
            return {"success": False, "error": "Please provide the SMS message."}

        if member and member.exists():
            try:
                wizard = self.env["dojo.send.message.wizard"].create({
                    "member_ids": [(6, 0, [member.id])],
                    "subject": "Message from Dojo",
                    "message_body": body,
                    "send_email": False,
                    "send_sms": True,
                })
                wizard.action_send()
                return {
                    "success": True,
                    "message": f"SMS sent to {member.name}: '{body[:60]}{'...' if len(body) > 60 else ''}'",
                    "data": {"member_id": member.id, "member_name": member.name, "body": body},
                }
            except Exception as exc:
                _logger.error("AI send_sms (wizard) failed: %s", exc, exc_info=True)
                # Fall through to direct partner SMS
        else:
            # Fall back to direct partner
            partner, display_name = self._resolve_target_partner(params, resolved_data)
            if not partner:
                return {"success": False, "error": display_name}

            phone = getattr(partner, "mobile", False) or partner.phone
            if not phone:
                return {"success": False, "error": f"{display_name} has no phone number on file."}

            try:
                partner.sudo()._message_sms(body=body, partner_ids=partner.ids)
                return {
                    "success": True,
                    "message": f"SMS sent to {display_name} ({phone}): '{body[:60]}{'...' if len(body) > 60 else ''}'",
                    "data": {"partner_id": partner.id, "partner_name": partner.name, "phone": phone, "body": body},
                }
            except Exception as exc:
                _logger.error("AI send_sms (direct) failed: %s", exc, exc_info=True)
                return {"success": False, "error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════════
    # Handler: email_blast (admin-only)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_email_blast(self, intent_data, resolved_data, action_log):
        """Create and send a mass email campaign.

        Parameters:
          - subject (required): email subject
          - body / message (required): email body (plain text or HTML)
          - membership_state: filter audience by state (active, trial, paused, etc.)
          - schedule / send_now: if True, triggers send immediately; else creates draft
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        subject = params.get("subject") or params.get("title")
        body = params.get("body") or params.get("message") or params.get("content")

        if not subject:
            return {"success": False, "error": "Please provide an email subject."}
        if not body:
            return {"success": False, "error": "Please provide the email body."}

        # Build audience domain
        membership_state = params.get("membership_state", "active")
        mailing_domain = [("membership_state", "=", membership_state)]
        audience_label = _STATE_LABELS.get(membership_state, membership_state)

        try:
            mailing = self.env["mailing.mailing"].sudo().create({
                "subject": subject,
                "body_html": f"<p>{body}</p>",
                "mailing_type": "mail",
                "mailing_model_id": self.env.ref("dojo_core.model_dojo_member").id,
                "mailing_domain": str(mailing_domain),
                "state": "draft",
            })

            send_now = params.get("send_now", False) or params.get("schedule") == "now"
            if send_now:
                mailing.action_launch()
                return {
                    "success": True,
                    "message": f"Email blast sent to all {audience_label}: '{subject}'",
                    "data": {"mailing_id": mailing.id, "audience": audience_label, "sent": True},
                }

            return {
                "success": True,
                "message": f"Email campaign draft created for {audience_label}: '{subject}'. Review and send from Marketing > Email.",
                "data": {"mailing_id": mailing.id, "audience": audience_label, "sent": False},
            }
        except Exception as exc:
            _logger.error("AI email_blast failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════════
    # Handler: sms_blast (admin-only)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_sms_blast(self, intent_data, resolved_data, action_log):
        """Create and send a mass SMS campaign.

        Parameters:
          - body / message (required): SMS text (keep under 160 chars for single message)
          - membership_state: filter audience by state (active, trial, paused, etc.)
          - send_now: if True, triggers send immediately; else creates draft
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        body = params.get("body") or params.get("message") or params.get("content")

        if not body:
            return {"success": False, "error": "Please provide the SMS message text."}

        membership_state = params.get("membership_state", "active")
        mailing_domain = [("membership_state", "=", membership_state)]
        audience_label = _STATE_LABELS.get(membership_state, membership_state)

        try:
            mailing = self.env["mailing.mailing"].sudo().create({
                "subject": body[:60],
                "body_plaintext": body,
                "mailing_type": "sms",
                "mailing_model_id": self.env.ref("dojo_core.model_dojo_member").id,
                "mailing_domain": str(mailing_domain),
                "state": "draft",
            })

            send_now = params.get("send_now", False)
            if send_now:
                mailing.action_launch()
                return {
                    "success": True,
                    "message": f"SMS blast sent to all {audience_label}: '{body[:60]}...'",
                    "data": {"mailing_id": mailing.id, "audience": audience_label, "sent": True},
                }

            return {
                "success": True,
                "message": f"SMS campaign draft created for {audience_label}. Review and send from Marketing > SMS.",
                "data": {"mailing_id": mailing.id, "audience": audience_label, "sent": False},
            }
        except Exception as exc:
            _logger.error("AI sms_blast failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
