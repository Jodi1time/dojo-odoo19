import hashlib
import json
import re

from odoo import api, fields, models


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$")


class DojoKioskCommandReceipt(models.Model):
    _name = "dojo.kiosk.command.receipt"
    _description = "Dojang Kiosk Command Receipt"
    _order = "create_date desc"

    idempotency_key = fields.Char(required=True, index=True, copy=False)
    fingerprint = fields.Char(required=True, index=True, copy=False)
    correlation_id = fields.Char(required=True, index=True, copy=False)
    config_id = fields.Many2one(
        "dojo.kiosk.config", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, ondelete="cascade", index=True
    )
    member_id = fields.Many2one(
        "dojo.member", required=True, ondelete="cascade", index=True
    )
    session_id = fields.Many2one(
        "dojo.class.session", required=True, ondelete="cascade", index=True
    )
    attendance_id = fields.Many2one(
        "dojo.attendance.log", ondelete="set null", index=True
    )
    response_json = fields.Text(required=True, copy=False)

    _unique_kiosk_command_key = models.Constraint(
        "unique(company_id, idempotency_key)",
        "This kiosk command key has already been used.",
    )


class DojoKioskV2Service(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _v2_validate_key(self, value):
        value = (value or "").strip()
        return value if _KEY_RE.fullmatch(value) else False

    def _v2_session_for_config(self, config, session_id):
        session = self.env["dojo.class.session"].sudo().browse(session_id).exists()
        if not session:
            return False
        if session.company_id and session.company_id != config.company_id:
            return False
        return session

    def _v2_member_for_config(self, config, member_id):
        member = self.env["dojo.member"].sudo().browse(member_id).exists()
        if not member or not member.active:
            return False
        if member.company_id and member.company_id != config.company_id:
            return False
        return member

    def _v2_covering_subscription(self, member, session):
        Subscription = self.env["sale.subscription"].sudo()
        if hasattr(Subscription, "_find_subscription_for_session"):
            return Subscription._find_subscription_for_session(member, session)

        subscriptions = Subscription.search([
            ("member_id", "=", member.id),
            ("state", "=", "active"),
            ("company_id", "=", session.company_id.id),
        ])
        template = session.template_id
        program = template.program_id if template else False
        for subscription in subscriptions:
            plan = subscription.plan_id
            if not plan:
                continue
            if (
                plan.plan_type == "program"
                and program
                and program in plan.program_ids
            ):
                return subscription
            if (
                plan.plan_type == "course"
                and template
                and (
                    not plan.allowed_template_ids
                    or template in plan.allowed_template_ids
                )
            ):
                return subscription
        return Subscription.browse()

    @api.model
    def get_session_first_context(self, token, session_id):
        config = self.validate_token(token)
        session = self._v2_session_for_config(config, session_id)
        if not session:
            return {"success": False, "code": "SESSION_UNAVAILABLE"}

        return {
            "success": True,
            "session": {
                "id": session.id,
                "name": session.template_id.name if session.template_id else session.name,
                "program": (
                    session.template_id.program_id.name
                    if session.template_id and session.template_id.program_id
                    else ""
                ),
                "start": fields.Datetime.to_string(session.start_datetime),
                "end": fields.Datetime.to_string(session.end_datetime),
                "state": session.state,
                "capacity": session.capacity,
                "seats_taken": session.seats_taken,
                "instructor": (
                    session.instructor_profile_id.name
                    if session.instructor_profile_id
                    else ""
                ),
            },
        }

    @api.model
    def search_session_roster(self, token, session_id, query, limit=12):
        config = self.validate_token(token)
        session = self._v2_session_for_config(config, session_id)
        if not session or session.state != "open":
            return {"success": False, "code": "SESSION_UNAVAILABLE", "members": []}

        query = (query or "").strip()
        if len(query) < 2:
            return {"success": True, "members": []}

        enrollments = self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id),
            ("status", "=", "registered"),
            "|",
            ("member_id.name", "ilike", query),
            ("member_id.member_number", "ilike", query),
        ], limit=min(int(limit or 12), 20), order="member_id asc")

        members = []
        for enrollment in enrollments:
            member = enrollment.member_id
            members.append({
                "member_id": member.id,
                "name": member.name or "",
                "belt_rank": (
                    member.current_rank_id.name if member.current_rank_id else ""
                ),
                "image_url": "/web/image/dojo.member/%d/image_128" % member.id,
                "attendance_state": enrollment.attendance_state or "pending",
            })
        return {"success": True, "members": members}

    @api.model
    def session_first_checkin(
        self,
        token,
        session_id,
        member_id,
        idempotency_key,
        correlation_id,
    ):
        config = self.validate_token(token)
        key = self._v2_validate_key(idempotency_key)
        correlation = self._v2_validate_key(correlation_id)
        if not key or not correlation:
            return {"success": False, "code": "INVALID_COMMAND"}

        session = self._v2_session_for_config(config, session_id)
        member = self._v2_member_for_config(config, member_id)
        if not session:
            return {"success": False, "code": "SESSION_UNAVAILABLE"}
        if not member:
            return {"success": False, "code": "MEMBER_UNAVAILABLE"}

        fingerprint = hashlib.sha256(
            ("%s:%s" % (session.id, member.id)).encode("utf-8")
        ).hexdigest()

        # Serialize retries for this tenant/key before reading or writing a receipt.
        advisory_lock = int(
            hashlib.sha256(
                ("%s:%s" % (config.company_id.id, key)).encode("utf-8")
            ).hexdigest()[:15],
            16,
        )
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [advisory_lock])

        Receipt = self.env["dojo.kiosk.command.receipt"].sudo()
        existing_command = Receipt.search([
            ("company_id", "=", config.company_id.id),
            ("idempotency_key", "=", key),
        ], limit=1)
        if existing_command:
            if existing_command.fingerprint != fingerprint:
                return {
                    "success": False,
                    "code": "IDEMPOTENCY_CONFLICT",
                    "correlation_id": correlation,
                }
            response = json.loads(existing_command.response_json)
            response["replayed"] = True
            response["correlation_id"] = correlation
            return response

        if session.state != "open":
            return {"success": False, "code": "SESSION_UNAVAILABLE"}

        enrollment = self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id),
            ("member_id", "=", member.id),
            ("status", "=", "registered"),
        ], limit=1)
        if not enrollment:
            return {"success": False, "code": "NOT_ON_ROSTER"}

        # Serialize different request keys for the same member/session roster row.
        self.env.cr.execute(
            "SELECT id FROM dojo_class_enrollment WHERE id = %s FOR UPDATE",
            [enrollment.id],
        )

        if member.membership_state in ("lead", "paused", "cancelled"):
            return {"success": False, "code": "MEMBERSHIP_INACTIVE"}

        if member.membership_state != "trial":
            subscription = self._v2_covering_subscription(member, session)
            if not subscription or subscription.state != "active":
                return {"success": False, "code": "ENTITLEMENT_INELIGIBLE"}

        Attendance = self.env["dojo.attendance.log"].sudo()
        attendance = Attendance.search([
            ("session_id", "=", session.id),
            ("member_id", "=", member.id),
        ], limit=1)

        already_checked_in = bool(attendance)
        if not attendance:
            now = fields.Datetime.now()
            status = (
                "late"
                if session.start_datetime and now > session.start_datetime
                else "present"
            )
            attendance = Attendance.create({
                "session_id": session.id,
                "member_id": member.id,
                "enrollment_id": enrollment.id,
                "status": status,
                "checkin_datetime": now,
            })
            enrollment.write({"attendance_state": "present"})

        response = {
            "success": True,
            "receipt": {
                "attendance_id": attendance.id,
                "member_id": member.id,
                "member_name": member.name or "",
                "session_id": session.id,
                "session_name": (
                    session.template_id.name if session.template_id else session.name
                ),
                "checked_in_at": fields.Datetime.to_string(
                    attendance.checkin_datetime
                ),
                "status": attendance.status,
                "already_checked_in": already_checked_in,
            },
            "replayed": False,
            "correlation_id": correlation,
        }

        Receipt.create({
            "idempotency_key": key,
            "fingerprint": fingerprint,
            "correlation_id": correlation,
            "config_id": config.id,
            "company_id": config.company_id.id,
            "member_id": member.id,
            "session_id": session.id,
            "attendance_id": attendance.id,
            "response_json": json.dumps(response, sort_keys=True),
        })
        return response
