"""Bounded test-system gateway for the existing Companion contract.

A server credential authenticates a paired demonstration device. Explicit
member/session allowlists constrain it to authorized test records. This does
not claim to be a production guardian/credential identity provider.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone

from psycopg2 import errors as pg_errors
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$")
ID = re.compile(r"^[1-9][0-9]{0,9}$")
PROBLEMS = {
    "INVALID_COMMAND": (400, "Invalid command"),
    "FORBIDDEN": (403, "This device is not authorized"),
    "SESSION_UNAVAILABLE": (404, "Session unavailable"),
    "SESSION_NOT_OPEN": (409, "Session not open for check-in"),
    "MEMBER_UNAVAILABLE": (404, "Member unavailable"),
    "NOT_ON_ROSTER": (422, "Please see the front desk"),
    "ELIGIBILITY_REVIEW_REQUIRED": (422, "Please see the front desk"),
    "ATTENDANCE_REVIEW_REQUIRED": (409, "Please see the front desk"),
    "VERSION_CONFLICT": (409, "The session changed. Please select it again"),
    "IDEMPOTENCY_CONFLICT": (409, "The request does not match its original attempt"),
    "CONCURRENT_RETRY": (409, "Please retry the same check-in"),
    "CAPABILITY_DISABLED": (503, "The kiosk connection is unavailable"),
}


class KioskProblem(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def problem(code, correlation="kiosk-invalid-command"):
    status, title = PROBLEMS[code]
    return {"problem": {"type": "urn:dojang:problem:" + code, "code": code,
                        "status": status, "title": title, "detail": title,
                        "correlationId": correlation}}


def record_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or int(value) > 2147483647:
        raise KioskProblem("INVALID_COMMAND")
    return int(value)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def session_version(session):
    value = session.write_date or session.create_date
    return int((value - datetime(1970, 1, 1)).total_seconds() * 1000000) if value else 0


class DojoKioskIntegrationConfig(models.Model):
    _inherit = "dojo.kiosk.config"

    integration_tenant_ref = fields.Char(copy=False, groups="base.group_system")
    integration_enabled = fields.Boolean(default=False, groups="base.group_system")
    integration_key_hash = fields.Char(copy=False, groups="base.group_system")
    integration_staff_key_hash = fields.Char(copy=False, groups="base.group_system")
    integration_member_ids = fields.Many2many(
        "dojo.member", "dojo_kiosk_integration_member_rel", "config_id", "member_id",
        groups="base.group_system", help="Explicit authorized TEST records. Empty means none.")
    integration_session_ids = fields.Many2many(
        "dojo.class.session", "dojo_kiosk_integration_session_rel", "config_id", "session_id",
        groups="base.group_system", help="Explicit authorized TEST sessions. Empty means none.")


class DojoKioskCommandReceipt(models.Model):
    _name = "dojo.kiosk.command.receipt"
    _description = "Dojang Kiosk Command Receipt"
    _order = "create_date desc"

    idempotency_key = fields.Char(required=True, index=True, copy=False)
    fingerprint = fields.Char(required=True, copy=False)
    correlation_id = fields.Char(required=True, index=True, copy=False)
    config_id = fields.Many2one("dojo.kiosk.config", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict", index=True)
    session_id = fields.Many2one("dojo.class.session", required=True, ondelete="restrict", index=True)
    attendance_id = fields.Many2one("dojo.attendance.log", ondelete="restrict", index=True)
    response_json = fields.Text(required=True, copy=False)
    _unique_kiosk_command_key = models.Constraint(
        "unique(company_id, config_id, idempotency_key)", "This kiosk command key has already been used.")


class DojoKioskOutbox(models.Model):
    _name = "dojo.kiosk.outbox"
    _description = "Pending Kiosk Attendance Event"
    attendance_id = fields.Many2one("dojo.attendance.log", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    correlation_id = fields.Char(required=True)
    event_type = fields.Char(default="attendance.checked_in", required=True)
    payload_json = fields.Text(required=True)
    state = fields.Selection([("pending", "Pending")], default="pending", required=True)
    _unique_attendance_event = models.Constraint("unique(attendance_id)", "Attendance event already recorded.")


class DojoKioskV2Service(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _v2_authorize(self, token, gateway_key, staff=False):
        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            raise KioskProblem("FORBIDDEN")
        if not isinstance(gateway_key, str) or not 32 <= len(gateway_key) <= 256:
            raise KioskProblem("FORBIDDEN")
        config = self.env["dojo.kiosk.config"].sudo().search([
            ("kiosk_token", "=", token), ("active", "=", True)], limit=1)
        stored = (config.integration_staff_key_hash if staff else config.integration_key_hash) if config else ""
        digest = hashlib.sha256(gateway_key.encode()).hexdigest()
        if not stored or not hmac.compare_digest(stored, digest):
            raise KioskProblem("FORBIDDEN")
        if not config.integration_enabled or not config.company_id or not config.integration_tenant_ref:
            raise KioskProblem("CAPABILITY_DISABLED")
        return config

    def _v2_session(self, config, value):
        sid = record_id(value)
        session = self.env["dojo.class.session"].sudo().search([
            ("id", "=", sid), ("id", "in", config.integration_session_ids.ids),
            ("company_id", "=", config.company_id.id)], limit=1)
        if not session or not session.template_id.active or session.template_id.company_id != config.company_id:
            raise KioskProblem("SESSION_UNAVAILABLE")
        return session

    def _v2_member(self, config, value):
        mid = record_id(value)
        member = self.env["dojo.member"].sudo().search([
            ("id", "=", mid), ("id", "in", config.integration_member_ids.ids),
            ("company_id", "=", config.company_id.id), ("active", "=", True)], limit=1)
        if not member:
            raise KioskProblem("MEMBER_UNAVAILABLE")
        return member

    def _v2_option(self, session):
        return {"sessionId": str(session.id), "title": session.template_id.name or session.name,
                "startsAt": iso(session.start_datetime), "endsAt": iso(session.end_datetime),
                "version": session_version(session), "capacity": session.capacity, "seatsTaken": session.seats_taken}

    def _v2_sessions(self, config):
        rows = self.env["dojo.class.session"].sudo().search([
            ("id", "in", config.integration_session_ids.ids),
            ("company_id", "=", config.company_id.id), ("state", "=", "open"),
            ("end_datetime", ">", fields.Datetime.now()),
        ], order="start_datetime asc", limit=100)
        return {"sessions": [self._v2_option(s) for s in rows
                             if s.template_id.active and s.template_id.company_id == config.company_id]}

    def _v2_roster(self, config, session_id):
        session = self._v2_session(config, session_id)
        if session.state != "open":
            raise KioskProblem("SESSION_NOT_OPEN")
        rows = self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id), ("status", "=", "registered"),
            ("member_id", "in", config.integration_member_ids.ids),
            ("member_id.company_id", "=", config.company_id.id), ("member_id.active", "=", True)], limit=500)
        def display_name(member):
            parts = (member.name or "Student").split()
            return parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")
        return {"roster": [{"memberId": str(e.member_id.id), "displayName": display_name(e.member_id),
                            "enrollmentStatus": "registered", "attendanceState": e.attendance_state if e.attendance_state in ("pending", "present", "absent", "excused") else "excused"}
                           for e in rows]}

    def _v2_eligible(self, config, session, member, enrollment):
        if member.membership_state != "active":
            raise KioskProblem("ELIGIBILITY_REVIEW_REQUIRED")
        template = session.template_id
        if template.course_member_ids and member not in template.course_member_ids:
            raise KioskProblem("ELIGIBILITY_REVIEW_REQUIRED")
        Sub = self.env["sale.subscription"].sudo()
        candidates = Sub.search([("member_id", "=", member.id), ("state", "=", "active"),
                                 ("active", "=", True), ("company_id", "=", config.company_id.id)])
        hold = False
        if "dojo.credit.transaction" in self.env.registry.models:
            holds = self.env["dojo.credit.transaction"].sudo().search([
                ("enrollment_id", "=", enrollment.id), ("transaction_type", "=", "hold"),
                ("status", "=", "pending")], limit=2)
            if len(holds) > 1:
                raise KioskProblem("ELIGIBILITY_REVIEW_REQUIRED")
            hold = holds[:1]
            if hold:
                candidates = candidates.filtered(lambda sub: sub == hold.subscription_id)
        for sub in candidates:
            plan = sub.plan_id
            if not plan or not plan.active or plan.company_id != config.company_id or sub.paused:
                continue
            covers = ((plan.plan_type == "program" and template.program_id in plan.program_ids)
                      or (plan.plan_type == "course" and (not plan.allowed_template_ids or template in plan.allowed_template_ids)))
            if not covers:
                continue
            if getattr(plan, "credits_per_period", 0) > 0:
                cost = getattr(template.program_id, "credits_per_class", 1) if template.program_id else 1
                if cost > 0 and (not hold or hold.amount > -cost):
                    continue
            sub.flush_recordset()
            self.env.cr.execute("SELECT id FROM sale_subscription WHERE id = %s FOR UPDATE NOWAIT", [sub.id])
            sub.invalidate_recordset()
            if sub.member_id != member or sub.company_id != config.company_id or not sub.active or sub.state != "active" or sub.paused:
                raise KioskProblem("ELIGIBILITY_REVIEW_REQUIRED")
            return
        raise KioskProblem("ELIGIBILITY_REVIEW_REQUIRED")

    def _v2_command(self, command):
        if not isinstance(command, dict) or not {"idempotencyKey", "correlationId", "payload"} <= command.keys():
            raise KioskProblem("INVALID_COMMAND")
        if command.keys() - {"idempotencyKey", "correlationId", "expectedVersion", "payload"}:
            raise KioskProblem("INVALID_COMMAND")
        for field in ("idempotencyKey", "correlationId"):
            if not isinstance(command[field], str) or not KEY.fullmatch(command[field]):
                raise KioskProblem("INVALID_COMMAND")
        payload = command["payload"]
        if not isinstance(payload, dict) or set(payload) != {"memberId", "sessionId"}:
            raise KioskProblem("INVALID_COMMAND")
        record_id(payload["memberId"])
        record_id(payload["sessionId"])
        version = command.get("expectedVersion")
        if "expectedVersion" in command and (type(version) is not int or not 0 <= version <= 9007199254740991):
            raise KioskProblem("INVALID_COMMAND")
        return payload

    def _v2_lock(self, label):
        lock_id = int(hashlib.sha256(label.encode()).hexdigest()[:15], 16)
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
        if not self.env.cr.fetchone()[0]:
            raise KioskProblem("CONCURRENT_RETRY")

    def _v2_check_in(self, config, command):
        payload = self._v2_command(command)
        session = self._v2_session(config, payload["sessionId"])
        member = self._v2_member(config, payload["memberId"])
        key, correlation = command["idempotencyKey"], command["correlationId"]
        fingerprint = hashlib.sha256(json.dumps({"payload": payload, "version": command.get("expectedVersion")}, sort_keys=True).encode()).hexdigest()
        self._v2_lock("key:%s:%s:%s" % (config.company_id.id, config.id, key))
        self._v2_lock("attendance:%s:%s" % (session.id, member.id))
        Receipt = self.env["dojo.kiosk.command.receipt"].sudo()
        saved = Receipt.search([("company_id", "=", config.company_id.id),
                                ("config_id", "=", config.id), ("idempotency_key", "=", key)], limit=1)
        if saved:
            if saved.fingerprint != fingerprint:
                raise KioskProblem("IDEMPOTENCY_CONFLICT")
            result = json.loads(saved.response_json)
            result.update(replayed=True, correlationId=correlation)
            return result
        session.flush_recordset()
        self.env.cr.execute("SELECT id FROM dojo_class_session WHERE id = %s FOR UPDATE NOWAIT", [session.id])
        session.invalidate_recordset()
        if session.company_id != config.company_id or session.state != "open" or session.end_datetime <= fields.Datetime.now():
            raise KioskProblem("SESSION_NOT_OPEN")
        if command.get("expectedVersion") is not None and command["expectedVersion"] != session_version(session):
            raise KioskProblem("VERSION_CONFLICT")
        member.flush_recordset()
        self.env.cr.execute("SELECT id FROM dojo_member WHERE id = %s FOR UPDATE NOWAIT", [member.id])
        member.invalidate_recordset()
        if not member.active or member.company_id != config.company_id:
            raise KioskProblem("MEMBER_UNAVAILABLE")
        session.template_id.flush_recordset()
        self.env.cr.execute("SELECT id FROM dojo_class_template WHERE id = %s FOR UPDATE NOWAIT", [session.template_id.id])
        session.template_id.invalidate_recordset()
        if not session.template_id.active or session.template_id.company_id != config.company_id:
            raise KioskProblem("SESSION_UNAVAILABLE")
        enrollment = self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "=", member.id), ("status", "=", "registered")], limit=1)
        if not enrollment:
            raise KioskProblem("NOT_ON_ROSTER")
        enrollment.flush_recordset()
        self.env.cr.execute("SELECT id FROM dojo_class_enrollment WHERE id = %s FOR UPDATE NOWAIT", [enrollment.id])
        enrollment.invalidate_recordset()
        if enrollment.status != "registered":
            raise KioskProblem("NOT_ON_ROSTER")
        Attendance = self.env["dojo.attendance.log"].sudo()
        attendance = Attendance.search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        existing = bool(attendance)
        if attendance and attendance.status not in ("present", "late"):
            raise KioskProblem("ATTENDANCE_REVIEW_REQUIRED")
        if not attendance:
            if enrollment.attendance_state != "pending":
                raise KioskProblem("ATTENDANCE_REVIEW_REQUIRED")
            self._v2_eligible(config, session, member, enrollment)
            now = fields.Datetime.now()
            status = "late" if now > session.start_datetime else "present"
            attendance = Attendance.create({"session_id": session.id, "member_id": member.id,
                                             "enrollment_id": enrollment.id, "status": status,
                                             "checkin_datetime": now})
            enrollment.write({"attendance_state": "present"})
        receipt = {"attendanceId": str(attendance.id), "memberId": str(member.id),
                   "sessionId": str(session.id), "checkedInAt": iso(attendance.checkin_datetime),
                   "status": attendance.status, "alreadyRecorded": existing, "correlationId": correlation}
        result = {"receipt": receipt, "replayed": False, "correlationId": correlation}
        Receipt.create({"company_id": config.company_id.id, "config_id": config.id,
                        "idempotency_key": key, "fingerprint": fingerprint, "correlation_id": correlation,
                        "member_id": member.id, "session_id": session.id, "attendance_id": attendance.id,
                        "response_json": json.dumps(result, sort_keys=True)})
        if not existing:
            self.env["dojo.kiosk.outbox"].sudo().create({"attendance_id": attendance.id,
                "company_id": config.company_id.id, "correlation_id": correlation,
                "payload_json": json.dumps(receipt, sort_keys=True)})
        return result

    def _v2_member_read(self, config, member_id):
        member = self._v2_member(config, member_id)
        logs = self.env["dojo.attendance.log"].sudo().search([
            ("member_id", "=", member.id), ("company_id", "=", config.company_id.id),
            ("status", "in", ["present", "late"])], order="checkin_datetime desc", limit=1)
        count = self.env["dojo.attendance.log"].sudo().search_count([
            ("member_id", "=", member.id), ("company_id", "=", config.company_id.id),
            ("status", "in", ["present", "late"]),
            ("checkin_datetime", ">=", fields.Datetime.now() - timedelta(days=7))])
        latest = ({"sessionTitle": logs.session_id.template_id.name or logs.session_id.name,
                   "checkedInAt": iso(logs.checkin_datetime), "late": logs.status == "late"} if logs else None)
        return {"member": {"tenantId": config.integration_tenant_ref, "id": str(member.id),
                  "memberNumber": member.member_number or "", "name": member.name or "Student",
                  "membershipState": member.membership_state,
                  "rank": {"name": member.current_rank_id.name or "Unassigned",
                           "stripes": member.current_stripe_count or 0},
                  "attendanceRate": max(0, min(1, (member.attendance_rate or 0) / 100))},
                "attendance": {"latest": latest, "lastSevenDays": count}}

    def _v2_dispatch(self, action, token, gateway_key, params=None):
        """Only called by fixed controller handlers, never generic model RPC."""
        params = params or {}
        correlation = "kiosk-invalid-command"
        if isinstance(params, dict):
            candidate = (params.get("command") or {}).get("correlationId") if isinstance(params.get("command"), dict) else params.get("correlationId")
            if isinstance(candidate, str) and KEY.fullmatch(candidate):
                correlation = candidate
        try:
            with self.env.cr.savepoint():
                staff = action in ("member", "members")
                config = self._v2_authorize(token, gateway_key, staff=staff)
                self = self.sudo().with_company(config.company_id).with_context(allowed_company_ids=[config.company_id.id])
                if action == "sessions":
                    return self._v2_sessions(config)
                if action == "roster":
                    return self._v2_roster(config, params.get("sessionId"))
                if action == "checkin":
                    return self._v2_check_in(config, params.get("command"))
                if action == "member":
                    return self._v2_member_read(config, params.get("memberId"))
                if action == "members":
                    members = config.integration_member_ids.filtered(lambda m: m.active and m.company_id == config.company_id)
                    return {"members": [{"id": str(m.id), "name": m.name} for m in members]}
                raise KioskProblem("INVALID_COMMAND")
        except KioskProblem as exc:
            return problem(exc.code, correlation)
        except AccessError:
            return problem("FORBIDDEN", correlation)
        except (ValidationError, UserError):
            return problem("ELIGIBILITY_REVIEW_REQUIRED", correlation)
        except (pg_errors.UniqueViolation, pg_errors.SerializationFailure, pg_errors.LockNotAvailable, pg_errors.DeadlockDetected):
            return problem("CONCURRENT_RETRY", correlation)
