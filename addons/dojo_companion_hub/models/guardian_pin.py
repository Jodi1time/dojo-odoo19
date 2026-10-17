import hashlib
import hmac

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from odoo.addons.dojo_kiosk.models.dojo_kiosk_guardian import PIN, weak_pin
from .service import HubProblem, exact, text


class HubGuardianKioskAccess(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "guardian_kiosk_access": {"handler": self._guardian_kiosk_access, "roles": "owner manager", "write": False},
            "set_guardian_kiosk_pin": {"handler": self._set_guardian_kiosk_pin, "roles": "owner manager", "write": True,
                "replay": self._replay_company_member, "audit": self._pin_audit},
            "revoke_guardian_kiosk_pin": {"handler": self._revoke_guardian_kiosk_pin, "roles": "owner manager", "write": True,
                "replay": self._replay_company_member},
        })
        return operations

    def _pin_audit(self, payload):
        secret = self.env["ir.config_parameter"].sudo().get_str("database.secret", "")
        pin = payload.get("pin") if isinstance(payload, dict) else None
        sealed = hmac.new(secret.encode(), str(pin).encode(), hashlib.sha256).hexdigest() if isinstance(pin, str) else None
        return {**payload, "pin": sealed} if isinstance(payload, dict) else payload

    def _household_guardians(self, site, member):
        household = member.partner_id.parent_id
        if not household.is_household or household.company_id not in (self.env["res.company"], site.company_id):
            return self.env["res.partner"]
        return (household.child_ids | household.primary_guardian_id).filtered(lambda p: p.active and p.is_guardian and not p.is_minor
            and not p.is_household and p.company_id in (self.env["res.company"], site.company_id))

    def _guardian_of(self, site, grant, p):
        member = self._member(site, grant, p["memberId"])
        guardian_id = record_id(p["guardianId"])
        guardian = self._household_guardians(site, member).filtered(lambda g: g.id == guardian_id)
        if not guardian:
            raise HubProblem("GUARDIAN_NOT_IN_HOUSEHOLD", 422)
        return member, guardian

    def _guardian_kiosk_access(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        guardians = self._household_guardians(site, member)
        pins = {row.partner_id.id: row for row in self.env["dojo.kiosk.guardian.pin"].sudo().search([
            ("company_id", "=", site.company_id.id), ("partner_id", "in", guardians.ids), ("active", "=", True)])}
        children = self.env["dojo.member"].sudo().search([("partner_id.parent_id", "=", member.partner_id.parent_id.id),
            ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=20) if guardians else self.env["dojo.member"]
        return {"memberId": str(member.id), "version": self._version(member),
            "students": [m.name for m in children if m.partner_id not in guardians],
            "guardians": [{"id": str(g.id), "name": g.name, "hasPhone": bool(g.phone), "hasPin": g.id in pins,
                "setAt": iso(pins[g.id].set_at) if g.id in pins else None, "setBy": pins[g.id].set_by.name if g.id in pins else None,
                "locked": bool(g.id in pins and pins[g.id].locked_until and pins[g.id].locked_until > fields.Datetime.now())}
                for g in guardians.sorted(lambda g: ((g.name or "").casefold(), g.id))]}

    def _set_guardian_kiosk_pin(self, site, grant, p):
        exact(p, "memberId guardianId pin reason")
        reason = text(p["reason"], 500)
        member, guardian = self._guardian_of(site, grant, p)
        if not guardian.phone:
            raise HubProblem("GUARDIAN_PHONE_REQUIRED", 422)
        if not isinstance(p["pin"], str) or not PIN.fullmatch(p["pin"]):
            raise HubProblem("INVALID_COMMAND", 400)
        if weak_pin(p["pin"]):
            raise HubProblem("GUARDIAN_PIN_TOO_SIMPLE", 422)
        self._lock(guardian)
        self.env["dojo.kiosk.guardian.pin"]._set_pin(site.company_id, guardian, p["pin"])
        return {"memberId": str(member.id), "guardianId": str(guardian.id), "state": "guardian_pin_set", "reason": reason,
            "messageSent": False, "_scopeMemberIds": [member.id]}

    def _revoke_guardian_kiosk_pin(self, site, grant, p):
        exact(p, "memberId guardianId reason")
        reason = text(p["reason"], 500)
        member, guardian = self._guardian_of(site, grant, p)
        row = self.env["dojo.kiosk.guardian.pin"].sudo().search([("company_id", "=", site.company_id.id),
            ("partner_id", "=", guardian.id), ("active", "=", True)], limit=1)
        if not row:
            raise HubProblem("GUARDIAN_PIN_NOT_SET", 422)
        self._lock(row)
        row.write({"active": False})
        self.env["dojo.kiosk.guardian.session"].sudo().search([("partner_id", "=", guardian.id)]).unlink()
        return {"memberId": str(member.id), "guardianId": str(guardian.id), "state": "guardian_pin_revoked", "reason": reason,
            "messageSent": False, "_scopeMemberIds": [member.id]}
