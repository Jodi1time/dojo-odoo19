"""Private hub records. Only the fixed service API exposes these to non-admins."""
import os
import re
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HubSite(models.Model):
    _name = "dojo.hub.site"
    _description = "Companion Site"
    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="restrict")
    kiosk_config_id = fields.Many2one("dojo.kiosk.config", required=True, ondelete="restrict")
    workspace_ref = fields.Char(required=True, help="Exact WhatsMax workspace identifier; never inferred from a sender.")
    provider_origin = fields.Char(help="Administrator-approved HTTPS WhatsMax origin, without path.")
    outbound_enabled = fields.Boolean(default=False)
    ai_enabled = fields.Boolean(default=False)
    webhook_secret_env = fields.Char(help="Name of the Odoo process environment variable holding the inbound HMAC secret.")
    provider_token_env = fields.Char(help="Name of the Odoo process environment variable holding the workspace-scoped WhatsMax token.")
    _unique_site_config = models.Constraint("unique(kiosk_config_id)", "This kiosk configuration already has a hub site.")

    @api.constrains("company_id", "kiosk_config_id", "provider_origin", "webhook_secret_env", "provider_token_env")
    def _check_config(self):
        for site in self:
            if site.company_id != site.kiosk_config_id.company_id:
                raise ValidationError("The hub and kiosk must belong to the same company.")
            for name in (site.webhook_secret_env, site.provider_token_env):
                if name and not re.fullmatch(r"DOJANG_HUB_[A-Z0-9_]{1,100}", name):
                    raise ValidationError("Use a DOJANG_HUB_ environment variable for secrets.")
            if site.provider_origin:
                value = urlsplit(site.provider_origin)
                if value.scheme != "https" or not value.hostname or value.username or value.password or value.path or value.query or value.fragment:
                    raise ValidationError("Set an exact HTTPS provider origin, without credentials or path.")

    def _secret(self, field):
        self.ensure_one()
        if field not in ("webhook_secret_env", "provider_token_env"):
            return ""
        value = os.environ.get(self[field] or "", "")
        return value if 32 <= len(value) <= 4096 and not any(c in value for c in "\r\n") else ""


class HubGrant(models.Model):
    _name = "dojo.hub.grant"
    _description = "Companion User Access"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    active = fields.Boolean(default=True)
    role = fields.Selection([(r, r.title()) for r in ("owner", "manager", "instructor", "guardian", "member")], required=True)
    member_ids = fields.Many2many("dojo.member", string="Explicit member scope")
    session_ids = fields.Many2many("dojo.class.session", string="Explicit class scope for instructors")
    _unique_user_site = models.Constraint("unique(site_id, user_id)", "User already provisioned for this site.")

    @api.constrains("site_id", "user_id", "member_ids", "session_ids", "role")
    def _check_scope(self):
        for grant in self:
            company = grant.site_id.company_id
            if company not in grant.user_id.company_ids or any(m.company_id != company for m in grant.member_ids) or any(s.company_id != company for s in grant.session_ids):
                raise ValidationError("Access cannot cross company boundaries.")
            if grant.role == "member" and (len(grant.member_ids) != 1 or grant.member_ids.partner_id != grant.user_id.partner_id):
                raise ValidationError("Member access must link to that user's own partner.")


class HubGuardian(models.Model):
    _name = "dojo.hub.guardian"
    _description = "Verified Guardian Channel Binding"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict")
    member_ids = fields.Many2many("dojo.member", string="Verified children")
    channel = fields.Selection([("sms", "SMS"), ("whatsapp", "WhatsApp")], required=True)
    contact_ref = fields.Char(required=True, help="Exact provider contact ID in this site's workspace.")
    verification_ref = fields.Char(required=True, help="Reference to staff's completed identity/guardian authority verification.")
    verified_by = fields.Many2one("res.users", required=True, default=lambda self: self.env.user)
    verified_at = fields.Datetime(required=True, default=fields.Datetime.now)
    active = fields.Boolean(default=True)
    allow_reply = fields.Boolean(default=False, help="Transactional replies are allowed on this verified channel.")
    _unique_guardian_channel = models.Constraint("unique(site_id, channel, contact_ref)", "Channel identity already linked.")

    @api.constrains("site_id", "member_ids", "partner_id", "contact_ref", "verification_ref")
    def _check_binding(self):
        for row in self:
            company = row.site_id.company_id
            if not row.member_ids or any(m.company_id != company for m in row.member_ids) or (row.partner_id.company_id and row.partner_id.company_id != company):
                raise ValidationError("Verify at least one child in the same company.")
            if not row.contact_ref or not re.fullmatch(r"[1-9][0-9]{0,9}", row.contact_ref) or int(row.contact_ref) > 2147483647 or not row.verification_ref.strip():
                raise ValidationError("A provider identity and verification reference are required.")


class HubMessage(models.Model):
    _name = "dojo.hub.message"
    _description = "Companion Inbound Message"
    _order = "id desc"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    event_ref = fields.Char(required=True, index=True)
    fingerprint = fields.Char(required=True)
    channel = fields.Char(required=True)
    contact_ref = fields.Char(required=True)
    source_text = fields.Text(required=True)
    received_at = fields.Datetime(default=fields.Datetime.now, required=True)
    binding_id = fields.Many2one("dojo.hub.guardian", ondelete="restrict")
    member_id = fields.Many2one("dojo.member", ondelete="restrict")
    session_id = fields.Many2one("dojo.class.session", ondelete="restrict")
    state = fields.Selection([("unmatched", "Needs identity verification"), ("received", "Needs class review"), ("resolved", "Ready for follow-up")], required=True)
    summary = fields.Text()
    reply_draft = fields.Text()
    draft_mode = fields.Char()
    revision = fields.Integer(default=1, required=True)
    _unique_inbound = models.Constraint("unique(site_id, event_ref)", "Inbound event already recorded.")


class HubReceipt(models.Model):
    _name = "dojo.hub.receipt"
    _description = "Companion Action Audit"
    _order = "id desc"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="restrict")
    request_key = fields.Char(required=True)
    operation = fields.Char(required=True)
    fingerprint = fields.Char(required=True)
    member_id = fields.Many2one("dojo.member", ondelete="restrict")
    session_id = fields.Many2one("dojo.class.session", ondelete="restrict")
    response_json = fields.Text(required=True)
    _unique_action = models.Constraint("unique(site_id, user_id, request_key)", "Action key already used.")


class HubDelivery(models.Model):
    _name = "dojo.hub.delivery"
    _description = "Approved Companion Outbound Delivery"
    _order = "id desc"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    message_id = fields.Many2one("dojo.hub.message", required=True, ondelete="restrict")
    binding_id = fields.Many2one("dojo.hub.guardian", required=True, ondelete="restrict")
    approved_by = fields.Many2one("res.users", required=True, ondelete="restrict")
    approved_at = fields.Datetime(required=True, default=fields.Datetime.now)
    approved_revision = fields.Integer(required=True)
    binding_version = fields.Char(required=True)
    workspace_ref = fields.Char(required=True)
    provider_origin = fields.Char(required=True)
    channel = fields.Char(required=True)
    contact_ref = fields.Char(required=True)
    body = fields.Text(required=True)
    state = fields.Selection([(v, v.title()) for v in ("queued", "dispatching", "accepted", "delivered", "failed", "uncertain", "cancelled")], default="queued", required=True, index=True)
    dispatched_at = fields.Datetime()
    provider_ref = fields.Char(index=True)
    result_code = fields.Char()
    _unique_approved_revision = models.Constraint("unique(message_id, approved_revision)", "This reply revision is already queued.")
    _unique_provider_ref = models.Constraint("unique(site_id, provider_ref)", "Provider receipt already belongs to another delivery.")


class HubProviderEvent(models.Model):
    _name = "dojo.hub.provider.event"
    _description = "Authenticated Delivery Status Event"
    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict")
    event_ref = fields.Char(required=True)
    fingerprint = fields.Char(required=True)
    provider_ref = fields.Char(required=True)
    status = fields.Selection([("delivered", "Delivered"), ("failed", "Failed")], required=True)
    delivery_id = fields.Many2one("dojo.hub.delivery", ondelete="restrict")
    _unique_callback = models.Constraint("unique(site_id, event_ref)", "Callback already recorded.")
