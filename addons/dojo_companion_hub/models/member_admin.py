import base64
import binascii
import io
import json

from PIL import Image

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, fingerprint, photo_type, text

PHOTO_BYTES = 300 * 1024
ASSIGNMENT_LIMIT = 50
OPEN_MEMBERSHIP = ("active", "paused", "pending")


class HubMemberAdmin(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "archive_review": {"handler": self._archive_review, "roles": "owner manager", "write": False},
            "archived_members": {"handler": self._archived_members, "roles": "owner manager", "write": False},
            "course_roster": {"handler": self._course_roster, "roles": "owner manager", "write": False},
            "course_assignment_preview": {"handler": self._course_assignment_preview, "roles": "owner manager", "write": False},
            "archive_member": {"handler": self._archive_member, "roles": "owner manager", "write": True,
                "replay": self._replay_company_member},
            "restore_member": {"handler": self._restore_member, "roles": "owner manager", "write": True,
                "replay": self._replay_company_member},
            "update_member_photo": {"handler": self._update_member_photo, "roles": "owner manager instructor", "write": True,
                "replay": self._replay_photo},
            "assign_course_members": {"handler": self._assign_course_members, "roles": "owner manager", "write": True,
                "replay": self._replay_course},
        })
        return operations

    def _replay_company_member(self, site, grant, receipt, saved):
        if not receipt.member_id or receipt.member_id.company_id != site.company_id:
            raise HubProblem("MEMBER_UNAVAILABLE", 404)

    def _replay_photo(self, site, grant, receipt, saved):
        self._member(site, grant, str(receipt.member_id.id))

    def _replay_course(self, site, grant, receipt, saved):
        self._course_template(site, saved["templateId"])

    def _archived_member(self, site, value):
        member = self.env["dojo.member"].sudo().with_context(active_test=False).search([
            ("id", "=", record_id(value)), ("company_id", "=", site.company_id.id), ("active", "=", False)], limit=1)
        if not member:
            raise HubProblem("MEMBER_UNAVAILABLE", 404)
        return member

    def _archive_impacts(self, site, member):
        now = fields.Datetime.now()
        bookings = self.env["dojo.class.enrollment"].sudo().search([
            ("member_id", "=", member.id), ("status", "=", "registered"), ("attendance_state", "=", "pending"),
            ("session_id.start_datetime", ">", now), ("session_id.company_id", "=", site.company_id.id)], limit=500)
        memberships = self.env["sale.subscription"].sudo().search([
            ("member_id", "=", member.id), ("company_id", "=", site.company_id.id)], limit=100).filtered(
            lambda s: s.state in OPEN_MEMBERSHIP)
        household = member.partner_id.parent_id.filtered("is_household")
        relatives = self.env["dojo.member"].sudo().search([
            ("partner_id.parent_id", "=", household.id), ("id", "!=", member.id)], limit=1) if household else household
        users = member.partner_id.user_ids.filtered("active")
        courses = self.env["dojo.class.template"].sudo().search([
            ("company_id", "=", site.company_id.id), ("course_member_ids", "in", [member.id])], limit=100)
        blockers = []
        if memberships:
            blockers.append("MEMBERSHIP_REVIEW_REQUIRED")
        if household and relatives and household.primary_guardian_id == member.partner_id:
            blockers.append("PRIMARY_GUARDIAN_REVIEW_REQUIRED")
        if users:
            blockers.append("PORTAL_ACCESS_REVIEW_REQUIRED")
        return bookings, {"futureBookings": [{"enrollmentId": str(b.id), "title": b.session_id.template_id.name,
                "startsAt": iso(b.session_id.start_datetime)} for b in bookings[:20]],
            "futureBookingCount": len(bookings), "openMemberships": len(memberships), "portalAccounts": len(users),
            "household": household.name or None, "courses": [{"id": str(c.id), "name": c.name} for c in courses],
            "blockers": blockers}

    def _archive_review(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        _, impacts = self._archive_impacts(site, member)
        return {"memberId": str(member.id), "name": member.name, "version": self._version(member), **impacts}

    def _archive_member(self, site, grant, p):
        exact(p, "memberId expectedVersion reason")
        member = self._member(site, grant, p["memberId"])
        reason = text(p["reason"], 500)
        self._lock(member)
        self._require_version(member, p["expectedVersion"])
        bookings, impacts = self._archive_impacts(site, member)
        if impacts["blockers"]:
            raise HubProblem(impacts["blockers"][0])
        for booking in bookings.sorted("id"):
            self._lock(booking)
        bookings.write({"status": "cancelled"})
        member.write({"active": False})
        return {"memberId": str(member.id), "state": "archived", "reason": reason,
            "cancelledBookingIds": [str(b.id) for b in bookings], "courseRostersKept": len(impacts["courses"])}

    def _restore_member(self, site, grant, p):
        exact(p, "memberId expectedVersion reason")
        member = self._archived_member(site, p["memberId"])
        reason = text(p["reason"], 500)
        self._lock(member)
        self._require_version(member, p["expectedVersion"])
        member.write({"active": True})
        return {"memberId": str(member.id), "state": "restored", "reason": reason, "bookingsRestored": 0}

    def _archived_members(self, site, grant, p):
        exact(p, "query")
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        domain = [("company_id", "=", site.company_id.id), ("active", "=", False)]
        if p["query"].strip():
            domain.append(("name", "ilike", p["query"].strip()))
        members = self.env["dojo.member"].sudo().with_context(active_test=False).search(domain, order="write_date desc, id desc", limit=51)
        receipts = self.env["dojo.hub.receipt"].sudo().search([("site_id", "=", site.id),
            ("operation", "=", "archive_member"), ("member_id", "in", members.ids)], order="id desc")
        latest = {}
        for receipt in receipts:
            latest.setdefault(receipt.member_id.id, receipt)
        rows = []
        for member in members[:50]:
            receipt = latest.get(member.id)
            reason = None
            if receipt:
                try:
                    reason = json.loads(receipt.response_json).get("reason")
                except ValueError:
                    reason = None
            rows.append({"id": str(member.id), "name": member.name, "memberNumber": member.member_number or "",
                "version": self._version(member), "archivedAt": iso(member.write_date),
                "archivedBy": receipt.user_id.name if receipt else None, "reason": reason})
        return {"members": rows, "more": len(members) > 50}

    def _photo_payload(self, value):
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > (PHOTO_BYTES * 4) // 3 + 8:
            raise HubProblem("PHOTO_TOO_LARGE", 422)
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise HubProblem("PHOTO_INVALID", 422)
        if len(raw) > PHOTO_BYTES:
            raise HubProblem("PHOTO_TOO_LARGE", 422)
        if not photo_type(raw):
            raise HubProblem("PHOTO_TYPE_UNSUPPORTED", 422)
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
        except Exception:
            raise HubProblem("PHOTO_INVALID", 422)
        if min(width, height) < 64 or max(width, height) > 4096:
            raise HubProblem("PHOTO_DIMENSIONS", 422)
        return value

    def _update_member_photo(self, site, grant, p):
        exact(p, "memberId expectedVersion image")
        member = self._member(site, grant, p["memberId"])
        image = self._photo_payload(p["image"])
        self._lock(member)
        self._lock(member.partner_id)
        if not self._contact_editable(site, member):
            raise HubProblem("SHARED_CONTACT_REVIEW_REQUIRED")
        self._require_version(member, p["expectedVersion"])
        member.partner_id.write({"image_1920": image or False})
        self._touch(member)
        return {"memberId": str(member.id), "state": "photo_updated" if image else "photo_removed",
            "version": self._version(member), "hasPhoto": bool(member.image_128)}

    def _course_template(self, site, value):
        template = self.env["dojo.class.template"].sudo().search([
            ("id", "=", record_id(value)), ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not template:
            raise HubProblem("COURSE_UNAVAILABLE", 404)
        return template

    def _future_sessions(self, template):
        return self.env["dojo.class.session"].sudo().search([
            ("template_id", "=", template.id), ("start_datetime", ">", fields.Datetime.now()),
            ("state", "not in", ["done", "cancelled"])], order="start_datetime, id", limit=500)

    def _course_roster(self, site, grant, p):
        exact(p, "templateId")
        templates = self.env["dojo.class.template"].sudo().search([
            ("company_id", "=", site.company_id.id), ("active", "=", True)], order="name, id", limit=100)
        result = {"templates": [{"id": str(t.id), "name": t.name, "version": self._version(t),
            "memberCount": len(t.course_member_ids), "autoEnroll": t.auto_enroll_members} for t in templates], "course": None}
        if p["templateId"]:
            template = self._course_template(site, p["templateId"])
            members = self._members(site, grant)
            roster = template.course_member_ids.filtered(lambda m: m.company_id == site.company_id)
            result["course"] = {"id": str(template.id), "name": template.name, "version": self._version(template),
                "autoEnroll": template.auto_enroll_members, "upcomingSessions": len(self._future_sessions(template)),
                "members": [{"id": str(m.id), "name": m.name} for m in roster.sorted(lambda m: ((m.name or "").casefold(), m.id))],
                "candidates": [{"id": str(m.id), "name": m.name} for m in (members - roster)]}
        return result

    def _assignment_plan(self, site, grant, p):
        template = self._course_template(site, p["templateId"])
        add_ids = self._id_list(p["addMemberIds"], ASSIGNMENT_LIMIT)
        remove_ids = self._id_list(p["removeMemberIds"], ASSIGNMENT_LIMIT)
        if not add_ids and not remove_ids or set(add_ids) & set(remove_ids):
            raise HubProblem("INVALID_COMMAND", 400)
        add = self.env["dojo.member"].sudo().browse([self._member(site, grant, str(i)).id for i in add_ids])
        remove = self.env["dojo.member"].sudo().with_context(active_test=False).search([
            ("id", "in", remove_ids), ("company_id", "=", site.company_id.id)])
        roster = template.with_context(active_test=False).course_member_ids
        if len(remove) != len(remove_ids) or remove - roster:
            raise HubProblem("COURSE_ROSTER_CHANGED")
        if add & roster:
            raise HubProblem("COURSE_ROSTER_CHANGED")
        sessions = self._future_sessions(template)
        Enrollment = self.env["dojo.class.enrollment"].sudo()
        existing = Enrollment.search([("session_id", "in", sessions.ids), ("member_id", "in", add.ids)])
        pairs = {(e.session_id.id, e.member_id.id) for e in existing}
        preferences = {pref.member_id.id: pref for pref in self.env["dojo.course.auto.enroll"].sudo().with_context(
            active_test=False).search([("template_id", "=", template.id), ("member_id", "in", add.ids)])}
        planned = {}
        if template.auto_enroll_members:
            for session in sessions:
                for member in add:
                    pref = preferences.get(member.id)
                    if (session.id, member.id) not in pairs and (pref is None or pref.should_enroll_on_date(session.start_datetime.date())):
                        planned.setdefault(session, []).append(member)
        conflicts = []
        for session, members in planned.items():
            if session.capacity and session.capacity > 0:
                registered = Enrollment.search_count([("session_id", "=", session.id), ("status", "=", "registered")])
                if registered + len(members) > session.capacity:
                    conflicts.append({"sessionId": str(session.id), "title": template.name, "startsAt": iso(session.start_datetime),
                        "capacity": session.capacity, "registered": registered, "adding": len(members)})
        cancelling = Enrollment.search([("session_id", "in", sessions.ids), ("member_id", "in", remove.ids),
            ("status", "=", "registered")])
        plan = {"templateId": str(template.id), "course": template.name, "autoEnroll": template.auto_enroll_members,
            "add": [{"memberId": str(m.id), "name": m.name,
                "futureBookings": sum(1 for members in planned.values() if m in members)} for m in add],
            "remove": [{"memberId": str(m.id), "name": m.name,
                "cancelledBookings": len(cancelling.filtered(lambda e: e.member_id == m))} for m in remove],
            "capacityConflicts": conflicts}
        token = fingerprint({"plan": plan, "version": self._version(template), "siteId": site.id, "userId": self.env.uid})
        return template, add, remove, plan, token

    def _course_assignment_preview(self, site, grant, p):
        exact(p, "templateId addMemberIds removeMemberIds")
        template, _, _, plan, token = self._assignment_plan(site, grant, p)
        return {**plan, "version": self._version(template), "valid": not plan["capacityConflicts"],
            "reviewToken": None if plan["capacityConflicts"] else token}

    def _assign_course_members(self, site, grant, p):
        exact(p, "templateId expectedVersion addMemberIds removeMemberIds reason reviewToken")
        reason = text(p["reason"], 500)
        template = self._course_template(site, p["templateId"])
        self._lock(template)
        self._require_version(template, p["expectedVersion"])
        template, add, remove, plan, token = self._assignment_plan(site, grant, p)
        if plan["capacityConflicts"]:
            raise HubProblem("CAPACITY_REVIEW_REQUIRED")
        if not isinstance(p["reviewToken"], str) or p["reviewToken"] != token:
            raise HubProblem("ASSIGNMENT_REVIEW_CHANGED")
        sessions = self._future_sessions(template)
        Enrollment = self.env["dojo.class.enrollment"].sudo()
        scope = (add | remove).ids
        before = Enrollment.search([("session_id", "in", sessions.ids), ("member_id", "in", scope), ("status", "=", "registered")])
        template.write({"course_member_ids": [(4, m.id) for m in add] + [(3, m.id) for m in remove]})
        self._touch(template)
        after = Enrollment.search([("session_id", "in", sessions.ids), ("member_id", "in", scope), ("status", "=", "registered")])
        return {"templateId": str(template.id), "state": "course_roster_updated", "reason": reason,
            "added": [str(i) for i in add.ids], "removed": [str(i) for i in remove.ids],
            "bookingsCreated": len(after - before), "bookingsCancelled": len(before - after),
            "version": self._version(template), "_scopeMemberIds": scope}
