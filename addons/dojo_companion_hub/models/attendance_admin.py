import json
from datetime import timedelta

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, text

MARKS = ("present", "late", "absent", "excused")
ATTENDED = ("present", "late")
WINDOW_DAYS = 31


class HubStaffRecordedAttendance(models.Model):
    _inherit = "dojo.attendance.log"

    def _send_checkin_notification(self, log):
        if self.env.context.get("dojo_hub_staff_recorded"):
            return None
        return super()._send_checkin_notification(log)


class HubAttendanceAdmin(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "attendance_sessions": {"handler": self._attendance_sessions, "roles": "owner manager instructor", "write": False},
            "attendance_sheet": {"handler": self._attendance_sheet, "roles": "owner manager instructor", "write": False},
            "mark_attendance": {"handler": self._mark_attendance, "roles": "owner manager instructor", "write": True,
                "replay": self._replay_attendance},
        })
        return operations

    def _replay_attendance(self, site, grant, receipt, saved):
        self._session(site, grant, str(receipt.session_id.id))
        if saved.get("reversal") and grant.role not in ("owner", "manager"):
            raise HubProblem("REVERSAL_REQUIRES_MANAGER", 403)

    def _attendance_window(self, session):
        now = fields.Datetime.now()
        return session.start_datetime <= now + timedelta(minutes=15) and session.end_datetime >= now - timedelta(days=WINDOW_DAYS)

    def _attendance_sessions(self, site, grant, p):
        exact(p, "")
        now = fields.Datetime.now()
        domain = [("company_id", "=", site.company_id.id), ("template_id.company_id", "=", site.company_id.id),
            ("state", "!=", "cancelled"), ("start_datetime", "<=", now + timedelta(minutes=15)),
            ("end_datetime", ">=", now - timedelta(days=7))]
        if grant.role == "instructor":
            domain.append(("id", "in", grant.session_ids.ids))
        sessions = self.env["dojo.class.session"].sudo().search(domain, order="start_datetime desc, id desc", limit=100)
        Enrollment = self.env["dojo.class.enrollment"].sudo()
        Log = self.env["dojo.attendance.log"].sudo()
        return {"sessions": [{"id": str(s.id), "title": s.template_id.name, "startsAt": iso(s.start_datetime), "endsAt": iso(s.end_datetime),
            "registered": Enrollment.search_count([("session_id", "=", s.id), ("status", "=", "registered")]),
            "attended": Log.search_count([("session_id", "=", s.id), ("status", "in", list(ATTENDED))])} for s in sessions],
            "windowDays": 7}

    def _hold(self, enrollment):
        if not enrollment or "dojo.credit.transaction" not in self.env:
            return None
        hold = self.env["dojo.credit.transaction"].sudo().search([("enrollment_id", "=", enrollment.id), ("transaction_type", "=", "hold")], limit=1)
        return hold.status if hold else None

    def _attendance_corrections(self, site, session, member_ids):
        receipts = self.env["dojo.hub.receipt"].sudo().search([("site_id", "=", site.id), ("operation", "=", "mark_attendance"),
            ("session_id", "=", session.id), ("member_id", "in", member_ids)], order="id desc", limit=200)
        history = {}
        for receipt in receipts:
            try:
                saved = json.loads(receipt.response_json)
            except ValueError:
                continue
            history.setdefault(receipt.member_id.id, []).append({"before": saved.get("before"), "after": saved.get("after"),
                "reason": saved.get("reason"), "actor": receipt.user_id.name, "at": iso(receipt.create_date), "reversal": bool(saved.get("reversal"))})
        return history

    def _attendance_sheet(self, site, grant, p):
        exact(p, "sessionId")
        session = self._session(site, grant, p["sessionId"])
        enrollments = self.env["dojo.class.enrollment"].sudo().search([("session_id", "=", session.id), ("status", "=", "registered"),
            ("member_id.company_id", "=", site.company_id.id), ("member_id.active", "=", True)], limit=500)
        logs = self.env["dojo.attendance.log"].sudo().search([("session_id", "=", session.id),
            ("member_id.company_id", "=", site.company_id.id)], limit=500)
        by_member = {log.member_id.id: log for log in logs}
        members = enrollments.member_id | logs.member_id.filtered("active")
        enrolled = {e.member_id.id: e for e in enrollments}
        history = self._attendance_corrections(site, session, members.ids)
        rows = []
        for member in members.sorted(lambda m: ((m.name or "").casefold(), m.id)):
            log, enrollment = by_member.get(member.id), enrolled.get(member.id)
            record = log or enrollment
            rows.append({"memberId": str(member.id), "name": member.name, "status": log.status if log else enrollment.attendance_state,
                "logId": str(log.id) if log else None, "version": self._version(record), "registered": bool(enrollment),
                "checkedInAt": iso(log.checkin_datetime) if log else None, "creditHold": self._hold(enrollment),
                "corrections": history.get(member.id, [])[:5]})
        return {"session": {"id": str(session.id), "title": session.template_id.name, "startsAt": iso(session.start_datetime),
            "endsAt": iso(session.end_datetime), "open": self._attendance_window(session)},
            "canReverse": grant.role in ("owner", "manager"), "marks": list(MARKS), "rows": rows, "limit": 500}

    def _mark_attendance(self, site, grant, p):
        exact(p, "sessionId memberId status expectedVersion reason")
        if p["status"] not in MARKS:
            raise HubProblem("INVALID_COMMAND", 400)
        reason = text(p["reason"], 500)
        session = self._session(site, grant, p["sessionId"])
        member = self.env["dojo.member"].sudo().search([("id", "=", record_id(p["memberId"])),
            ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not member:
            raise HubProblem("MEMBER_UNAVAILABLE", 404)
        if not self._attendance_window(session):
            raise HubProblem("ATTENDANCE_WINDOW_CLOSED")
        enrollment = self.env["dojo.class.enrollment"].sudo().search([("session_id", "=", session.id),
            ("member_id", "=", member.id), ("status", "=", "registered")], limit=1)
        Log = self.env["dojo.attendance.log"].sudo()
        log = Log.search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if not enrollment and not log:
            raise HubProblem("NOT_ON_ROSTER")
        for row in (enrollment, log):
            if row:
                self._lock(row)
        self._require_version(log or enrollment, p["expectedVersion"])
        before = log.status if log else enrollment.attendance_state
        after = p["status"]
        if before == after:
            raise HubProblem("ATTENDANCE_UNCHANGED")
        reversal = before in ATTENDED and after not in ATTENDED
        if reversal and grant.role not in ("owner", "manager"):
            raise HubProblem("REVERSAL_REQUIRES_MANAGER", 403)
        if log and grant.role == "instructor":
            raise HubProblem("CORRECTION_REQUIRES_MANAGER", 403)
        becomes_attended = after in ATTENDED and before not in ATTENDED
        if becomes_attended:
            if not enrollment:
                raise HubProblem("NOT_ON_ROSTER")
            self.env["dojo.kiosk.service"]._v2_eligible(site.kiosk_config_id, session, member, enrollment)
        now = fields.Datetime.now()
        stamp = "%s %s: %s → %s. %s" % (iso(now), self.env.user.name, before, after, reason)
        hold_before = self._hold(enrollment)
        if log:
            log.write({"status": after, "note": "\n".join(filter(None, [log.note, stamp]))})
            if becomes_attended and hasattr(Log, "_confirm_hold"):
                Log._confirm_hold(enrollment)
        else:
            log = Log.with_context(dojo_hub_staff_recorded=True).create({"session_id": session.id, "member_id": member.id, "enrollment_id": enrollment.id,
                "status": after, "checkin_datetime": min(now, session.start_datetime), "note": stamp})
        if enrollment:
            enrollment.write({"attendance_state": "present" if after in ATTENDED else after})
        log.invalidate_recordset()
        return {"memberId": str(member.id), "sessionId": str(session.id), "logId": str(log.id), "state": "attendance_recorded",
            "before": before, "after": after, "reason": reason, "reversal": reversal,
            "creditHold": {"before": hold_before, "after": self._hold(enrollment)}, "version": self._version(log)}
