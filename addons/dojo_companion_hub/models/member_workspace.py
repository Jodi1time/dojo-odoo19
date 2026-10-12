"""Explicit member tabs under individual staff authority, not a device cookie."""
from odoo import models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso


class HubMemberWorkspace(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _member_workspace(self, site, grant, payload):
        details = self._member_details(site, grant, payload)
        member = self._member(site, grant, payload["memberId"])
        manage = grant.role in ("owner", "manager")
        household = member.partner_id.parent_id
        if not household.is_household or household.company_id not in (self.env["res.company"], site.company_id):
            household = self.env["res.partner"]
        family = safety = documents = None
        if manage:
            relatives = self._members(site, grant).filtered(lambda m: household and m.partner_id.parent_id == household)
            bindings = self.env["dojo.hub.guardian"].sudo().search([
                ("site_id", "=", site.id), ("member_ids", "in", [member.id]), ("active", "=", True)])
            family = {"household": household.name or None,
                "members": [{"id": str(m.id), "name": m.name} for m in relatives],
                "guardians": [{"id": str(b.id), "name": b.partner_id.name, "channel": b.channel, "allowReply": b.allow_reply} for b in bindings]}
            contacts = member.emergency_contact_ids.filtered(lambda c: c.company_id == site.company_id)
            safety = {"allergies": member.allergies or None, "medicalNotes": member.medical_notes or None,
                "emergencyNote": member.emergency_note or None,
                "contacts": [{"id": str(c.id), "name": c.name, "relationship": c.relationship, "phone": c.phone,
                    "primary": c.is_primary} for c in contacts]}
            # Attachment metadata only, on this exact native record. No broad
            # partner search, binary payload, public token or download URL.
            attachments = self.env["ir.attachment"].sudo().search([
                ("res_model", "=", "dojo.member"), ("res_id", "=", member.id), ("res_field", "=", False),
                ("company_id", "in", [False, site.company_id.id]), ("type", "=", "binary")], order="id desc", limit=30)
            documents = {"waiver": {"state": member.waiver_state,
                "signedBy": member.waiver_signed_by or None, "signedAt": iso(member.waiver_signed_on) if member.waiver_signed_on else None}
                if "waiver_state" in member._fields else None,
                "files": [{"id": str(a.id), "name": a.name, "mimeType": a.mimetype or "Document", "bytes": a.file_size} for a in attachments]}
        context = self._context(site, grant, {})
        return {**details, "family": family, "safety": safety, "documents": documents,
            "messages": [m for m in context["messages"] if m["memberId"] == str(member.id)],
            "timeline": [a for a in context["timeline"] if a["memberId"] == str(member.id)],
            "recordScope": "Exact selected member; staff account and current site",
            "limits": {**details["limits"], "documents": 30, "messageWindow": 50, "timelineWindow": 50}}
