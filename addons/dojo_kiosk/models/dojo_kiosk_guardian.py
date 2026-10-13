import hashlib
import re
import secrets
from datetime import timedelta

from passlib.context import CryptContext
from odoo import fields, models

from .dojo_kiosk_v2 import PROBLEMS, KioskProblem, problem, record_id

PIN = re.compile(r"^[0-9]{6}$")
PIN_CONTEXT = CryptContext(schemes=["pbkdf2_sha512"], pbkdf2_sha512__rounds=120000)
DUMMY_HASH = PIN_CONTEXT.hash("000000")
MAX_FAILURES = 5
LOCK_MINUTES = 15
DEVICE_FAILURES = 10
DEVICE_WINDOW_MINUTES = 10
SESSION_MINUTES = 3

PROBLEMS.update({
    "GUARDIAN_NOT_VERIFIED": (401, "We could not verify that phone number and PIN. Please see the front desk"),
    "GUARDIAN_LOCKED": (429, "Too many attempts. Please see the front desk"),
    "GUARDIAN_SESSION_EXPIRED": (401, "Please verify again"),
    "NOT_YOUR_STUDENT": (403, "Please see the front desk"),
})


def weak_pin(pin):
    return len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210"


def digits(value):
    return re.sub(r"[^0-9]", "", value or "")


class DojoKioskGuardianPin(models.Model):
    _name = "dojo.kiosk.guardian.pin"
    _description = "Guardian Kiosk PIN"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    pin_hash = fields.Char(required=True, copy=False, groups="base.group_system")
    failed_attempts = fields.Integer(default=0, copy=False)
    locked_until = fields.Datetime(copy=False)
    set_by = fields.Many2one("res.users", ondelete="restrict", copy=False)
    set_at = fields.Datetime(copy=False)
    active = fields.Boolean(default=True)
    _unique_guardian_pin = models.Constraint("unique(company_id, partner_id)", "A guardian has one kiosk PIN per school.")

    def _set_pin(self, company, partner, pin):
        if not isinstance(pin, str) or not PIN.fullmatch(pin) or weak_pin(pin):
            raise KioskProblem("INVALID_COMMAND")
        values = {"pin_hash": PIN_CONTEXT.hash(pin), "failed_attempts": 0, "locked_until": False,
                  "set_by": self.env.uid, "set_at": fields.Datetime.now(), "active": True}
        row = self.sudo().with_context(active_test=False).search([("company_id", "=", company.id), ("partner_id", "=", partner.id)], limit=1)
        if row:
            row.write(values)
        else:
            row = self.sudo().create({**values, "company_id": company.id, "partner_id": partner.id})
        return row


class DojoKioskGuardianAttempt(models.Model):
    _name = "dojo.kiosk.guardian.attempt"
    _description = "Guardian Kiosk Verification Attempt"
    _order = "id desc"

    config_id = fields.Many2one("dojo.kiosk.config", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", ondelete="set null")
    success = fields.Boolean(default=False)


class DojoKioskGuardianSession(models.Model):
    _name = "dojo.kiosk.guardian.session"
    _description = "Short-lived Guardian Kiosk Session"

    config_id = fields.Many2one("dojo.kiosk.config", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    session_id = fields.Many2one("dojo.class.session", required=True, ondelete="cascade")
    token_hash = fields.Char(required=True, index=True, copy=False)
    expires_at = fields.Datetime(required=True)
    member_ids = fields.Many2many("dojo.member", "dojo_kiosk_guardian_session_member_rel", "guardian_session_id", "member_id")


class DojoKioskGuardianService(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _guardian_children(self, config, guardian):
        household = guardian.parent_id
        if not household.is_household:
            return self.env["dojo.member"]
        return self.env["dojo.member"].sudo().search([
            ("partner_id.parent_id", "=", household.id), ("partner_id", "!=", guardian.id),
            ("company_id", "=", config.company_id.id), ("active", "=", True),
            ("id", "in", config.integration_member_ids.ids)], limit=20)

    def _guardian_candidates(self, config, phone):
        self.env.cr.execute("""
            SELECT p.id FROM res_partner p JOIN res_partner h ON h.id = p.parent_id
            WHERE p.active AND p.is_guardian AND NOT coalesce(p.is_minor, false) AND h.is_household
              AND coalesce(p.company_id, h.company_id) = %s
              AND (regexp_replace(coalesce(p.phone, ''), '[^0-9]', '', 'g') = %s)
            LIMIT 5""", [config.company_id.id, phone])
        return self.env["res.partner"].sudo().browse([r[0] for r in self.env.cr.fetchall()])

    def _guardian_verify(self, config, params):
        if not isinstance(params, dict) or set(params) != {"phone", "pin", "sessionId"}:
            raise KioskProblem("INVALID_COMMAND")
        phone, pin = digits(params["phone"] if isinstance(params["phone"], str) else ""), params["pin"]
        if not 7 <= len(phone) <= 15 or not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise KioskProblem("INVALID_COMMAND")
        session = self._v2_session(config, params["sessionId"])
        if session.state != "open" or session.end_datetime <= fields.Datetime.now():
            raise KioskProblem("SESSION_NOT_OPEN")
        self._v2_lock("guardian:%s" % config.id)
        now = fields.Datetime.now()
        Attempt = self.env["dojo.kiosk.guardian.attempt"].sudo()
        if Attempt.search_count([("config_id", "=", config.id), ("success", "=", False),
                                 ("create_date", ">=", now - timedelta(minutes=DEVICE_WINDOW_MINUTES))]) >= DEVICE_FAILURES:
            return problem("GUARDIAN_LOCKED")
        candidates = self._guardian_candidates(config, phone)
        pins = self.env["dojo.kiosk.guardian.pin"].sudo().search([("company_id", "=", config.company_id.id),
            ("partner_id", "in", candidates.ids), ("active", "=", True)])
        if any(p.locked_until and p.locked_until > now for p in pins):
            Attempt.create({"config_id": config.id, "success": False})
            return problem("GUARDIAN_LOCKED")
        matched = self.env["dojo.kiosk.guardian.pin"]
        for row in pins:
            if PIN_CONTEXT.verify(pin, row.pin_hash):
                matched |= row
        if not pins:
            PIN_CONTEXT.verify(pin, DUMMY_HASH)
        if len(matched) != 1:
            for row in pins:
                failures = row.failed_attempts + 1
                row.write({"failed_attempts": failures,
                           "locked_until": now + timedelta(minutes=LOCK_MINUTES) if failures >= MAX_FAILURES else False})
            Attempt.create({"config_id": config.id, "success": False})
            return problem("GUARDIAN_NOT_VERIFIED")
        guardian = matched.partner_id
        matched.write({"failed_attempts": 0, "locked_until": False})
        Attempt.create({"config_id": config.id, "partner_id": guardian.id, "success": True})
        children = self._guardian_children(config, guardian)
        enrollments = self.env["dojo.class.enrollment"].sudo().search([("session_id", "=", session.id),
            ("member_id", "in", children.ids), ("status", "=", "registered")])
        booked = {e.member_id.id: e for e in enrollments}
        token = secrets.token_urlsafe(32)
        self.env["dojo.kiosk.guardian.session"].sudo().search([("config_id", "=", config.id), ("expires_at", "<", now)]).unlink()
        self.env["dojo.kiosk.guardian.session"].sudo().create({"config_id": config.id, "partner_id": guardian.id,
            "session_id": session.id, "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": now + timedelta(minutes=SESSION_MINUTES), "member_ids": [(6, 0, list(booked))]})
        def short(member):
            parts = (member.name or "Student").split()
            return parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")
        return {"guardianToken": token, "expiresInSeconds": SESSION_MINUTES * 60, "guardian": short(guardian),
                "students": [{"memberId": str(m.id), "displayName": short(m), "onRoster": m.id in booked,
                              "attendanceState": booked[m.id].attendance_state if m.id in booked else None}
                             for m in children.sorted(lambda m: ((m.name or "").casefold(), m.id))]}

    def _guardian_check_in(self, config, params):
        if not isinstance(params, dict) or set(params) != {"guardianToken", "command"} or not isinstance(params["guardianToken"], str):
            raise KioskProblem("INVALID_COMMAND")
        payload = self._v2_command(params["command"])
        if self.env["dojo.kiosk.command.receipt"].sudo().search_count([("company_id", "=", config.company_id.id),
                ("config_id", "=", config.id), ("idempotency_key", "=", params["command"]["idempotencyKey"])]):
            return {**self._v2_check_in(config, params["command"]), "guardianVerified": True}
        digest = hashlib.sha256(params["guardianToken"].encode()).hexdigest()
        grant = self.env["dojo.kiosk.guardian.session"].sudo().search([("config_id", "=", config.id),
            ("token_hash", "=", digest)], limit=1)
        if not grant or grant.expires_at <= fields.Datetime.now():
            raise KioskProblem("GUARDIAN_SESSION_EXPIRED")
        if record_id(payload["sessionId"]) != grant.session_id.id or record_id(payload["memberId"]) not in grant.member_ids.ids:
            raise KioskProblem("NOT_YOUR_STUDENT")
        result = self._v2_check_in(config, params["command"])
        return {**result, "guardianVerified": True}
