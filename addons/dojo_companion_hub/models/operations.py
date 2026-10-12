"""Read-only operations reports. No copied business records or inferred absences."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso
from .service import HubProblem, exact


class HubOperations(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _report_rows(self, model, domain, order="id", limit=10000):
        rows = self.env[model].sudo().search(domain, order=order, limit=limit + 1)
        if len(rows) > limit:
            # Never label a silently truncated total as a complete report.
            raise HubProblem("REPORT_TOO_LARGE", 422)
        return rows

    def _operations_report(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "fromDate toDate")
        try:
            start, end = (date.fromisoformat(p[k]) for k in ("fromDate", "toDate"))
            if any(not isinstance(p[k], str) or len(p[k]) != 10 for k in ("fromDate", "toDate")) or not 0 <= (end-start).days <= 30:
                raise ValueError()
        except (ValueError, TypeError):
            raise HubProblem("INVALID_REPORT_RANGE", 400)
        tz = ZoneInfo(site.company_id.partner_id.tz or "UTC")
        lower = datetime.combine(start, time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
        upper = datetime.combine(end+timedelta(days=1), time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
        sessions = self._report_rows("dojo.class.session", [
            ("company_id", "=", site.company_id.id), ("template_id.company_id", "=", site.company_id.id),
            ("start_datetime", ">=", lower), ("start_datetime", "<", upper), ("state", "in", ["open", "done"])])
        logs = self._report_rows("dojo.attendance.log", [
            ("session_id", "in", sessions.ids), ("member_id.company_id", "=", site.company_id.id)])
        daily = defaultdict(Counter)
        for log in logs:
            day = str(log.session_id.start_datetime.replace(tzinfo=timezone.utc).astimezone(tz).date())
            daily[day][log.status] += 1
        profiles = self._instructor_profiles(site, grant)
        shifts = self._report_rows("hr.attendance", [
            ("employee_id", "in", profiles.employee_id.ids), ("employee_id.company_id", "=", site.company_id.id),
            ("source_attendance_id", "=", False), ("check_in", ">=", lower), ("check_in", "<", upper)])
        workload = []
        for profile in profiles:
            assigned = sessions.filtered(lambda s: s.instructor_profile_id == profile)
            worked = shifts.filtered(lambda s: s.employee_id == profile.employee_id)
            if assigned or worked:
                workload.append({"id": str(profile.id), "name": profile.name, "sessions": len(assigned),
                    "scheduledHours": sum((s.end_datetime-s.start_datetime).total_seconds()/3600 for s in assigned),
                    "closedShiftHours": sum(worked.filtered("check_out").mapped("worked_hours")),
                    "openShifts": len(worked.filtered(lambda s: not s.check_out))})
        members = self._report_rows("dojo.member", [("company_id", "=", site.company_id.id), ("active", "=", True)])
        ranks = Counter(m.current_rank_id.name if m.current_rank_id and m.current_rank_id.company_id in (self.env["res.company"], site.company_id) else "Not recorded" for m in members)
        households = Counter()
        for member in members:
            home = member.partner_id.parent_id
            if home.is_household and home.company_id in (self.env["res.company"], site.company_id):
                households[home.id] += 1
        return {"fromDate": str(start), "toDate": str(end), "timeZone": str(tz), "generatedAt": iso(fields.Datetime.now()),
            "sessions": len(sessions), "classes": [{"id": str(s.id), "name": s.template_id.name, "startsAt": iso(s.start_datetime)} for s in sessions.sorted("start_datetime")],
            "attendance": {"recorded": len(logs), "attended": len(logs.filtered(lambda a: a.status in ("present", "late"))),
                "daily": [{"date": day, "counts": dict(counts)} for day, counts in sorted(daily.items())]},
            "workload": workload, "unassignedSessions": len(sessions.filtered(lambda s: not s.instructor_profile_id)),
            "beltDistribution": [{"name": name, "members": count} for name, count in sorted(ranks.items())],
            "family": {"households": len(households), "membersInHouseholds": sum(households.values()),
                "membersWithoutHousehold": len(members)-sum(households.values())},
            "scope": "Current company; class/shift start dates in the school timezone. Belts and households are a current active-member snapshot."}

    def _session_roster(self, site, grant, p):
        self._role(grant)
        exact(p, "sessionId")
        session = self._session(site, grant, p["sessionId"])
        enrollments = self._report_rows("dojo.class.enrollment", [
            ("session_id", "=", session.id), ("status", "=", "registered"),
            ("member_id.company_id", "=", site.company_id.id), ("member_id.active", "=", True)], limit=500)
        logs = self._report_rows("dojo.attendance.log", [("session_id", "=", session.id), ("member_id", "in", enrollments.member_id.ids)], limit=500)
        actual = {a.member_id.id: a for a in logs}
        return {"session": {"id": str(session.id), "name": session.template_id.name, "startsAt": iso(session.start_datetime),
            "endsAt": iso(session.end_datetime), "instructor": session.instructor_profile_id.name if session.instructor_profile_id.company_id == site.company_id else None, "state": session.state},
            "timeZone": site.company_id.partner_id.tz or "UTC", "generatedAt": iso(fields.Datetime.now()),
            "members": [{"id": str(e.member_id.id), "name": e.member_id.name,
                "rank": e.member_id.current_rank_id.name if e.member_id.current_rank_id and e.member_id.current_rank_id.company_id in (self.env["res.company"], site.company_id) else "Not recorded",
                "attendance": actual[e.member_id.id].status if e.member_id.id in actual else e.attendance_state}
                for e in enrollments.sorted(lambda row: (row.member_id.name or "", row.member_id.id))]}
