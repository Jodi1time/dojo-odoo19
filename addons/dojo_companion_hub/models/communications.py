from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, fingerprint, text

CHANNELS = ("sms", "email", "whatsapp")
PURPOSES = ("service", "marketing")
SOURCES = ("in_person", "paper_form", "web_form", "verbal", "reply_stop")
QUIET_START, QUIET_END = 21, 8
AUDIENCE_LIMIT = 500
AUTOMATION_MODULES = ("dojo_crm", "dojo_communications", "dojo_core", "dojo_automation", "dojo_subscriptions", "dojo_kiosk", "dojo_credits", "dojo_companion_hub")


class HubConsent(models.Model):
    _name = "dojo.hub.consent"
    _description = "Companion Contact Consent Record"
    _order = "id desc"

    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    channel = fields.Selection([(c, c) for c in CHANNELS], required=True)
    purpose = fields.Selection([(p, p) for p in PURPOSES], required=True)
    state = fields.Selection([("granted", "Granted"), ("withdrawn", "Withdrawn")], required=True)
    source = fields.Selection([(s, s) for s in SOURCES], required=True)
    evidence = fields.Text(required=True)
    recorded_by = fields.Many2one("res.users", required=True, ondelete="restrict")
    recorded_at = fields.Datetime(required=True, default=fields.Datetime.now)


class HubBroadcast(models.Model):
    _name = "dojo.hub.broadcast"
    _description = "Companion Reviewed Broadcast"
    _order = "id desc"

    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    name = fields.Char(required=True)
    channel = fields.Selection([(c, c) for c in CHANNELS], required=True)
    purpose = fields.Selection([(p, p) for p in PURPOSES], required=True)
    body = fields.Text(required=True)
    audience = fields.Char(required=True)
    audience_label = fields.Char(required=True)
    state = fields.Selection([("awaiting_approval", "Awaiting approval"), ("approved", "Approved"), ("cancelled", "Cancelled")],
        required=True, default="awaiting_approval")
    created_by = fields.Many2one("res.users", required=True, ondelete="restrict")
    approved_by = fields.Many2one("res.users", ondelete="restrict")
    approved_at = fields.Datetime()
    scheduled_at = fields.Datetime()
    note = fields.Text()
    recipient_ids = fields.One2many("dojo.hub.broadcast.recipient", "broadcast_id")


class HubBroadcastRecipient(models.Model):
    _name = "dojo.hub.broadcast.recipient"
    _description = "Companion Broadcast Recipient"

    broadcast_id = fields.Many2one("dojo.hub.broadcast", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict")
    member_ids = fields.Many2many("dojo.member", "dojo_hub_broadcast_recipient_member_rel", "recipient_id", "member_id")
    address = fields.Char(required=True)
    state = fields.Selection([("queued", "Queued"), ("excluded", "Excluded"), ("accepted", "Accepted by provider"),
        ("delivered", "Delivered"), ("failed", "Failed")], required=True, default="queued")
    reason = fields.Char()
    provider_ref = fields.Char()


def mask(address):
    if "@" in address:
        name, domain = address.split("@", 1)
        return name[:1] + "•••@" + domain
    return "•••" + address[-4:]


class HubCommunications(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "consent_overview": {"handler": self._consent_overview, "roles": "owner manager", "write": False},
            "record_consent": {"handler": self._record_consent, "roles": "owner manager", "write": True, "replay": self._replay_company_member},
            "broadcast_context": {"handler": self._broadcast_context, "roles": "owner manager", "write": False},
            "broadcast_preview": {"handler": self._broadcast_preview, "roles": "owner manager", "write": False},
            "create_broadcast": {"handler": self._create_broadcast, "roles": "owner manager", "write": True, "replay": self._replay_broadcast},
            "approve_broadcast": {"handler": self._approve_broadcast, "roles": "owner manager", "write": True, "replay": self._replay_broadcast},
            "cancel_broadcast": {"handler": self._cancel_broadcast, "roles": "owner manager", "write": True, "replay": self._replay_broadcast},
            "automation_overview": {"handler": self._automation_overview, "roles": "owner manager", "write": False},
            "set_automation_active": {"handler": self._set_automation_active, "roles": "owner", "write": True},
        })
        return operations

    def _replay_broadcast(self, site, grant, receipt, saved):
        self._broadcast(site, saved["broadcastId"])

    def _broadcast(self, site, value):
        row = self.env["dojo.hub.broadcast"].sudo().search([("id", "=", record_id(value)), ("site_id", "=", site.id)], limit=1)
        if not row:
            raise HubProblem("BROADCAST_UNAVAILABLE", 404)
        return row

    def _consent_state(self, site, partner, channel, purpose):
        row = self.env["dojo.hub.consent"].sudo().search([("site_id", "=", site.id), ("partner_id", "=", partner.id),
            ("channel", "=", channel), ("purpose", "=", purpose)], limit=1)
        return row.state if row else None

    def _address(self, partner, channel):
        return (partner.email or "").strip() if channel == "email" else (partner.phone or "").strip()

    def _native_opt_out(self, partner, channel):
        if channel == "email":
            return bool(partner.email) and bool(self.env["mail.blacklist"].sudo().search_count([("email", "=", partner.email_normalized), ("active", "=", True)]))
        number = partner.phone_sanitized if "phone_sanitized" in partner._fields else False
        return bool(number) and bool(self.env["phone.blacklist"].sudo().search_count([("number", "=", number), ("active", "=", True)]))

    def _contacts(self, site, member):
        household = member.partner_id.parent_id
        guardians = self._household_guardians(site, member) if household else self.env["res.partner"]
        return member.partner_id | guardians

    def _consent_overview(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        rows = []
        for partner in self._contacts(site, member):
            channels = []
            for channel in CHANNELS:
                address = self._address(partner, channel)
                channels.append({"channel": channel, "address": mask(address) if address else None, "nativeOptOut": self._native_opt_out(partner, channel),
                    "service": self._consent_state(site, partner, channel, "service"), "marketing": self._consent_state(site, partner, channel, "marketing")})
            history = self.env["dojo.hub.consent"].sudo().search([("site_id", "=", site.id), ("partner_id", "=", partner.id)], limit=10)
            rows.append({"partnerId": str(partner.id), "name": partner.name, "student": partner == member.partner_id, "channels": channels,
                "history": [{"channel": h.channel, "purpose": h.purpose, "state": h.state, "source": h.source, "evidence": h.evidence,
                    "by": h.recorded_by.name, "at": iso(h.recorded_at)} for h in history]})
        return {"memberId": str(member.id), "contacts": rows, "version": self._version(member)}

    def _record_consent(self, site, grant, p):
        exact(p, "memberId partnerId channel purpose state source evidence")
        member = self._member(site, grant, p["memberId"])
        partner = self._contacts(site, member).filtered(lambda c: c.id == record_id(p["partnerId"]))
        if not partner:
            raise HubProblem("CONTACT_UNAVAILABLE", 404)
        if p["channel"] not in CHANNELS or p["purpose"] not in PURPOSES or p["state"] not in ("granted", "withdrawn") or p["source"] not in SOURCES:
            raise HubProblem("INVALID_COMMAND", 400)
        evidence = text(p["evidence"], 500)
        address = self._address(partner, p["channel"])
        if not address:
            raise HubProblem("CONTACT_ADDRESS_REQUIRED", 422)
        if self._consent_state(site, partner, p["channel"], p["purpose"]) == p["state"]:
            raise HubProblem("CONSENT_UNCHANGED")
        self._lock(partner)
        self.env["dojo.hub.consent"].sudo().create({"site_id": site.id, "partner_id": partner.id, "channel": p["channel"],
            "purpose": p["purpose"], "state": p["state"], "source": p["source"], "evidence": evidence, "recorded_by": self.env.uid})
        native = None
        note = "Companion consent %s by %s: %s" % (p["state"], self.env.user.name, evidence)
        if p["purpose"] == "marketing":
            if p["channel"] == "email":
                Blacklist = self.env["mail.blacklist"].sudo().with_context(mail_create_nosubscribe=True, tracking_disable=True)
                (Blacklist._add if p["state"] == "withdrawn" else Blacklist._remove)(address, message=note)
                native = "email_opt_out" if p["state"] == "withdrawn" else "email_opt_in"
            elif partner.phone_sanitized:
                Blacklist = self.env["phone.blacklist"].sudo().with_context(mail_create_nosubscribe=True, tracking_disable=True)
                (Blacklist._add if p["state"] == "withdrawn" else Blacklist._remove)([partner.phone_sanitized], message=note)
                native = "phone_opt_out" if p["state"] == "withdrawn" else "phone_opt_in"
        return {"memberId": str(member.id), "partnerId": str(partner.id), "state": "consent_recorded", "consent": p["state"],
            "channel": p["channel"], "purpose": p["purpose"], "nativeOptOutUpdated": native, "messageSent": False,
            "_scopeMemberIds": [member.id]}

    def _audiences(self, site):
        templates = self.env["dojo.class.template"].sudo().search([("company_id", "=", site.company_id.id), ("active", "=", True)],
            order="name", limit=100)
        return ([{"id": "state:%s" % s, "label": "%s students" % s.title()} for s in ("active", "trial", "paused")]
            + [{"id": "template:%s" % t.id, "label": "Class: %s" % t.name} for t in templates])

    def _audience_members(self, site, audience):
        Member = self.env["dojo.member"].sudo()
        base = [("company_id", "=", site.company_id.id), ("active", "=", True)]
        if not isinstance(audience, str) or ":" not in audience:
            raise HubProblem("INVALID_COMMAND", 400)
        kind, value = audience.split(":", 1)
        if kind == "state" and value in ("active", "trial", "paused"):
            return Member.search(base + [("membership_state", "=", value)], order="id", limit=AUDIENCE_LIMIT + 1), "%s students" % value.title()
        if kind == "template":
            template = self.env["dojo.class.template"].sudo().search([("id", "=", record_id(value)),
                ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
            if not template:
                raise HubProblem("INVALID_COMMAND", 400)
            now = fields.Datetime.now()
            booked = self.env["dojo.class.enrollment"].sudo().search([("session_id.template_id", "=", template.id),
                ("session_id.start_datetime", ">=", now), ("session_id.start_datetime", "<=", now + timedelta(days=14)),
                ("status", "=", "registered")]).member_id
            members = (template.course_member_ids | booked).filtered(lambda m: m.active and m.company_id == site.company_id)
            return members.sorted("id")[:AUDIENCE_LIMIT + 1], "Class: %s" % template.name
        raise HubProblem("INVALID_COMMAND", 400)

    def _recipient_plan(self, site, channel, purpose, members):
        recipients, excluded = {}, []
        for member in members:
            household = member.partner_id.parent_id
            contact = household.primary_guardian_id if household.is_household and household.primary_guardian_id and (
                member.partner_id.is_minor or not self._address(member.partner_id, channel)) else member.partner_id
            address = self._address(contact, channel)
            reason = None
            if not address:
                reason = "no_contact"
            else:
                reason = self._exclusion(site, contact, channel, purpose)
            if reason:
                excluded.append({"memberId": member.id, "partnerId": contact.id, "name": contact.name, "reason": reason, "address": address})
                continue
            row = recipients.setdefault(contact.id, {"partnerId": contact.id, "name": contact.name, "address": address, "memberIds": []})
            row["memberIds"].append(member.id)
        return list(recipients.values()), excluded

    def _exclusion(self, site, partner, channel, purpose):
        if purpose == "service":
            return "opted_out" if self._consent_state(site, partner, channel, "service") == "withdrawn" else None
        marketing = self._consent_state(site, partner, channel, "marketing")
        if marketing == "withdrawn" or self._native_opt_out(partner, channel):
            return "opted_out"
        return None if marketing == "granted" else "no_marketing_consent"

    def _quiet_schedule(self, site):
        tz = ZoneInfo(site.company_id.partner_id.tz or "UTC")
        local = fields.Datetime.now().replace(tzinfo=timezone.utc).astimezone(tz)
        if QUIET_END <= local.hour < QUIET_START:
            return None
        day = local.date() if local.hour < QUIET_END else local.date() + timedelta(days=1)
        start = datetime.combine(day, time(QUIET_END), tzinfo=tz)
        return start.astimezone(timezone.utc).replace(tzinfo=None)

    def _broadcast_plan(self, site, p):
        if p["channel"] not in CHANNELS or p["purpose"] not in PURPOSES:
            raise HubProblem("INVALID_COMMAND", 400)
        body = text(p["body"], 1000 if p["channel"] != "email" else 5000)
        members, label = self._audience_members(site, p["audience"])
        if len(members) > AUDIENCE_LIMIT:
            raise HubProblem("AUDIENCE_TOO_LARGE", 422)
        recipients, excluded = self._recipient_plan(site, p["channel"], p["purpose"], members)
        counts = {}
        for row in excluded:
            counts[row["reason"]] = counts.get(row["reason"], 0) + 1
        token = fingerprint({"site": site.id, "user": self.env.uid, "channel": p["channel"], "purpose": p["purpose"], "body": body,
            "audience": p["audience"], "recipients": sorted((r["partnerId"], r["address"], tuple(sorted(r["memberIds"]))) for r in recipients),
            "excluded": sorted((e["memberId"], e["reason"]) for e in excluded)})
        return {"body": body, "label": label, "recipients": recipients, "excluded": excluded, "excludedCounts": counts, "token": token}

    def _provider_state(self, site):
        return "configured_not_exercised" if site.outbound_enabled and site.provider_origin and site._secret("provider_token_env") else "not_configured"

    def _broadcast_row(self, row):
        states = {}
        for r in row.recipient_ids:
            states[r.state] = states.get(r.state, 0) + 1
        return {"id": str(row.id), "name": row.name, "channel": row.channel, "purpose": row.purpose, "audience": row.audience_label,
            "state": row.state, "createdBy": row.created_by.name, "approvedBy": row.approved_by.name or None,
            "approvedAt": iso(row.approved_at), "scheduledAt": iso(row.scheduled_at), "counts": states, "version": self._version(row),
            "body": row.body}

    def _broadcast_context(self, site, grant, p):
        exact(p, "")
        rows = self.env["dojo.hub.broadcast"].sudo().search([("site_id", "=", site.id)], limit=20)
        return {"audiences": self._audiences(site), "provider": self._provider_state(site), "quietHours": {"start": QUIET_START, "end": QUIET_END,
            "timeZone": site.company_id.partner_id.tz or "UTC"}, "broadcasts": [self._broadcast_row(r) for r in rows]}

    def _broadcast_preview(self, site, grant, p):
        exact(p, "channel purpose audience body")
        plan = self._broadcast_plan(site, p)
        return {"audience": plan["label"], "recipientCount": len(plan["recipients"]), "excludedCounts": plan["excludedCounts"],
            "sample": [{"name": r["name"], "address": mask(r["address"]), "students": len(r["memberIds"])} for r in plan["recipients"][:20]],
            "excludedSample": [{"name": e["name"], "reason": e["reason"]} for e in plan["excluded"][:20]],
            "quietHoursStart": iso(self._quiet_schedule(site)), "provider": self._provider_state(site), "reviewToken": plan["token"]}

    def _create_broadcast(self, site, grant, p):
        exact(p, "channel purpose audience body name reason reviewToken")
        reason, name = text(p["reason"], 500), text(p["name"], 120)
        plan = self._broadcast_plan(site, p)
        if p["reviewToken"] != plan["token"]:
            raise HubProblem("BROADCAST_REVIEW_CHANGED")
        if not plan["recipients"]:
            raise HubProblem("BROADCAST_NO_RECIPIENTS", 422)
        row = self.env["dojo.hub.broadcast"].sudo().create({"site_id": site.id, "name": name, "channel": p["channel"], "purpose": p["purpose"],
            "body": plan["body"], "audience": p["audience"], "audience_label": plan["label"], "created_by": self.env.uid,
            "note": "%s %s: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason),
            "recipient_ids": [(0, 0, {"partner_id": r["partnerId"], "address": r["address"], "member_ids": [(6, 0, r["memberIds"])]}) for r in plan["recipients"]]
                + [(0, 0, {"partner_id": e["partnerId"], "address": e["address"] or "-", "state": "excluded", "reason": e["reason"],
                    "member_ids": [(6, 0, [e["memberId"]])]}) for e in plan["excluded"]]})
        return {"broadcastId": str(row.id), "state": "broadcast_awaiting_approval", "recipients": len(plan["recipients"]),
            "excluded": len(plan["excluded"]), "messageSent": False, "version": self._version(row)}

    def _approve_broadcast(self, site, grant, p):
        exact(p, "broadcastId expectedVersion reason")
        reason = text(p["reason"], 500)
        row = self._broadcast(site, p["broadcastId"])
        self._lock(row)
        self._require_version(row, p["expectedVersion"])
        if row.state != "awaiting_approval":
            raise HubProblem("BROADCAST_NOT_PENDING")
        withdrawn = 0
        for recipient in row.recipient_ids.filtered(lambda r: r.state == "queued"):
            partner = recipient.partner_id
            if self._address(partner, row.channel) != recipient.address or self._exclusion(site, partner, row.channel, row.purpose):
                recipient.write({"state": "excluded", "reason": "consent_or_contact_changed"})
                withdrawn += 1
        queued = len(row.recipient_ids.filtered(lambda r: r.state == "queued"))
        if not queued:
            raise HubProblem("BROADCAST_NO_RECIPIENTS", 422)
        scheduled = self._quiet_schedule(site)
        row.write({"state": "approved", "approved_by": self.env.uid, "approved_at": fields.Datetime.now(), "scheduled_at": scheduled,
            "note": "\n".join(filter(None, [row.note, "%s %s approved: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)]))})
        return {"broadcastId": str(row.id), "state": "broadcast_approved", "queued": queued, "excludedAtApproval": withdrawn,
            "scheduledAt": iso(scheduled), "provider": self._provider_state(site), "messageSent": False, "version": self._version(row)}

    def _cancel_broadcast(self, site, grant, p):
        exact(p, "broadcastId expectedVersion reason")
        reason = text(p["reason"], 500)
        row = self._broadcast(site, p["broadcastId"])
        self._lock(row)
        self._require_version(row, p["expectedVersion"])
        if row.state == "cancelled" or row.recipient_ids.filtered(lambda r: r.state in ("accepted", "delivered")):
            raise HubProblem("BROADCAST_NOT_CANCELLABLE")
        row.recipient_ids.filtered(lambda r: r.state == "queued").write({"state": "excluded", "reason": "cancelled"})
        row.write({"state": "cancelled", "note": "\n".join(filter(None, [row.note, "%s %s cancelled: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)]))})
        return {"broadcastId": str(row.id), "state": "broadcast_cancelled", "messageSent": False, "version": self._version(row)}

    def _automation_records(self):
        Data = self.env["ir.model.data"].sudo()
        refs = Data.search([("module", "in", list(AUTOMATION_MODULES)), ("model", "in", ["base.automation", "ir.cron"])])
        rows = []
        for ref in refs:
            record = self.env[ref.model].sudo().with_context(active_test=False).browse(ref.res_id).exists()
            if record:
                rows.append((ref, record))
        return rows

    def _automation_overview(self, site, grant, p):
        exact(p, "")
        out = []
        for ref, record in self._automation_records():
            if ref.model == "base.automation":
                sends = any(a.state in ("mail_post", "sms", "followers", "next_activity", "webhook") for a in record.action_server_ids)
                out.append({"id": "base.automation:%s" % record.id, "kind": "Rule", "name": record.name, "module": ref.module,
                    "active": record.active, "trigger": record.trigger, "model": record.model_id.model, "sendsMessages": sends,
                    "version": self._version(record)})
            else:
                out.append({"id": "ir.cron:%s" % record.id, "kind": "Scheduled job", "name": record.display_name, "module": ref.module,
                    "active": record.active, "trigger": "every %s %s" % (record.interval_number, record.interval_type),
                    "model": record.model_id.model, "nextRun": iso(record.nextcall), "sendsMessages": None, "version": self._version(record)})
        out.sort(key=lambda r: (not r["active"], r["module"], r["name"] or ""))
        return {"automations": out, "canManage": grant.role == "owner"}

    def _set_automation_active(self, site, grant, p):
        exact(p, "automationId active expectedVersion reason")
        reason = text(p["reason"], 500)
        if not isinstance(p["active"], bool) or not isinstance(p["automationId"], str) or ":" not in p["automationId"]:
            raise HubProblem("INVALID_COMMAND", 400)
        model, raw = p["automationId"].split(":", 1)
        match = [record for ref, record in self._automation_records() if ref.model == model and record.id == record_id(raw)]
        if not match:
            raise HubProblem("AUTOMATION_UNAVAILABLE", 404)
        record = match[0]
        self._lock(record)
        self._require_version(record, p["expectedVersion"])
        if record.active == p["active"]:
            raise HubProblem("AUTOMATION_UNCHANGED")
        record.write({"active": p["active"]})
        return {"automationId": p["automationId"], "state": "automation_resumed" if p["active"] else "automation_paused", "reason": reason,
            "name": record.display_name, "version": self._version(record)}
