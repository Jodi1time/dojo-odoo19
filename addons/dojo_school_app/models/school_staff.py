import base64
import binascii
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from markupsafe import Markup, escape

from odoo import fields, models, tools
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mail import html2plaintext, plaintext2html

from .school_app import ATTENDED, CURRENT, SchoolAppProblem

IMAGE = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")
MAX_IMAGE_BYTES = 3 * 1024 * 1024
FLAG_HOURS = 12
TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
AUTOMATION_KINDS = ("bday", "exp", "welcome", "milestone", "absence")
AUTOMATION_DEFAULTS = {
    "bday_enabled": False,
    "bday_time": "09:00",
    "bday_subject": "Happy Birthday, {name}!",
    "bday_template": "Happy birthday, {name}! Everyone at the school wishes you a great year of training.",
    "exp_enabled": False,
    "exp_days": 7,
    "exp_subject": "Your membership is ending soon",
    "exp_template": "Hi {name}, your membership ends in a few days. Stop by the front desk or reply to renew.",
    "welcome_enabled": False,
    "welcome_subject": "Welcome to the school, {name}!",
    "welcome_template": "Welcome, {name}! We're excited to have you on the mat.",
    "milestone_enabled": False,
    "milestone_subject": "Congratulations on your {belt} belt, {name}!",
    "milestone_template": "Congratulations, {name}! You've earned your {belt} belt. Keep up the great work.",
    "absence_enabled": False,
    "absence_days": 14,
    "absence_subject": "We miss you, {name}",
    "absence_template": "Hi {name}, we haven't seen you in class for a while. We'd love to have you back.",
    "absence_max_per_run": 10,
}
AUTOMATION_LIMITS = {"exp_days": (1, 90), "absence_days": (3, 180), "absence_max_per_run": (1, 100)}


class DojoSchoolAppStaff(models.AbstractModel):
    _inherit = "dojo.school.app"

    def catalog(self, ctx):
        self._allow(ctx, "catalog")
        ranks = self.env["dojo.belt.rank"].sudo().search([("company_id", "in", [False, ctx.company.id]), ("active", "=", True)],
                                                         order="sequence asc, id asc")
        templates = self.env["dojo.class.template"].sudo().search([("company_id", "=", ctx.company.id), ("active", "=", True)],
                                                                  order="name asc, id asc")
        return {"belts": list(dict.fromkeys(ranks.mapped("name"))), "classes": list(dict.fromkeys(templates.mapped("name")))}

    def _templates(self, company):
        return self.env["dojo.class.template"].sudo().with_company(company).search(
            [("company_id", "=", company.id), ("active", "=", True)])

    def _classes(self, company, members):
        result = defaultdict(list)
        if members:
            for template in self._templates(company).filtered(lambda row: row.course_member_ids & members):
                for member in template.course_member_ids & members:
                    result[member.id].append(template.name)
        return {key: sorted(value) for key, value in result.items()}

    def _subscriptions(self, members):
        result = {}
        if members:
            for subscription in self.env["sale.subscription"].sudo().search(
                    [("member_id", "in", members.ids), ("state", "=", "active")], order="date asc, id asc"):
                result.setdefault(subscription.member_id.id, subscription)
        return result

    def _emergency_contacts(self, members):
        result = {}
        if members:
            for contact in self.env["dojo.emergency.contact"].sudo().search(
                    [("member_id", "in", members.ids)], order="is_primary desc, id asc"):
                result.setdefault(contact.member_id.id, contact)
        return result

    def _guardian(self, member):
        household = member.partner_id.parent_id
        if household and household.is_household and household.primary_guardian_id != member.partner_id:
            return household, household.primary_guardian_id
        return household if household and household.is_household else self.env["res.partner"], self.env["res.partner"]

    def _recipient(self, member):
        guardian = self._guardian(member)[1]
        if guardian and guardian.email:
            return guardian
        return member.partner_id if member.email else self.env["res.partner"]

    def _student(self, ctx, member, classes=None, subscription=None, contact=None):
        first, last = self._split(member.name)
        guardian = self._guardian(member)[1]
        return {
            "id": str(member.id),
            "first": first,
            "last": last,
            "dob": fields.Date.to_string(member.date_of_birth) if member.date_of_birth else None,
            "belt": member.current_rank_id.name or "",
            "class": classes or [],
            "phone": member.phone or "",
            "email": member.email or "",
            "parent_name": guardian.name or "",
            "parent_phone": guardian.phone or "",
            "parent_email": guardian.email or "",
            "emergency_name": (contact.name or "") if contact else "",
            "emergency_phone": (contact.phone or "") if contact else "",
            "expdate": fields.Date.to_string(subscription.date) if subscription and subscription.date else None,
            "notes": html2plaintext(member.partner_id.comment or "").strip() if member.partner_id.comment else "",
            "photo_url": ("/api/admin/students/photo?id=%d&v=%s" % (member.id, self._photo_version(member))
                          if member.image_128 else None),
            "status": member.membership_state,
            "member_number": member.member_number or "",
        }

    def _students(self, ctx, members):
        classes = self._classes(ctx.company, members)
        subscriptions = self._subscriptions(members)
        contacts = self._emergency_contacts(members)
        return [self._student(ctx, member, classes.get(member.id), subscriptions.get(member.id), contacts.get(member.id))
                for member in members]

    def students(self, ctx):
        self._allow(ctx, "students.read")
        members = self._members(ctx).search(self._member_domain(ctx), order="name asc, id asc", limit=5000)
        return {"students": self._students(ctx, members)}

    def _rank_named(self, ctx, name):
        rank = self.env["dojo.belt.rank"].sudo().search([("company_id", "in", [False, ctx.company.id]), ("active", "=", True),
                                                         ("name", "=ilike", name)], order="sequence asc", limit=1)
        if not rank:
            raise SchoolAppProblem("UNKNOWN_BELT", 422)
        return rank

    def _class_names(self, value):
        if value in (None, "", False):
            return []
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list) or len(value) > 50:
            raise SchoolAppProblem("INVALID")
        return [name for name in dict.fromkeys(self._text(item, 120) for item in value) if name]

    def _award(self, ctx, member, rank, note):
        awarded_by = self.env["dojo.instructor.profile"].sudo().search([
            ("user_id", "=", ctx.user.id), ("company_id", "=", ctx.company.id)], limit=1)
        row = self.env["dojo.member.rank"].sudo().create({
            "member_id": member.id, "rank_id": rank.id, "date_awarded": self._today(ctx.company),
            "awarded_by": awarded_by.id or False, "notes": note, "company_id": ctx.company.id})
        member.invalidate_recordset()
        return row

    def _save_guardian(self, ctx, member, name, phone, email):
        household, guardian = self._guardian(member)
        if guardian:
            guardian.write({"name": name or guardian.name, "phone": phone or False, "email": email or False, "is_guardian": True})
            return
        if not (name or phone or email):
            return
        last = self._split(member.name)[1] or member.name
        if not household:
            household = self.env["res.partner"].sudo().create({"name": "%s Household" % last, "is_household": True,
                                                                "company_id": ctx.company.id})
        guardian = self.env["res.partner"].sudo().create({"name": name or "Parent of %s" % member.name, "phone": phone or False,
                                                           "email": email or False, "parent_id": household.id,
                                                           "is_guardian": True, "company_id": ctx.company.id})
        household.write({"primary_guardian_id": guardian.id})
        member.partner_id.write({"parent_id": household.id})

    def _save_emergency(self, ctx, member, name, phone):
        contact = self._emergency_contacts(member).get(member.id)
        if contact:
            if name or phone:
                contact.write({"name": name or contact.name, "phone": phone or contact.phone})
            return
        if not (name or phone):
            return
        if not (name and phone):
            raise SchoolAppProblem("INVALID", message="Add both a name and a phone number for the emergency contact.")
        self.env["dojo.emergency.contact"].sudo().create({"member_id": member.id, "name": name, "phone": phone,
                                                          "relationship": "Emergency contact", "is_primary": True,
                                                          "company_id": ctx.company.id})

    def _save_classes(self, ctx, member, names):
        templates = self._templates(ctx.company)
        wanted = templates.filtered(lambda template: template.name in names)
        if len(set(wanted.mapped("name"))) != len(names):
            raise SchoolAppProblem("UNKNOWN_CLASS", 422)
        for template in templates:
            inside = member in template.course_member_ids
            if template in wanted and not inside:
                template.write({"course_member_ids": [(4, member.id)]})
            elif template not in wanted and inside:
                template.write({"course_member_ids": [(3, member.id)]})

    def save_student(self, ctx, data):
        self._allow(ctx, "students.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        first, last = self._text(data.get("first")), self._text(data.get("last"))
        if not first or not last:
            raise SchoolAppProblem("INVALID", message="Please enter the student's first and last name.")
        email = self._email(data.get("email"))
        parent_email = self._email(data.get("parent_email"))
        born = self._day(ctx.company, data["dob"]) if data.get("dob") else False
        if born and born > self._today(ctx.company):
            raise SchoolAppProblem("INVALID", message="Date of birth can't be in the future.")
        expires = self._day(ctx.company, data["expdate"]) if data.get("expdate") else False
        belt = self._text(data.get("belt"))
        rank = self._rank_named(ctx, belt) if belt else self.env["dojo.belt.rank"]
        classes = self._class_names(data.get("class"))
        partner_values = {"name": "%s %s" % (first, last), "email": email or False,
                          "phone": self._text(data.get("phone"), 40) or False}
        notes = self._long_text(data.get("notes"), 5000)
        partner_values["comment"] = plaintext2html(notes) if notes else False
        warnings = []
        if data.get("id"):
            member = self._member(ctx, data["id"])
            created = False
            if rank and rank != member.current_rank_id:
                if ctx.role not in ("admin",):
                    raise SchoolAppProblem("BELT_ADMIN_ONLY", 403)
                self._award(ctx, member, rank, "Belt set in the student profile by %s" % ctx.user.name)
            member.partner_id.write(partner_values)
            member.write({"date_of_birth": born})
        else:
            partner = self.env["res.partner"].sudo().create({**partner_values, "is_student": True, "company_id": ctx.company.id})
            member = self.env["dojo.member"].sudo().with_company(ctx.company).create({
                "partner_id": partner.id, "company_id": ctx.company.id, "membership_state": "active", "date_of_birth": born})
            created = True
            if rank:
                self._award(ctx, member, rank, "Starting belt set by %s" % ctx.user.name)
        self._save_guardian(ctx, member, self._text(data.get("parent_name")), self._text(data.get("parent_phone"), 40), parent_email)
        self._save_emergency(ctx, member, self._text(data.get("emergency_name")), self._text(data.get("emergency_phone"), 40))
        self._save_classes(ctx, member, classes)
        subscription = self._subscriptions(member).get(member.id)
        if subscription and (subscription.date or False) != expires:
            subscription.write({"date": expires})
        elif not subscription and expires:
            warnings.append("NO_MEMBERSHIP")
        if created:
            self._automation_send(ctx.company, "welcome", member, "welcome")
        member.invalidate_recordset()
        return {"created": created, "student": self._students(ctx, member)[0], "warnings": warnings}

    def archive_student(self, ctx, student_id):
        self._allow(ctx, "students.archive")
        member = self._member(ctx, student_id)
        member.write({"active": False})
        return {"archived": True, "id": str(member.id)}

    def student_photo(self, ctx, student_id):
        self._allow(ctx, "students.read")
        member = self._member(ctx, student_id, archived=True)
        if not member.image_128:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return member.partner_id.image_512 or member.image_128

    def save_student_photo(self, ctx, data):
        self._allow(ctx, "students.write")
        if not isinstance(data, dict) or not isinstance(data.get("image"), str):
            raise SchoolAppProblem("INVALID")
        member = self._member(ctx, data.get("id"))
        match = IMAGE.fullmatch(data["image"].strip())
        if not match:
            raise SchoolAppProblem("BAD_IMAGE", 422)
        try:
            raw = base64.b64decode(match.group(2), validate=False)
        except (binascii.Error, ValueError):
            raise SchoolAppProblem("BAD_IMAGE", 422)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise SchoolAppProblem("BAD_IMAGE", 422)
        try:
            member.partner_id.write({"image_1920": base64.b64encode(raw).decode()})
        except (UserError, ValidationError):
            raise SchoolAppProblem("BAD_IMAGE", 422)
        member.invalidate_recordset()
        return {"photo_url": "/api/admin/students/photo?id=%d&v=%s" % (member.id, self._photo_version(member))}

    def _attendance_row(self, company, log):
        return {
            "date": self._local(company, log.checkin_datetime).date().isoformat(),
            "time": self._clock(company, log.checkin_datetime),
            "class_time": self._clock(company, log.session_id.start_datetime),
            "class": log.session_id.template_id.name or log.session_id.name or "",
            "student_id": str(log.member_id.id),
            "status": log.status,
            "created_at": self._iso(company, log.create_date),
        }

    def student_attendance(self, ctx, student_id, limit=None):
        self._allow(ctx, "attendance.read")
        member = self._member(ctx, student_id, archived=True)
        logs = self.env["dojo.attendance.log"].sudo().search([("member_id", "=", member.id), ("status", "in", ATTENDED)],
                                                             order="checkin_datetime desc, id desc", limit=self._limit(limit, 10, 500))
        return {"attendance": [self._attendance_row(ctx.company, log) for log in logs]}

    def attendance_last(self, ctx):
        self._allow(ctx, "attendance.read")
        members = self._members(ctx).search(self._member_domain(ctx))
        rows = self.env["dojo.attendance.log"].sudo()._read_group(
            [("member_id", "in", members.ids), ("status", "in", ATTENDED)], groupby=["member_id"], aggregates=["checkin_datetime:max"])
        return {"lastCheckins": {str(member.id): self._local(ctx.company, latest).date().isoformat() for member, latest in rows}}

    def attendance_range(self, ctx, since=None, limit=None):
        self._allow(ctx, "attendance.read")
        members = self._members(ctx, archived=True).search(self._member_domain(ctx))
        domain = [("member_id", "in", members.ids), ("status", "in", ATTENDED)]
        if since:
            domain.append(("checkin_datetime", ">=", self._bounds(ctx.company, self._day(ctx.company, since))[0]))
        logs = self.env["dojo.attendance.log"].sudo().search(domain, order="checkin_datetime desc, id desc",
                                                             limit=self._limit(limit, 1000, 5000))
        return {"attendance": [self._attendance_row(ctx.company, log) for log in logs]}

    def checkin_sessions(self, ctx):
        self._allow(ctx, "checkin.read")
        sessions = self._todays_sessions(ctx.company, self._session_domain(ctx))
        return {"sessions": [self._session_payload(session) for session in sessions]}

    def _roster(self, ctx, session):
        enrollments = session.enrollment_ids.filtered(lambda enrollment: enrollment.status == "registered")
        if ctx.grant and ctx.role == "instructor":
            allowed = self._members(ctx).search(self._member_domain(ctx))
            enrollments = enrollments.filtered(lambda enrollment: enrollment.member_id in allowed)
        logs = {log.member_id.id: log for log in self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "in", enrollments.member_id.ids)])}
        students = [{
            "member_id": enrollment.member_id.id,
            "name": enrollment.member_id.name or "",
            "status": logs[enrollment.member_id.id].status if enrollment.member_id.id in logs else enrollment.attendance_state,
            "checked_in": enrollment.member_id.id in logs and logs[enrollment.member_id.id].status in ATTENDED,
        } for enrollment in enrollments]
        students.sort(key=lambda row: row["name"].lower())
        return students

    def checkin_roster(self, ctx, session_id):
        self._allow(ctx, "checkin.read")
        session = self._session(ctx, session_id)
        return {"session": self._session_payload(session), "students": self._roster(ctx, session)}

    def checkin_mark(self, ctx, session_id, student_id):
        self._allow(ctx, "checkin.write")
        session = self._session(ctx, session_id)
        member = self._member(ctx, student_id)
        if session.state not in ("open", "done"):
            raise SchoolAppProblem("SESSION_CLOSED", 409)
        self._lock("school-app:attendance:%s:%s" % (session.id, member.id))
        Enrollment = self.env["dojo.class.enrollment"].sudo()
        enrollment = Enrollment.search([("session_id", "=", session.id), ("member_id", "=", member.id),
                                        ("status", "=", "registered")], limit=1)
        if not enrollment:
            enrollment = Enrollment.create({"session_id": session.id, "member_id": member.id,
                                            "status": "registered", "attendance_state": "pending"})
        Log = self.env["dojo.attendance.log"].sudo().with_company(ctx.company).with_context(dojo_hub_staff_recorded=True)
        log = Log.search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        now = fields.Datetime.now()
        stamp = "Checked in by %s at %s" % (ctx.user.name, self._iso(ctx.company, now))
        if log:
            log.write({"status": "present" if log.status not in ATTENDED else log.status,
                       "note": "\n".join(filter(None, [log.note, stamp]))})
        else:
            Log.create({"session_id": session.id, "member_id": member.id, "enrollment_id": enrollment.id, "status": "present",
                        "checkin_datetime": now if now <= session.end_datetime else session.start_datetime, "note": stamp})
        enrollment.write({"attendance_state": "present"})
        warnings = ["MEMBERSHIP_INACTIVE"] if member.membership_state not in CURRENT else []
        return {"checked_in": True, "warnings": warnings}

    def checkin_undo(self, ctx, session_id, student_id):
        self._allow(ctx, "checkin.write")
        session = self._session(ctx, session_id)
        member = self._member(ctx, student_id)
        if ctx.role != "admin" and self._local(ctx.company, session.start_datetime).date() != self._today(ctx.company):
            raise SchoolAppProblem("UNDO_WINDOW", 403)
        self._lock("school-app:attendance:%s:%s" % (session.id, member.id))
        log = self.env["dojo.attendance.log"].sudo().search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if not log:
            return {"checked_in": False, "undone": False}
        if (self.env["dojo.kiosk.command.receipt"].sudo().search_count([("attendance_id", "=", log.id)])
                or self.env["dojo.kiosk.outbox"].sudo().search_count([("attendance_id", "=", log.id)])):
            raise SchoolAppProblem("UNDO_NEEDS_REVIEW", 409)
        previous = log.status
        log.unlink()
        self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "=", member.id), ("status", "=", "registered")]).write(
            {"attendance_state": "pending"})
        member.with_context(mail_create_nosubscribe=True).message_post(
            body=Markup("Check-in undone for %s (was %s) by %s.") % (
                escape(session.template_id.name or session.name or "class"), escape(previous), escape(ctx.user.name)),
            subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
        return {"checked_in": False, "undone": True}

    def _mail_ready(self):
        if self.env["ir.mail_server"].sudo().search_count([("active", "=", True)]):
            return True
        server = tools.config.get("smtp_server")
        return bool(server) and server != "localhost"

    def _fill(self, text, member):
        first = self._split(member.name)[0] if member else "Student"
        belt = member.current_rank_id.name if member else "White"
        classes = ", ".join(self._classes(member.company_id, member).get(member.id, [])) if member else ""
        return (text or "").replace("{name}", first or "").replace("{belt}", belt or "").replace("{class}", classes)

    def _queue_mail(self, company, to, subject, body, user=None):
        sender = company.email or (user.email if user else "") or self.env.company.email or False
        return self.env["mail.mail"].sudo().create({
            "subject": subject, "body_html": plaintext2html(body), "email_to": to, "email_from": sender,
            "auto_delete": False})

    def _wake_mail_queue(self):
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    def _send_to_members(self, ctx, kind, members, subject, body):
        if not self._mail_ready():
            raise SchoolAppProblem("EMAIL_NOT_SET_UP", 422)
        subject, body = self._text(subject, 200), self._long_text(body)
        if not subject or not body:
            raise SchoolAppProblem("INVALID", message="Please fill in the subject and message.")
        mails = self.env["mail.mail"]
        failed = 0
        for member in members:
            recipient = self._recipient(member)
            if not recipient:
                failed += 1
                continue
            mails |= self._queue_mail(ctx.company, recipient.email, self._fill(subject, member), self._fill(body, member), ctx.user)
        self.env["dojo.school.email.log"].sudo().create({
            "company_id": ctx.company.id, "user_id": ctx.user.id, "kind": kind, "subject": subject, "body": body,
            "member_ids": [(6, 0, members.ids)], "mail_ids": [(6, 0, mails.ids)],
            "recipient_name": members[:1].name if kind == "individual" else False,
            "recipient_count": len(members), "queued": len(mails), "failed": failed})
        self._wake_mail_queue()
        return {"sent": len(mails), "failed": failed}

    def email_mass(self, ctx, data):
        self._allow(ctx, "email")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        ids = self._id_list(data.get("member_ids"), 5000)
        members = self._members(ctx).search(self._member_domain(ctx) + [("id", "in", ids)])
        return self._send_to_members(ctx, "mass", members, data.get("subject"), data.get("body"))

    def email_individual(self, ctx, data):
        self._allow(ctx, "email")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        member = self._member(ctx, data.get("member_id"))
        return self._send_to_members(ctx, "individual", member, data.get("subject"), data.get("body"))

    def email_followup(self, ctx, data):
        self._allow(ctx, "email")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        member = self._member(ctx, data.get("member_id"))
        subject, body = self._text(data.get("subject"), 200), self._long_text(data.get("body"))
        if not subject:
            raise SchoolAppProblem("INVALID")
        self.env["dojo.school.email.log"].sudo().create({
            "company_id": ctx.company.id, "user_id": ctx.user.id, "kind": "followup", "subject": subject, "body": body,
            "member_ids": [(6, 0, member.ids)], "recipient_name": member.name, "recipient_count": 1})
        return {"recorded": True}

    def email_history(self, ctx, limit=None):
        self._allow(ctx, "email")
        rows = self.env["dojo.school.email.log"].sudo().search([("company_id", "=", ctx.company.id)],
                                                              limit=self._limit(limit, 50, 500))
        return {"history": [{
            "id": row.id, "subject": row.subject,
            "type": "individual" if row.kind in ("individual", "followup") else row.kind,
            "kind": row.kind, "recipient_name": row.recipient_name or "", "recipient_count": row.recipient_count,
            "sent": row.queued, "failed": row.failed, "sent_at": self._iso(ctx.company, row.sent_at),
        } for row in rows]}

    def _automation_key(self, company):
        return "dojo_school_app.automations.%d" % company.id

    def _automation_settings(self, company):
        stored = self.env["ir.config_parameter"].sudo().get_str(self._automation_key(company))
        try:
            values = json.loads(stored) if stored else {}
        except ValueError:
            values = {}
        return {key: values.get(key, default) for key, default in AUTOMATION_DEFAULTS.items()}

    def automations(self, ctx):
        self._allow(ctx, "automations")
        return self._automation_settings(ctx.company)

    def save_automations(self, ctx, data):
        self._allow(ctx, "automations")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        settings = self._automation_settings(ctx.company)
        for key, default in AUTOMATION_DEFAULTS.items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise SchoolAppProblem("INVALID")
            elif isinstance(default, int):
                low, high = AUTOMATION_LIMITS[key]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    raise SchoolAppProblem("INVALID", message="%s must be between %d and %d." % (key.replace("_", " "), low, high))
            elif key.endswith("_time"):
                if not isinstance(value, str) or not TIME.fullmatch(value):
                    raise SchoolAppProblem("INVALID")
            else:
                value = self._text(value, 200) if key.endswith("_subject") else self._long_text(value, 5000)
            settings[key] = value
        self.env["ir.config_parameter"].sudo().set_str(self._automation_key(ctx.company), json.dumps(settings, sort_keys=True))
        return settings

    def test_automation(self, ctx, kind):
        self._allow(ctx, "automations")
        if kind not in AUTOMATION_KINDS:
            raise SchoolAppProblem("INVALID")
        if not ctx.user.email:
            raise SchoolAppProblem("NO_EMAIL", 422)
        if not self._mail_ready():
            raise SchoolAppProblem("EMAIL_NOT_SET_UP", 422)
        settings = self._automation_settings(ctx.company)
        sample = self._members(ctx).search(self._member_domain(ctx) + [("membership_state", "in", CURRENT)], limit=1)
        subject = "[Test] " + self._fill(settings["%s_subject" % kind], sample)
        mail = self._queue_mail(ctx.company, ctx.user.email, subject, self._fill(settings["%s_template" % kind], sample), ctx.user)
        self.env["dojo.school.email.log"].sudo().create({
            "company_id": ctx.company.id, "user_id": ctx.user.id, "kind": "test", "subject": subject,
            "mail_ids": [(6, 0, mail.ids)], "recipient_name": ctx.user.name, "recipient_count": 1, "queued": 1})
        self._wake_mail_queue()
        return {"sent": True, "to": ctx.user.email}

    def _automation_send(self, company, kind, member, key):
        settings = self._automation_settings(company)
        if not settings.get("%s_enabled" % kind) or not self._mail_ready():
            return False
        recipient = self._recipient(member)
        if not recipient:
            return False
        Log = self.env["dojo.school.automation.log"].sudo()
        if Log.search_count([("member_id", "=", member.id), ("kind", "=", kind), ("key", "=", key)]):
            return False
        Log.create({"company_id": company.id, "member_id": member.id, "kind": kind, "key": key})
        subject = self._fill(settings["%s_subject" % kind], member)
        mail = self._queue_mail(company, recipient.email, subject, self._fill(settings["%s_template" % kind], member))
        self.env["dojo.school.email.log"].sudo().create({
            "company_id": company.id, "kind": "automation", "subject": subject, "member_ids": [(6, 0, member.ids)],
            "mail_ids": [(6, 0, mail.ids)], "recipient_name": member.name, "recipient_count": 1, "queued": 1})
        return True

    def _cron_automations(self):
        sent = 0
        Member = self.env["dojo.member"].sudo()
        for company in self.env["res.company"].sudo().search([]):
            settings = self._automation_settings(company)
            if not any(settings["%s_enabled" % kind] for kind in AUTOMATION_KINDS) or not self._mail_ready():
                continue
            now = datetime.now(self._tz(company))
            today = now.date()
            current = Member.with_company(company).search([("company_id", "=", company.id), ("membership_state", "in", CURRENT)])
            if settings["bday_enabled"] and now.strftime("%H:%M") >= settings["bday_time"]:
                for member in current.filtered(lambda row: row.date_of_birth and (row.date_of_birth.month, row.date_of_birth.day) == (today.month, today.day)):
                    sent += self._automation_send(company, "bday", member, str(today.year))
            if settings["exp_enabled"]:
                target = today + timedelta(days=settings["exp_days"])
                for subscription in self.env["sale.subscription"].sudo().search([("member_id", "in", current.ids), ("state", "=", "active"), ("date", "=", target)]):
                    sent += self._automation_send(company, "exp", subscription.member_id, "%s:%s" % (subscription.id, subscription.date))
            if settings["absence_enabled"]:
                rows = self.env["dojo.attendance.log"].sudo()._read_group(
                    [("member_id", "in", current.ids), ("status", "in", ATTENDED)], groupby=["member_id"], aggregates=["checkin_datetime:max"])
                cutoff = today - timedelta(days=settings["absence_days"])
                quota = settings["absence_max_per_run"]
                for member, latest in sorted(rows, key=lambda row: row[1]):
                    last = self._local(company, latest).date()
                    if last > cutoff or quota <= 0:
                        continue
                    if self._automation_send(company, "absence", member, last.isoformat()):
                        sent += 1
                        quota -= 1
        if sent:
            self._wake_mail_queue()
        return sent

    def _flagged(self, shift, now):
        return (shift.out_mode in ("auto_check_out", "technical")
                or (not shift.check_out and shift.check_in < now - timedelta(hours=FLAG_HOURS))
                or (shift.check_out and (shift.worked_hours or 0) > FLAG_HOURS))

    def _instructor_profiles(self, ctx):
        domain = [("company_id", "=", ctx.company.id), ("active", "=", True), ("employee_id", "!=", False)]
        if ctx.role == "instructor":
            domain.append(("user_id", "=", ctx.user.id))
        return self.env["dojo.instructor.profile"].sudo().search(domain, order="name asc, id asc")

    def _timesheet_row(self, company, shift, names):
        return {"id": shift.id, "instructor_name": names.get(shift.employee_id.id, shift.employee_id.name or ""),
                "worked_hours": round(shift.worked_hours or 0, 2), "check_in": self._wall(company, shift.check_in),
                "check_out": self._wall(company, shift.check_out), "state": shift.state}

    def timesheets(self, ctx):
        self._allow(ctx, "timesheets.read")
        profiles = self._instructor_profiles(ctx)
        names = {profile.employee_id.id: profile.name or "" for profile in profiles}
        now = fields.Datetime.now()
        shifts = self.env["hr.attendance"].sudo().search([
            ("employee_id", "in", profiles.employee_id.ids), ("check_in", ">=", now - timedelta(days=60)),
            ("source_attendance_id", "=", False), ("state", "!=", "refused")], order="check_in desc, id desc", limit=500)
        return {"timesheets": [self._timesheet_row(ctx.company, shift, names) for shift in shifts if self._flagged(shift, now)]}

    def correct_timesheet(self, ctx, shift_id, data):
        self._allow(ctx, "timesheets.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        profiles = self._instructor_profiles(ctx)
        shift = self.env["hr.attendance"].sudo().with_company(ctx.company).search([
            ("id", "=", self._id(shift_id)), ("employee_id", "in", profiles.employee_id.ids),
            ("source_attendance_id", "=", False)], limit=1)
        if not shift:
            raise SchoolAppProblem("NOT_FOUND", 404)
        self._lock("school-app:employee:%s" % shift.employee_id.id)
        automatic = ctx.company.attendance_validation == "no_validation"
        if shift.state == "refused" or (shift.state == "validated" and not automatic):
            raise SchoolAppProblem("TIMESHEET_REVIEW", 409)
        check_in = self._from_wall(ctx.company, data["check_in"]) if data.get("check_in") else shift.check_in
        check_out = self._from_wall(ctx.company, data["check_out"]) if data.get("check_out") else shift.check_out
        if not check_out or not check_in < check_out <= fields.Datetime.now() or check_out - check_in > timedelta(hours=24):
            raise SchoolAppProblem("INVALID", message="Check-out must be after check-in, within 24 hours, and not in the future.")
        reason = self._text(data.get("reason"), 500) or "Corrected in Timesheets"
        before = (self._wall(ctx.company, shift.check_in), self._wall(ctx.company, shift.check_out) or "open")
        shift.with_context(tracking_disable=True).write({"check_in": check_in, "check_out": check_out, "out_mode": "manual"})
        shift.invalidate_recordset()
        shift.employee_id.message_post(
            body=Markup("Shift corrected by %s: %s – %s became %s – %s. Reason: %s") % (
                escape(ctx.user.name), before[0], before[1], self._wall(ctx.company, shift.check_in),
                self._wall(ctx.company, shift.check_out), escape(reason)),
            subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
        names = {profile.employee_id.id: profile.name or "" for profile in profiles}
        return {"timesheet": self._timesheet_row(ctx.company, shift, names)}

    def _report_window(self, ctx, days=30):
        today = self._today(ctx.company)
        return self._bounds(ctx.company, today - timedelta(days=days - 1))[0], self._bounds(ctx.company, today)[1]

    def attendance_report(self, ctx):
        self._allow(ctx, "reports")
        company = ctx.company
        lo, hi = self._report_window(ctx)
        sessions = self.env["dojo.class.session"].sudo().search(self._session_domain(ctx) + [
            ("start_datetime", ">=", lo), ("start_datetime", "<", hi)])
        logs = self.env["dojo.attendance.log"].sudo().search([("session_id", "in", sessions.ids), ("status", "in", ATTENDED)])
        daily = Counter(self._local(company, log.checkin_datetime).date().isoformat() for log in logs)
        by_class = Counter(log.session_id.template_id.name or log.session_id.name or "Class" for log in logs)
        by_instructor = Counter(log.session_id.instructor_profile_id.name for log in logs if log.session_id.instructor_profile_id)
        peaks = Counter(self._local(company, log.checkin_datetime).hour for log in logs)
        ranked = lambda counter: [{"name": name, "count": count} for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]
        return {
            "daily": [{"date": day, "count": count} for day, count in sorted(daily.items(), reverse=True)],
            "by_class": ranked(by_class),
            "by_instructor": ranked(by_instructor),
            "peak_times": [{"hour": hour, "count": count} for hour, count in sorted(peaks.items())],
        }

    def instructor_workload(self, ctx):
        self._allow(ctx, "reports")
        lo, hi = self._report_window(ctx)
        now = fields.Datetime.now()
        profiles = self._instructor_profiles(ctx)
        sessions = self.env["dojo.class.session"].sudo().search(self._session_domain(ctx) + [
            ("start_datetime", ">=", lo), ("start_datetime", "<", min(hi, now)), ("state", "in", ("open", "done")),
            ("instructor_profile_id", "in", profiles.ids)])
        shifts = self.env["hr.attendance"].sudo().search([("employee_id", "in", profiles.employee_id.ids),
                                                          ("check_in", ">=", lo), ("check_in", "<", hi)])
        taught = Counter(session.instructor_profile_id.id for session in sessions)
        hours = defaultdict(float)
        for shift in shifts:
            hours[shift.employee_id.id] += shift.worked_hours or 0
        rows = [{"id": profile.id, "name": profile.name or "", "hours_worked": round(hours[profile.employee_id.id], 1),
                 "classes_taught": taught.get(profile.id, 0)} for profile in profiles]
        rows.sort(key=lambda row: (-row["classes_taught"], row["name"]))
        return {"instructors": rows}

    def promotion_eligibility(self, ctx):
        self._allow(ctx, "reports")
        members = self._members(ctx).search(self._member_domain(ctx) + [("membership_state", "in", CURRENT)], order="name asc")
        registrations = self.env["dojo.belt.test.registration"].sudo().search([
            ("member_id", "in", members.ids), ("result", "=", "pending"), ("test_id.state", "in", ["scheduled", "in_progress"])])
        tests = {registration.member_id.id: registration.test_id for registration in registrations}
        eligible = []
        for member in members:
            next_rank = member._get_next_belt_rank()
            threshold = next_rank.attendance_threshold if next_rank else 0
            if next_rank and ((threshold and member.attendance_since_last_rank >= threshold) or member.id in tests):
                test = tests.get(member.id)
                eligible.append({"member_id": member.id, "member_name": member.name or "", "current_rank": member.current_rank_id.name or "",
                                 "target_rank": next_rank.name, "attended": member.attendance_since_last_rank, "required": threshold or None,
                                 "test_date": fields.Date.to_string(test.test_date) if test and test.test_date else None})
        return {"eligible": eligible}

    def promote(self, ctx, data):
        self._allow(ctx, "promotions")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        from_belt = self._text(data.get("from_belt"))
        to_rank = self._rank_named(ctx, self._text(data.get("to_belt")))
        if not from_belt or from_belt.lower() == to_rank.name.lower():
            raise SchoolAppProblem("INVALID", message="Choose a different belt to promote to.")
        ids = self._id_list(data.get("member_ids"))
        members = self._members(ctx).search(self._member_domain(ctx) + [("id", "in", ids)], order="name asc")
        promoted, skipped, rows = [], [], self.env["dojo.member.rank"]
        for member in members:
            self._lock("school-app:member:%s" % member.id)
            current = member.current_rank_id.name or ""
            if current.lower() == to_rank.name.lower() or current.lower() != from_belt.lower():
                skipped.append(member.name or "")
                continue
            rows |= self._award(ctx, member, to_rank, "Promoted from %s to %s by %s" % (current, to_rank.name, ctx.user.name))
            promoted.append(member)
            member.with_context(mail_create_nosubscribe=True).message_post(
                body=Markup("Promoted from %s to %s by %s.") % (escape(current), escape(to_rank.name), escape(ctx.user.name)),
                subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
        batch = self.env["dojo.school.promotion.batch"]
        if promoted:
            batch = batch.sudo().create({"company_id": ctx.company.id, "user_id": ctx.user.id, "from_belt": from_belt,
                                         "to_rank_id": to_rank.id, "rank_ids": [(6, 0, rows.ids)],
                                         "student_names": json.dumps([member.name for member in promoted])})
            for member, row in zip(promoted, rows):
                self._automation_send(ctx.company, "milestone", member, "rank:%d" % row.id)
        return {"promoted": len(promoted), "skipped": skipped, "batch_id": batch.id or None}

    def promotion_history(self, ctx, limit=None):
        self._allow(ctx, "promotions")
        batches = self.env["dojo.school.promotion.batch"].sudo().search([("company_id", "=", ctx.company.id)],
                                                                       limit=self._limit(limit, 20, 5000))
        return {"history": [{
            "id": batch.id, "promoted_at": self._iso(ctx.company, batch.promoted_at), "from_belt": batch.from_belt,
            "to_belt": batch.to_rank_id.name, "promoted_count": len(batch.rank_ids),
            "student_names": json.loads(batch.student_names or "[]"), "undone": bool(batch.undone_at),
            "undone_at": self._iso(ctx.company, batch.undone_at),
        } for batch in batches]}

    def promotion_undo(self, ctx, data):
        self._allow(ctx, "promotions")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        batch = self.env["dojo.school.promotion.batch"].sudo().search([
            ("id", "=", self._id(data.get("batch_id"))), ("company_id", "=", ctx.company.id)], limit=1)
        if not batch:
            raise SchoolAppProblem("NOT_FOUND", 404)
        self._lock("school-app:batch:%s" % batch.id)
        if batch.undone_at:
            raise SchoolAppProblem("ALREADY_UNDONE", 409)
        Rank = self.env["dojo.member.rank"].sudo()
        reverted, skipped = 0, []
        for row in batch.rank_ids.filtered("active"):
            member = row.member_id
            self._lock("school-app:member:%s" % member.id)
            if Rank.search([("member_id", "=", member.id)], order="date_awarded desc, id desc", limit=1) != row:
                skipped.append(member.name or "")
                continue
            row.write({"active": False, "reversed_at": fields.Datetime.now(), "reversed_by": ctx.user.id,
                       "reversal_reason": "Promotion batch undone in the school app"})
            member.invalidate_recordset()
            member.with_context(mail_create_nosubscribe=True).message_post(
                body=Markup("Promotion to %s undone by %s.") % (escape(batch.to_rank_id.name), escape(ctx.user.name)),
                subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
            reverted += 1
        batch.write({"undone_at": fields.Datetime.now(), "undone_by": ctx.user.id})
        return {"reverted": reverted, "skipped": skipped}

    def save_test_roster(self, ctx, data):
        self._allow(ctx, "rosters")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        ids = self._id_list(data.get("member_ids"))
        members = self._members(ctx).search(self._member_domain(ctx) + [("id", "in", ids)], order="name asc")
        if not members:
            raise SchoolAppProblem("INVALID")
        roster = self.env["dojo.school.test.roster"].sudo().create({
            "company_id": ctx.company.id, "user_id": ctx.user.id, "roster_date": self._today(ctx.company),
            "belt_filter": self._text(data.get("belt_filter")), "class_filter": self._text(data.get("class_filter")),
            "member_ids": [(6, 0, members.ids)], "student_names": json.dumps(members.mapped("name"))})
        return {"id": roster.id, "student_count": len(members)}

    def test_rosters(self, ctx, limit=None):
        self._allow(ctx, "rosters")
        rosters = self.env["dojo.school.test.roster"].sudo().search([("company_id", "=", ctx.company.id)],
                                                                   limit=self._limit(limit, 20, 5000))
        return {"rosters": [{
            "id": roster.id, "roster_date": fields.Date.to_string(roster.roster_date), "belt_filter": roster.belt_filter or "",
            "class_filter": roster.class_filter or "", "student_count": len(json.loads(roster.student_names or "[]")),
            "student_names": json.loads(roster.student_names or "[]"), "created_at": self._iso(ctx.company, roster.create_date),
        } for roster in rosters]}
