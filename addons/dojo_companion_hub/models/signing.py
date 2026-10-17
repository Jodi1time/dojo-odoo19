import base64
import hashlib
import io

from odoo import fields, models
from odoo.tools import html2plaintext
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, text

SIGNATURE_BYTES = 200000


class HubSignature(models.Model):
    _name = "dojo.hub.signature"
    _description = "Companion Witnessed Document Signature"
    _order = "id desc"

    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict", index=True)
    signer_partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict")
    signed_by = fields.Char(required=True)
    relationship = fields.Selection([("self", "Student"), ("guardian", "Household guardian")], required=True)
    document = fields.Char(required=True)
    content_sha256 = fields.Char(required=True)
    signature_sha256 = fields.Char(required=True)
    witnessed_by = fields.Many2one("res.users", required=True, ondelete="restrict")
    signed_at = fields.Datetime(required=True)
    attachment_id = fields.Many2one("ir.attachment", ondelete="set null")


class HubSigning(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "waiver_context": {"handler": self._waiver_context, "roles": "owner manager", "write": False},
            "sign_waiver": {"handler": self._sign_waiver, "roles": "owner manager", "write": True, "replay": self._replay_company_member,
                "audit": self._signature_audit},
        })
        return operations

    def _signature_audit(self, payload):
        if isinstance(payload, dict) and isinstance(payload.get("signature"), str):
            return {**payload, "signature": hashlib.sha256(payload["signature"].encode()).hexdigest()}
        return payload

    def _waiver_text(self):
        config = self.env["dojo.waiver.config"].sudo().get_singleton()
        body = html2plaintext(config.content_html or "").strip()
        return config.name or "Waiver", body, hashlib.sha256(("%s\n%s" % (config.name, body)).encode()).hexdigest()

    def _signers(self, site, member):
        adult = not member.partner_id.is_minor
        guardians = self._household_guardians(site, member) if member.partner_id.parent_id else self.env["res.partner"]
        rows = [{"partnerId": str(g.id), "name": g.name, "relationship": "guardian"} for g in guardians]
        if adult:
            rows.insert(0, {"partnerId": str(member.partner_id.id), "name": member.name, "relationship": "self"})
        return rows

    def _waiver_context(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        title, body, digest = self._waiver_text()
        last = self.env["dojo.hub.signature"].sudo().search([("site_id", "=", site.id), ("member_id", "=", member.id)], limit=1)
        return {"memberId": str(member.id), "title": title, "text": body, "contentVersion": digest, "minor": bool(member.partner_id.is_minor),
            "state": member.waiver_state, "signedBy": member.waiver_signed_by or None, "signedAt": iso(member.waiver_signed_on),
            "pdfStored": bool(member.waiver_attachment_id), "signers": self._signers(site, member),
            "witnessedBy": last.witnessed_by.name if last else None, "signedVersionMatches": bool(last) and last.content_sha256 == digest,
            "version": self._version(member)}

    def _signature_png(self, value):
        if not isinstance(value, str) or not value.startswith("data:image/png;base64,"):
            raise HubProblem("SIGNATURE_INVALID", 422)
        try:
            raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        except ValueError:
            raise HubProblem("SIGNATURE_INVALID", 422)
        if not 200 <= len(raw) <= SIGNATURE_BYTES:
            raise HubProblem("SIGNATURE_INVALID", 422)
        from PIL import Image
        try:
            image = Image.open(io.BytesIO(raw))
            image.verify()
            image = Image.open(io.BytesIO(raw)).convert("LA")
        except Exception:
            raise HubProblem("SIGNATURE_INVALID", 422)
        if not (200 <= image.width <= 2000 and 80 <= image.height <= 1000):
            raise HubProblem("SIGNATURE_INVALID", 422)
        inked = sum(1 for alpha in image.getchannel("A").getdata() if alpha > 0) if image.getchannel("A").getextrema()[0] < 255 else \
            sum(1 for luma in image.getchannel("L").getdata() if luma < 128)
        if inked < 150:
            raise HubProblem("SIGNATURE_EMPTY", 422)
        return raw

    def _sign_waiver(self, site, grant, p):
        exact(p, "memberId expectedVersion signerPartnerId signedByName contentVersion agreed signature")
        member = self._member(site, grant, p["memberId"])
        self._lock(member)
        self._require_version(member, p["expectedVersion"])
        if p["agreed"] is not True:
            raise HubProblem("SIGNATURE_AGREEMENT_REQUIRED", 422)
        title, body, digest = self._waiver_text()
        if p["contentVersion"] != digest:
            raise HubProblem("WAIVER_TEXT_CHANGED")
        signer = next((s for s in self._signers(site, member) if s["partnerId"] == str(record_id(p["signerPartnerId"]))), None)
        if not signer:
            raise HubProblem("SIGNER_NOT_AUTHORIZED", 422)
        name = text(p["signedByName"], 120)
        if member.waiver_state == "signed":
            raise HubProblem("WAIVER_ALREADY_SIGNED")
        raw = self._signature_png(p["signature"])
        now = fields.Datetime.now()
        member.sudo().apply_waiver(signature=base64.b64encode(raw).decode(), signed_by=name, signed_on=now)
        member.invalidate_recordset()
        row = self.env["dojo.hub.signature"].sudo().create({"site_id": site.id, "member_id": member.id,
            "signer_partner_id": int(signer["partnerId"]), "signed_by": name, "relationship": signer["relationship"], "document": title,
            "content_sha256": digest, "signature_sha256": hashlib.sha256(raw).hexdigest(), "witnessed_by": self.env.uid, "signed_at": now,
            "attachment_id": member.waiver_attachment_id.id or False})
        return {"memberId": str(member.id), "signatureId": str(row.id), "state": "waiver_signed", "signedBy": name,
            "relationship": signer["relationship"], "pdfStored": bool(member.waiver_attachment_id), "messageSent": False,
            "version": self._version(member)}
