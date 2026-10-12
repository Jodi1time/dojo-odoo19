"""Staff identity, PIN and native HR attendance. No public employee directory.

The existing instructor profile owns the employee link. The signed-in user's
site grant is authority; the shared kiosk PIN is an additional gate, never an
identity selector. Native hr.attendance retains overlap and payroll rules.
"""
from datetime import datetime, timedelta, timezone

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id, session_version
from .service import HubProblem, exact, text


class InstructorService(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _instructor_profiles(self, site, grant, own=False):
        self._role(grant)
        domain = [("company_id", "=", site.company_id.id), ("active", "=", True),
                  ("employee_id.active", "=", True), ("employee_id.company_id", "=", site.company_id.id)]
        if own or grant.role == "instructor":
            domain.append(("user_id", "=", self.env.uid))
        return self.env["dojo.instructor.profile"].sudo().search(domain)

    def _own_instructor(self, site, grant):
        profile = self._instructor_profiles(site, grant, own=True)
        if len(profile) != 1 or profile.employee_id.user_id.id != self.env.uid:
            raise HubProblem("INSTRUCTOR_PROFILE_REQUIRED", 403)
        return profile

    def _shift_data(self, shift, name):
        return {"id": str(shift.id), "instructor": name, "checkIn": iso(shift.check_in),
                "checkOut": iso(shift.check_out), "hours": shift.worked_hours,
                "version": session_version(shift), "state": shift.state,
                "needsReview": shift.out_mode in ("auto_check_out", "technical"),
                "correctable": bool(shift.check_out) and shift.state == "draft" and not shift.source_attendance_id}

    def _instructor_context(self, site, grant, p):
        exact(p, "")
        profiles = self._instructor_profiles(site, grant)
        own = profiles.filtered(lambda i: i.user_id.id == self.env.uid and i.employee_id.user_id.id == self.env.uid)
        domain = [("employee_id", "in", profiles.employee_id.ids), ("source_attendance_id", "=", False),
                  ("employee_id.company_id", "=", site.company_id.id)]
        current = self.env["hr.attendance"].sudo().search([
            ("employee_id", "in", own.employee_id.ids), ("check_out", "=", False)], limit=1)
        shifts = self.env["hr.attendance"].sudo().search(domain + [
            ("check_in", ">=", fields.Datetime.now() - timedelta(days=31))], order="check_in desc, id desc", limit=101)
        names = {i.employee_id.id: i.name for i in profiles}
        return {"instructor": {"name": own.name, "id": str(own.id)} if len(own) == 1 else None,
                "current": self._shift_data(current, own.name) if current else None,
                "canCorrect": grant.role in ("owner", "manager"),
                "shifts": [self._shift_data(a, names[a.employee_id.id]) for a in shifts[:100]],
                "windowDays": 31, "limit": 100, "truncated": len(shifts) > 100}

    def _clock_gate(self, site, grant, p):
        exact(p, "direction currentShiftId pin")
        self._own_instructor(site, grant)
        if p["direction"] not in ("in", "out") or not isinstance(p["pin"], str) or not p["pin"].isascii() or not p["pin"].isdigit() or len(p["pin"]) != 6:
            raise HubProblem("INVALID_COMMAND", 400)
        if (p["direction"] == "in" and p["currentShiftId"] is not None) or (p["direction"] == "out" and not p["currentShiftId"]):
            raise HubProblem("INVALID_COMMAND", 400)
        # Serialize guesses too; a failed PIN must RETURN (not raise/rollback)
        # so the existing lockout counter is committed by the request.
        self._lock(site.kiosk_config_id)
        result = self.env["dojo.kiosk.service"].sudo().verify_pin(p["pin"], config_id=site.kiosk_config_id.id)
        if not result.get("success"):
            return {"error": {"code": "PIN_LOCKED" if result.get("error") == "locked" else "PIN_INCORRECT", "status": 403}}

    def _instructor_clock(self, site, grant, p):
        profile = self._own_instructor(site, grant)
        employee = profile.employee_id
        self._lock(employee)
        Attendance = self.env["hr.attendance"].sudo().with_company(site.company_id)
        current = Attendance.search([("employee_id", "=", employee.id), ("check_out", "=", False)], limit=1)
        if p["direction"] == "in":
            if current:
                raise HubProblem("ALREADY_CLOCKED_IN")
            shift = Attendance.create({"employee_id": employee.id, "check_in": fields.Datetime.now(), "in_mode": "manual"})
        else:
            if not current or current.id != record_id(p["currentShiftId"]):
                raise HubProblem("SHIFT_CHANGED")
            if current.state != "draft" or current.source_attendance_id:
                raise HubProblem("TIMESHEET_REVIEW_REQUIRED")
            shift = current
            shift.write({"check_out": fields.Datetime.now(), "out_mode": "manual"})
        shift.flush_recordset()
        shift.invalidate_recordset()
        return {"state": "clocked_in" if p["direction"] == "in" else "clocked_out",
                "shift": self._shift_data(shift, profile.name)}

    def _recheck_shift_receipt(self, site, grant, result, own=False):
        profiles = self._instructor_profiles(site, grant, own=own)
        shift = self.env["hr.attendance"].sudo().search([
            ("id", "=", record_id(result["shift"]["id"])),
            ("employee_id", "in", profiles.employee_id.ids),
            ("employee_id.company_id", "=", site.company_id.id)], limit=1)
        if not shift:
            raise HubProblem("SHIFT_UNAVAILABLE", 404)

    def _correct_timesheet(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "shiftId expectedVersion checkIn checkOut reason")
        reason = text(p["reason"], 500)
        profiles = self._instructor_profiles(site, grant)
        shift = self.env["hr.attendance"].sudo().with_company(site.company_id).search([
            ("id", "=", record_id(p["shiftId"])), ("employee_id", "in", profiles.employee_id.ids),
            ("employee_id.company_id", "=", site.company_id.id), ("source_attendance_id", "=", False)], limit=1)
        if not shift:
            raise HubProblem("SHIFT_UNAVAILABLE", 404)
        self._lock(shift.employee_id)
        self._lock(shift)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(shift):
            raise HubProblem("VERSION_CONFLICT")
        if not shift.check_out or shift.state != "draft":
            raise HubProblem("TIMESHEET_REVIEW_REQUIRED")
        values = {}
        for field, key in (("check_in", "checkIn"), ("check_out", "checkOut")):
            try:
                value = datetime.fromisoformat(p[key].replace("Z", "+00:00"))
                if value.tzinfo is None:
                    raise ValueError()
                values[field] = value.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError, AttributeError):
                raise HubProblem("INVALID_COMMAND", 400)
        if not values["check_in"] < values["check_out"] <= fields.Datetime.now() or values["check_out"] - values["check_in"] > timedelta(hours=24):
            raise HubProblem("INVALID_SHIFT_TIMES", 422)
        before = {"checkIn": iso(shift.check_in), "checkOut": iso(shift.check_out)}
        shift.with_context(tracking_disable=True).write({**values, "out_mode": "manual"})
        shift.flush_recordset()
        shift.invalidate_recordset()
        profile = profiles.filtered(lambda i: i.employee_id == shift.employee_id)
        return {"state": "timesheet_corrected", "shift": self._shift_data(shift, profile.name),
                "before": before, "reason": reason, "actorId": str(self.env.uid)}
