"""
bridge_service.py
─────────────────
x.bridge.service — AbstractModel that owns ALL bridge business logic.

Controllers are thin HTTP adapters; every read/write goes through here.
All public methods receive an explicit `company_id` arg and enforce it
in every ORM domain — even under sudo() — to prevent cross-tenant leaks.

Method naming convention:
  get_*   → read-only, returns dicts safe for JSON serialisation
  do_*    → mutations, must call cr.commit() after returning
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class BridgeService(models.AbstractModel):
    _name = "x.bridge.service"
    _description = "Bridge Service Layer (business logic for API controllers)"

    # ═══════════════════════════════════════════════════════════════════════════
    # MEMBER reads
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def get_member_profile(self, member_id: int, company_id: int) -> dict:
        """
        Return a JSON-safe member profile dict.
        Joins through partner_id for name/email/phone (dojo.member uses _inherits).
        """
        member = self._get_member(member_id, company_id)

        current_rank = self.env["dojo.member.rank"].search(
            [("member_id", "=", member.id)],
            order="date_awarded desc",
            limit=1,
        )

        return {
            "id": member.id,
            "member_number": member.member_number,
            "name": member.name,
            "email": member.email,
            "phone": member.phone,
            "mobile": member.mobile,
            "membership_state": member.membership_state,
            "is_student": member.partner_id.is_student if hasattr(member.partner_id, "is_student") else None,
            "is_guardian": member.partner_id.is_guardian if hasattr(member.partner_id, "is_guardian") else None,
            "is_minor": member.partner_id.is_minor if hasattr(member.partner_id, "is_minor") else None,
            "company_id": member.company_id.id,
            "company_name": member.company_id.name,
            "belt_rank": (
                {
                    "id": current_rank.rank_id.id,
                    "name": current_rank.rank_id.name,
                    "date_awarded": (
                        current_rank.date_awarded.isoformat()
                        if current_rank.date_awarded
                        else None
                    ),
                }
                if current_rank
                else None
            ),
        }

    @api.model
    def get_member_subscriptions(self, member_id: int, company_id: int) -> list:
        """Return all subscriptions for the member within the company."""
        self._get_member(member_id, company_id)  # existence + tenant check

        subs = self.env["sale.subscription"].search(
            [
                ("member_id", "=", member_id),
                ("company_id", "=", company_id),
            ],
            order="create_date desc",
        )

        result = []
        for sub in subs:
            plan = sub.plan_id
            result.append(
                {
                    "id": sub.id,
                    "state": sub.state,
                    "plan": {
                        "id": plan.id,
                        "name": plan.name,
                        "price": float(plan.price) if plan.price else None,
                        "billing_period": plan.billing_period
                        if hasattr(plan, "billing_period")
                        else None,
                    },
                    "next_billing_date": (
                        sub.recurring_next_date.isoformat()
                        if sub.recurring_next_date
                        else None
                    ),
                    "start_date": (
                        sub.date_start.isoformat()
                        if sub.date_start
                        else None
                    ),
                }
            )
        return result

    @api.model
    def get_member_rank_history(self, member_id: int, company_id: int) -> dict:
        """Return current rank + full rank history for the member."""
        self._get_member(member_id, company_id)

        records = self.env["dojo.member.rank"].search(
            [("member_id", "=", member_id)],
            order="date_awarded desc",
        )

        history = [
            {
                "id": r.id,
                "rank_id": r.rank_id.id,
                "rank_name": r.rank_id.name,
                "rank_sequence": r.rank_id.sequence
                if hasattr(r.rank_id, "sequence")
                else None,
                "date_awarded": r.date_awarded.isoformat() if r.date_awarded else None,
            }
            for r in records
        ]

        return {
            "member_id": member_id,
            "current_rank": history[0] if history else None,
            "history": history,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # CLASS / SESSION reads
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def get_sessions(
        self,
        company_id: int,
        from_dt=None,
        to_dt=None,
        program_id: int | None = None,
        member_id: int | None = None,
    ) -> list:
        """
        Return schedule. All sessions are scoped to company_id.
        If member_id is supplied, each session includes `member_enrolled` bool.
        """
        domain = [("company_id", "=", company_id)]
        if from_dt:
            domain.append(("start_datetime", ">=", from_dt))
        if to_dt:
            domain.append(("start_datetime", "<=", to_dt))
        if program_id:
            domain.append(("template_id.program_id", "=", program_id))

        sessions = self.env["dojo.class.session"].search(
            domain, order="start_datetime asc", limit=200
        )

        enrolled_session_ids: set[int] = set()
        if member_id:
            enrollments = self.env["dojo.class.enrollment"].search(
                [
                    ("member_id", "=", member_id),
                    ("session_id", "in", sessions.ids),
                    ("status", "not in", ["cancelled"]),
                ]
            )
            enrolled_session_ids = {e.session_id.id for e in enrollments}

        result = []
        for s in sessions:
            template = s.template_id
            result.append(
                {
                    "id": s.id,
                    "state": s.state,
                    "start_datetime": s.start_datetime.isoformat()
                    if s.start_datetime
                    else None,
                    "end_datetime": s.end_datetime.isoformat()
                    if hasattr(s, "end_datetime") and s.end_datetime
                    else None,
                    "template": {
                        "id": template.id,
                        "name": template.name,
                        "program_id": template.program_id.id
                        if template.program_id
                        else None,
                        "program_name": template.program_id.name
                        if template.program_id
                        else None,
                        "max_capacity": template.max_capacity
                        if hasattr(template, "max_capacity")
                        else None,
                    },
                    "seats_taken": s.seats_taken
                    if hasattr(s, "seats_taken")
                    else None,
                    "member_enrolled": s.id in enrolled_session_ids,
                }
            )
        return result

    @api.model
    def get_session_detail(
        self, session_id: int, company_id: int, member_id: int | None = None
    ) -> dict:
        """Single session detail + optional enrollment status."""
        session = self._get_session(session_id, company_id)
        template = session.template_id

        enrollment = None
        if member_id:
            enr = self.env["dojo.class.enrollment"].search(
                [
                    ("session_id", "=", session_id),
                    ("member_id", "=", member_id),
                ],
                limit=1,
            )
            if enr:
                enrollment = {
                    "id": enr.id,
                    "status": enr.status,
                }

        return {
            "id": session.id,
            "state": session.state,
            "start_datetime": session.start_datetime.isoformat()
            if session.start_datetime
            else None,
            "end_datetime": session.end_datetime.isoformat()
            if hasattr(session, "end_datetime") and session.end_datetime
            else None,
            "company_id": session.company_id.id,
            "template": {
                "id": template.id,
                "name": template.name,
                "program_id": template.program_id.id if template.program_id else None,
                "program_name": template.program_id.name
                if template.program_id
                else None,
                "max_capacity": template.max_capacity
                if hasattr(template, "max_capacity")
                else None,
            },
            "seats_taken": session.seats_taken
            if hasattr(session, "seats_taken")
            else None,
            "enrollment": enrollment,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # MUTATIONS — callers must commit the cursor after these return successfully
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def do_enroll(self, session_id: int, member_id: int, company_id: int) -> dict:
        """Book a member into a live session using current Dojang enrollment states.

        Full sessions use the existing waitlist state rather than failing or
        inventing a parallel booking model.
        """
        session = self._get_session(session_id, company_id)
        member = self._get_member(member_id, company_id)

        if session.state != "open":
            raise UserError("Only open sessions can accept bookings.")

        if member.membership_state in ("cancelled", "paused", "lead"):
            raise UserError("Membership is not active for class booking.")

        active_sub = getattr(member, "active_subscription_id", False)
        if not active_sub or getattr(active_sub, "state", "") != "active":
            raise UserError("No active subscription found for this member.")

        existing = self.env["dojo.class.enrollment"].search(
            [
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
            ],
            limit=1,
        )
        if existing and existing.status in ("registered", "waitlist"):
            return {
                "enrollment_id": existing.id,
                "status": existing.status,
                "already_existed": True,
            }

        status = (
            "waitlist"
            if session.capacity > 0 and session.seats_taken >= session.capacity
            else "registered"
        )

        if existing:
            existing.write({
                "status": status,
                "attendance_state": "pending",
            })
            enrollment = existing
        else:
            enrollment = self.env["dojo.class.enrollment"].create(
                {
                    "session_id": session_id,
                    "member_id": member_id,
                    "status": status,
                    "attendance_state": "pending",
                }
            )

        _logger.info(
            "Bridge: booked member_id=%s in session_id=%s "
            "(enrollment_id=%s status=%s)",
            member_id,
            session_id,
            enrollment.id,
            enrollment.status,
        )
        return {
            "enrollment_id": enrollment.id,
            "status": enrollment.status,
            "already_existed": False,
        }

    @api.model
    def do_cancel_enrollment(
        self, session_id: int, member_id: int, company_id: int
    ) -> dict:
        """Cancel a registered/waitlisted booking for the member."""
        self._get_session(session_id, company_id)
        self._get_member(member_id, company_id)

        enrollment = self.env["dojo.class.enrollment"].search(
            [
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
                ("status", "in", ["registered", "waitlist"]),
            ],
            limit=1,
        )
        if not enrollment:
            raise UserError("No active booking found to cancel.")

        enrollment.write({"status": "cancelled"})
        _logger.info(
            "Bridge: cancelled enrollment_id=%s for member_id=%s session_id=%s",
            enrollment.id,
            member_id,
            session_id,
        )
        return {"enrollment_id": enrollment.id, "status": "cancelled"}

    @api.model
    def do_checkin(self, session_id: int, member_id: int, company_id: int) -> dict:
        """Record session-first attendance for a member.

        Invariants:
        - the session must be open
        - the member must belong to the same tenant/company
        - the member must have an active membership/subscription
        - the member must already be on the actual session roster
        - replay is idempotent
        """
        session = self._get_session(session_id, company_id)
        member = self._get_member(member_id, company_id)

        if session.state != "open":
            raise UserError("This session is not open for check-in.")

        if member.membership_state in ("cancelled", "paused", "lead"):
            raise UserError("Membership is not active. Please see the front desk.")

        active_sub = getattr(member, "active_subscription_id", False)
        if not active_sub or getattr(active_sub, "state", "") != "active":
            raise UserError("No active subscription found. Please see the front desk.")

        enrollment = self.env["dojo.class.enrollment"].search(
            [
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
                ("status", "=", "registered"),
            ],
            limit=1,
        )
        if not enrollment:
            raise UserError(
                "Member is not on this session roster. "
                "Book the member or use an authorized staff override first."
            )

        existing = self.env["dojo.attendance.log"].search(
            [
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
            ],
            limit=1,
        )
        if existing:
            return {
                "attendance_id": existing.id,
                "checkin_datetime": (
                    existing.checkin_datetime.isoformat()
                    if existing.checkin_datetime
                    else None
                ),
                "status": getattr(existing, "status", "already_checked_in"),
                "already_existed": True,
                "session_id": session.id,
                "member_id": member.id,
            }

        now = fields.Datetime.now()
        attendance_status = (
            "late"
            if session.start_datetime and now > session.start_datetime
            else "present"
        )

        log = self.env["dojo.attendance.log"].create(
            {
                "session_id": session_id,
                "member_id": member_id,
                "enrollment_id": enrollment.id,
                "status": attendance_status,
                "checkin_datetime": now,
            }
        )
        enrollment.write({"attendance_state": "present"})

        _logger.info(
            "Bridge: checked in member_id=%s to session_id=%s "
            "(log_id=%s status=%s)",
            member_id,
            session_id,
            log.id,
            attendance_status,
        )
        return {
            "attendance_id": log.id,
            "checkin_datetime": (
                log.checkin_datetime.isoformat()
                if log.checkin_datetime
                else None
            ),
            "status": attendance_status,
            "already_existed": False,
            "session_id": session.id,
            "member_id": member.id,
            "session_name": session.name,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Private helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_member(self, member_id: int, company_id: int):
        member = self.env["dojo.member"].search(
            [("id", "=", member_id), ("company_id", "=", company_id)],
            limit=1,
        )
        if not member:
            raise UserError(
                f"Member {member_id} not found in company {company_id}."
            )
        return member

    def _get_session(self, session_id: int, company_id: int):
        session = self.env["dojo.class.session"].search(
            [("id", "=", session_id), ("company_id", "=", company_id)],
            limit=1,
        )
        if not session:
            raise UserError(
                f"Session {session_id} not found in company {company_id}."
            )
        return session
