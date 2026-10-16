import hashlib
import hmac
import re
from collections import Counter
from datetime import date, datetime, time, timedelta

import pytz
from markupsafe import Markup, escape

from odoo import fields, models

HUB_ROLES = {"owner": "admin", "manager": "admin", "instructor": "instructor", "guardian": "parent", "member": "parent"}
PRIORITY = {"admin": 4, "front_desk": 3, "instructor": 2, "parent": 1}
STAFF = ("admin", "front_desk", "instructor")
LABELS = {"admin": "Owner / Admin", "front_desk": "Front Desk", "instructor": "Instructor", "parent": "Parent"}
PAGES = {
    "admin": ("dashboard", "students", "checkin", "email", "automation", "timesheets", "reports", "kiosk"),
    "front_desk": ("dashboard", "students", "checkin", "email", "kiosk"),
    "instructor": ("checkin", "students", "timesheets", "kiosk"),
    "parent": (),
}
ALLOWED = {
    "dashboard": ("admin", "front_desk"),
    "students.read": STAFF,
    "students.write": ("admin", "front_desk"),
    "students.archive": ("admin",),
    "students.status": ("admin",),
    "checkin.read": STAFF,
    "checkin.write": STAFF,
    "attendance.read": STAFF,
    "timesheets.read": ("admin", "instructor"),
    "timesheets.write": ("admin",),
    "reports": ("admin",),
    "promotions.read": ("admin",),
    "promotions.write": ("admin",),
}
MEMBERSHIP_STATES = ("lead", "trial", "active", "paused", "cancelled")
CURRENT = ("active", "trial")
ATTENDED = ("present", "late")
MARKABLE = ("present", "late", "absent", "excused")
PIN = re.compile(r"^[0-9]{4,12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_MINUTES = 15
MESSAGES = {
    "SIGN_IN_REQUIRED": "Please sign in.",
    "NOT_ALLOWED": "Your role doesn't include this.",
    "NOT_FOUND": "That record isn't available.",
    "INVALID": "Some details are missing or invalid.",
    "KIOSK_NOT_PAIRED": "This kiosk isn't connected. Open the kiosk link from Odoo on this device.",
    "SESSION_CLOSED": "This class isn't open for check-in.",
    "NOT_ON_ROSTER": "Please see the front desk.",
    "SEE_FRONT_DESK": "Please see the front desk.",
    "PIN_INCORRECT": "That PIN didn't match.",
    "PIN_LOCKED": "Too many tries. Please wait and try again.",
    "PIN_SHARED": "This PIN belongs to more than one instructor. Ask an admin to give each instructor their own PIN.",
    "ALREADY_CLOCKED_IN": "You're already clocked in.",
    "NOT_CLOCKED_IN": "You're not clocked in.",
    "TIMESHEET_REVIEW": "This shift needs an admin to review it.",
    "UNDO_WINDOW": "Only an admin can undo a check-in from another day.",
    "UNDO_NEEDS_REVIEW": "This check-in has a kiosk receipt. Correct it in attendance review.",
    "UNDO_NOT_LATEST": "Only a student's most recent promotion can be undone.",
}


class SchoolAppProblem(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = MESSAGES.get(code, "Something went wrong.")


class SchoolAppContext:
    __slots__ = ("role", "company", "user", "grant")

    def __init__(self, role, company, user, grant=None):
        self.role = role
        self.company = company
        self.user = user
        self.grant = grant

    @property
    def pages(self):
        return PAGES[self.role]


def failure(code, status, **extra):
    return {"_status": status, "ok": False, "code": code, "error": MESSAGES[code], **extra}


class DojoSchoolApp(models.AbstractModel):
    _name = "dojo.school.app"
    _description = "Dojang School App"

    def _resolve(self, user):
        user = user.sudo()
        if not user or not user.active or user._is_public():
            return None
        options = []
        if user.has_group("base.group_system") or user.has_group("dojo_core.group_dojo_admin"):
            options.append(("admin", user.company_id, None))
        if user.has_group("dojo_school_app.group_dojo_front_desk"):
            options.append(("front_desk", user.company_id, None))
        if user.has_group("dojo_core.group_dojo_instructor"):
            options.append(("instructor", user.company_id, None))
        grants = self.env["dojo.hub.grant"].sudo().search([("user_id", "=", user.id), ("active", "=", True)])
        for grant in grants:
            if grant.site_id.active and grant.site_id.company_id and grant.role in HUB_ROLES:
                options.append((HUB_ROLES[grant.role], grant.site_id.company_id, grant))
        if user.has_group("dojo_core.group_dojo_parent_student") or self._family(user.partner_id):
            options.append(("parent", user.company_id, None))
        if not options:
            return None
        role, company, grant = max(options, key=lambda option: (PRIORITY[option[0]], option[2] is None))
        return SchoolAppContext(role, company, user, grant)

    def _family(self, partner):
        if not partner:
            return self.env["dojo.member"]
        return self.env["dojo.member"].sudo().search([
            "|", ("partner_id", "=", partner.id), ("partner_id.parent_id.primary_guardian_id", "=", partner.id)])

    def _home(self, ctx):
        if ctx.role != "parent":
            return "/dojang/app"
        portal = self.env["ir.module.module"].sudo().search_count([
            ("name", "=", "dojo_members_portal"), ("state", "=", "installed")])
        return "/my/dojo" if portal else "/my"

    def me(self, ctx):
        if ctx is None:
            return {"authenticated": False, "home": "/dojang/login"}
        user = ctx.user
        return {
            "authenticated": True,
            "user": {"id": user.id, "name": user.name or "", "login": user.login or "", "email": user.email or ""},
            "role": ctx.role,
            "role_label": LABELS[ctx.role],
            "pages": list(ctx.pages),
            "home": self._home(ctx),
            "school": {"id": ctx.company.id, "name": ctx.company.name or "", "timezone": self._tz(ctx.company).zone},
        }

    def _allow(self, ctx, action):
        if ctx is None:
            raise SchoolAppProblem("SIGN_IN_REQUIRED", 401)
        if ctx.role not in ALLOWED[action]:
            raise SchoolAppProblem("NOT_ALLOWED", 403)

    def _id(self, value):
        if isinstance(value, bool):
            raise SchoolAppProblem("INVALID")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 10:
            number = int(value.strip())
        else:
            raise SchoolAppProblem("INVALID")
        if not 0 < number <= 2147483647:
            raise SchoolAppProblem("INVALID")
        return number

    def _text(self, value, limit=120):
        if value is None or value is False:
            return ""
        if not isinstance(value, str):
            raise SchoolAppProblem("INVALID")
        value = " ".join(value.split())
        if len(value) > limit:
            raise SchoolAppProblem("INVALID")
        return value

    def _tz(self, company):
        try:
            return pytz.timezone(company.partner_id.tz or "UTC")
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _today(self, company):
        return datetime.now(self._tz(company)).date()

    def _day(self, company, value):
        if value in (None, ""):
            return self._today(company)
        if not isinstance(value, str):
            raise SchoolAppProblem("INVALID")
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            raise SchoolAppProblem("INVALID")

    def _bounds(self, company, day):
        tz = self._tz(company)
        start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(datetime.combine(day + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, end

    def _local(self, company, value):
        return pytz.utc.localize(value).astimezone(self._tz(company))

    def _iso(self, company, value):
        return self._local(company, value).isoformat(timespec="seconds") if value else None

    def _split(self, name):
        parts = (name or "").split()
        return (parts[0] if parts else "", " ".join(parts[1:]))

    def _short(self, name):
        first, last = self._split(name)
        return ((first + " " + last.split()[-1][0] + ".") if last else first) or "Student"

    def _initials(self, name):
        first, last = self._split(name)
        return ((first[:1] + (last.split()[-1][:1] if last else "")).upper()) or "?"

    def _age(self, company, born):
        if not born:
            return None
        today = self._today(company)
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    def _next_birthday(self, today, born):
        for year in (today.year, today.year + 1):
            try:
                candidate = born.replace(year=year)
            except ValueError:
                candidate = date(year, 2, 28)
            if candidate >= today:
                return candidate
        return None

    def _lock(self, *labels):
        for label in labels:
            key = int(hashlib.sha256(label.encode()).hexdigest()[:15], 16)
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [key])

    def _member_domain(self, ctx):
        domain = [("company_id", "=", ctx.company.id)]
        if ctx.grant and ctx.role == "instructor":
            booked = self.env["dojo.class.enrollment"].sudo().search([
                ("session_id", "in", ctx.grant.session_ids.ids), ("status", "=", "registered")]).member_id
            domain.append(("id", "in", (ctx.grant.member_ids | booked).ids))
        return domain

    def _session_domain(self, ctx):
        domain = [("company_id", "=", ctx.company.id), ("template_id.active", "=", True)]
        if ctx.grant and ctx.role == "instructor":
            domain.append(("id", "in", ctx.grant.session_ids.ids))
        return domain

    def _member(self, ctx, value, archived=False):
        Member = self.env["dojo.member"].sudo().with_company(ctx.company)
        if archived:
            Member = Member.with_context(active_test=False)
        member = Member.search(self._member_domain(ctx) + [("id", "=", self._id(value))], limit=1)
        if not member:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return member

    def _session(self, ctx, value):
        session = self.env["dojo.class.session"].sudo().with_company(ctx.company).search(
            self._session_domain(ctx) + [("id", "=", self._id(value))], limit=1)
        if not session:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return session

    def _attendance_stats(self, members):
        if not members:
            return {}
        rows = self.env["dojo.attendance.log"].sudo()._read_group(
            [("member_id", "in", members.ids), ("status", "in", ATTENDED)],
            groupby=["member_id"], aggregates=["checkin_datetime:max", "__count"])
        return {member.id: (latest, count) for member, latest, count in rows}

    def _subscriptions(self, members):
        result = {}
        if not members:
            return result
        subscriptions = self.env["sale.subscription"].sudo().search(
            [("member_id", "in", members.ids), ("state", "=", "active")], order="date asc, id asc")
        for subscription in subscriptions:
            result.setdefault(subscription.member_id.id, subscription)
        return result

    def _programs(self, members):
        result = {}
        if not members:
            return result
        rows = self.env["dojo.program.enrollment"].sudo().search(
            [("member_id", "in", members.ids), ("is_active", "=", True)], order="enrolled_date asc, id asc")
        for row in rows:
            if row.program_id.name and row.program_id.name not in result.setdefault(row.member_id.id, []):
                result[row.member_id.id].append(row.program_id.name)
        return result

    def _guardian(self, member):
        household = member.partner_id.parent_id
        guardian = household.primary_guardian_id if household and household.is_household else False
        if not guardian or guardian == member.partner_id:
            return None
        return {"name": guardian.name or "", "phone": guardian.phone or "", "email": guardian.email or ""}

    def _student(self, ctx, member, stats=None, subscription=None, programs=None):
        first, last = self._split(member.name)
        rank = member.current_rank_id
        return {
            "id": member.id,
            "name": member.name or "",
            "first_name": first,
            "last_name": last,
            "initials": self._initials(member.name),
            "member_number": member.member_number or "",
            "email": member.email or "",
            "phone": member.phone or "",
            "date_of_birth": fields.Date.to_string(member.date_of_birth) if member.date_of_birth else None,
            "age": self._age(ctx.company, member.date_of_birth),
            "status": member.membership_state,
            "active": member.active,
            "belt": rank.name or "",
            "belt_color": rank.color or "",
            "stripes": member.current_stripe_count or 0,
            "programs": programs or [],
            "guardian": self._guardian(member),
            "joined_on": fields.Date.to_string(member.create_date.date()) if member.create_date else None,
            "membership_ends_on": fields.Date.to_string(subscription.date) if subscription and subscription.date else None,
            "membership_renews_on": fields.Date.to_string(subscription.recurring_next_date) if subscription and subscription.recurring_next_date else None,
            "attendance_count": stats[1] if stats else 0,
            "last_attended_at": self._iso(ctx.company, stats[0]) if stats else None,
            "has_photo": bool(member.image_128),
        }

    def students(self, ctx, include_archived=False, query=None):
        self._allow(ctx, "students.read")
        Member = self.env["dojo.member"].sudo().with_company(ctx.company)
        domain = self._member_domain(ctx)
        if include_archived and ctx.role == "admin":
            Member = Member.with_context(active_test=False)
        query = self._text(query, 80)
        if query:
            domain += ["|", "|", ("name", "ilike", query), ("member_number", "ilike", query), ("email", "ilike", query)]
        members = Member.search(domain, order="name asc, id asc", limit=5000)
        stats = self._attendance_stats(members)
        subscriptions = self._subscriptions(members)
        programs = self._programs(members)
        return {"students": [self._student(ctx, member, stats.get(member.id), subscriptions.get(member.id), programs.get(member.id))
                             for member in members]}

    def _student_values(self, ctx, data):
        values = {}
        name = self._text(data.get("name")) or " ".join(filter(None, [self._text(data.get("first_name")), self._text(data.get("last_name"))]))
        if name:
            values["name"] = name
        if "email" in data:
            email = self._text(data.get("email"), 254)
            if email and not EMAIL.fullmatch(email):
                raise SchoolAppProblem("INVALID")
            values["email"] = email or False
        if "phone" in data:
            values["phone"] = self._text(data.get("phone"), 40) or False
        if "date_of_birth" in data:
            born = data.get("date_of_birth")
            values["date_of_birth"] = self._day(ctx.company, born) if born else False
            if values["date_of_birth"] and values["date_of_birth"] > self._today(ctx.company):
                raise SchoolAppProblem("INVALID")
        if "status" in data:
            self._allow(ctx, "students.status")
            if data["status"] not in MEMBERSHIP_STATES:
                raise SchoolAppProblem("INVALID")
            values["membership_state"] = data["status"]
        return values

    def save_student(self, ctx, data):
        self._allow(ctx, "students.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        values = self._student_values(ctx, data)
        student_id = data.get("id") or data.get("student_id")
        if student_id:
            member = self._member(ctx, student_id, archived=ctx.role == "admin")
            if values:
                partner_values = {key: values.pop(key) for key in ("name", "email", "phone") if key in values}
                if partner_values:
                    member.partner_id.write(partner_values)
                if values:
                    member.write(values)
            created = False
        else:
            if not values.get("name"):
                raise SchoolAppProblem("INVALID")
            partner = self.env["res.partner"].sudo().create({
                "name": values.pop("name"), "email": values.pop("email", False),
                "phone": values.pop("phone", False), "company_id": ctx.company.id})
            member = self.env["dojo.member"].sudo().with_company(ctx.company).create(
                {**values, "partner_id": partner.id, "company_id": ctx.company.id})
            created = True
        member.invalidate_recordset()
        stats = self._attendance_stats(member)
        return {"created": created, "student": self._student(ctx, member, stats.get(member.id),
                                                              self._subscriptions(member).get(member.id),
                                                              self._programs(member).get(member.id))}

    def archive_student(self, ctx, student_id):
        self._allow(ctx, "students.archive")
        member = self._member(ctx, student_id)
        member.write({"active": False})
        return {"archived": True, "id": member.id}

    def student_attendance(self, ctx, student_id, limit=100):
        self._allow(ctx, "attendance.read")
        member = self._member(ctx, student_id, archived=ctx.role == "admin")
        limit = max(1, min(self._id(limit) if limit else 100, 500))
        Log = self.env["dojo.attendance.log"].sudo()
        logs = Log.search([("member_id", "=", member.id)], order="checkin_datetime desc, id desc", limit=limit)
        return {
            "student": {"id": member.id, "name": member.name or ""},
            "total_attended": Log.search_count([("member_id", "=", member.id), ("status", "in", ATTENDED)]),
            "attendance": [{
                "id": log.id,
                "session_id": log.session_id.id or None,
                "class": log.session_id.template_id.name or log.session_id.name or "",
                "date": self._local(ctx.company, log.checkin_datetime).date().isoformat(),
                "checked_in_at": self._iso(ctx.company, log.checkin_datetime),
                "status": log.status,
            } for log in logs],
        }

    def dashboard(self, ctx):
        self._allow(ctx, "dashboard")
        company = ctx.company
        today = self._today(company)
        start, end = self._bounds(company, today)
        members = self.env["dojo.member"].sudo().with_company(company).search(self._member_domain(ctx))
        current = members.filtered(lambda member: member.membership_state in CURRENT)
        logs = self.env["dojo.attendance.log"].sudo().search([
            ("member_id", "in", members.ids), ("status", "in", ATTENDED),
            ("checkin_datetime", ">=", start), ("checkin_datetime", "<", end)], order="checkin_datetime desc, id desc")
        birthdays = []
        for member in current.filtered("date_of_birth"):
            upcoming = self._next_birthday(today, member.date_of_birth)
            if upcoming and (upcoming - today).days <= 30:
                birthdays.append({"id": member.id, "name": member.name or "", "date": upcoming.isoformat(),
                                  "turning": upcoming.year - member.date_of_birth.year, "in_days": (upcoming - today).days})
        birthdays.sort(key=lambda row: (row["in_days"], row["name"]))
        soon = today + timedelta(days=30)
        expiring = self.env["sale.subscription"].sudo().search([
            ("member_id", "in", current.ids), ("state", "=", "active"),
            ("date", ">=", today), ("date", "<=", soon)], order="date asc, id asc")
        belts = Counter(member.current_rank_id for member in current)
        return {
            "date": today.isoformat(),
            "total_students": len(current),
            "todays_checkins": len(logs),
            "birthdays_this_month": len(current.filtered(lambda member: member.date_of_birth and member.date_of_birth.month == today.month)),
            "expiring_soon": len(expiring),
            "recent_checkins": [{"student_id": log.member_id.id, "name": log.member_id.name or "",
                                 "class": log.session_id.template_id.name or log.session_id.name or "",
                                 "checked_in_at": self._iso(company, log.checkin_datetime), "status": log.status}
                                for log in logs[:10]],
            "upcoming_birthdays": birthdays[:20],
            "expiring": [{"student_id": sub.member_id.id, "name": sub.member_id.name or "",
                          "ends_on": fields.Date.to_string(sub.date), "plan": sub.plan_id.name or ""} for sub in expiring[:20]],
            "belt_distribution": [{"belt": rank.name or "No belt", "color": rank.color or "", "count": count}
                                  for rank, count in sorted(belts.items(), key=lambda item: (item[0].sequence if item[0] else -1, item[0].name or ""))],
        }

    def _session_row(self, company, session):
        registered = session.enrollment_ids.filtered(lambda enrollment: enrollment.status == "registered")
        attended = self.env["dojo.attendance.log"].sudo().search_count([
            ("session_id", "=", session.id), ("status", "in", ATTENDED)])
        return {
            "id": session.id,
            "title": session.template_id.name or session.name or "",
            "program": session.template_id.program_id.name or "",
            "starts_at": self._iso(company, session.start_datetime),
            "ends_at": self._iso(company, session.end_datetime),
            "instructor": session.instructor_profile_id.name or "",
            "capacity": session.capacity,
            "booked": len(registered),
            "checked_in": attended,
            "state": session.state,
        }

    def _roster_rows(self, ctx, session, members=None):
        enrollments = session.enrollment_ids.filtered(lambda enrollment: enrollment.status == "registered")
        if members is not None:
            enrollments = enrollments.filtered(lambda enrollment: enrollment.member_id in members)
        logs = {log.member_id.id: log for log in self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "in", enrollments.member_id.ids)])}
        rows = []
        for enrollment in enrollments:
            member = enrollment.member_id
            log = logs.get(member.id)
            rows.append({
                "student_id": member.id,
                "name": member.name or "",
                "initials": self._initials(member.name),
                "belt": member.current_rank_id.name or "",
                "belt_color": member.current_rank_id.color or "",
                "status": log.status if log else enrollment.attendance_state,
                "checked_in": bool(log and log.status in ATTENDED),
                "checked_in_at": self._iso(ctx.company, log.checkin_datetime) if log and log.status in ATTENDED else None,
            })
        rows.sort(key=lambda row: row["name"].lower())
        return rows

    def checkin_sessions(self, ctx, day=None):
        self._allow(ctx, "checkin.read")
        target = self._day(ctx.company, day)
        start, end = self._bounds(ctx.company, target)
        sessions = self.env["dojo.class.session"].sudo().with_company(ctx.company).search(
            self._session_domain(ctx) + [("state", "in", ("open", "done")),
                                         ("start_datetime", ">=", start), ("start_datetime", "<", end)],
            order="start_datetime asc, id asc")
        return {"date": target.isoformat(), "sessions": [self._session_row(ctx.company, session) for session in sessions]}

    def checkin_roster(self, ctx, session_id):
        self._allow(ctx, "checkin.read")
        session = self._session(ctx, session_id)
        members = None
        if ctx.grant and ctx.role == "instructor":
            members = self.env["dojo.member"].sudo().search(self._member_domain(ctx))
        return {"session": self._session_row(ctx.company, session), "roster": self._roster_rows(ctx, session, members)}

    def _student_row(self, ctx, session, member):
        rows = [row for row in self._roster_rows(ctx, session) if row["student_id"] == member.id]
        return rows[0] if rows else None

    def checkin_mark(self, ctx, session_id, student_id, status=None):
        self._allow(ctx, "checkin.write")
        status = status or "present"
        if status not in MARKABLE:
            raise SchoolAppProblem("INVALID")
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
        stamp = "Marked %s by %s at %s" % (status, ctx.user.name, self._iso(ctx.company, now))
        if log:
            log.write({"status": status, "note": "\n".join(filter(None, [log.note, stamp]))})
        else:
            Log.create({"session_id": session.id, "member_id": member.id, "enrollment_id": enrollment.id,
                        "status": status, "checkin_datetime": now if now <= session.end_datetime else session.start_datetime,
                        "note": stamp})
        enrollment.write({"attendance_state": "present" if status in ATTENDED else status})
        warnings = ["MEMBERSHIP_INACTIVE"] if status in ATTENDED and member.membership_state not in CURRENT else []
        return {"student": self._student_row(ctx, session, member), "warnings": warnings}

    def checkin_undo(self, ctx, session_id, student_id):
        self._allow(ctx, "checkin.write")
        session = self._session(ctx, session_id)
        member = self._member(ctx, student_id)
        if ctx.role != "admin" and self._local(ctx.company, session.start_datetime).date() != self._today(ctx.company):
            raise SchoolAppProblem("UNDO_WINDOW", 403)
        self._lock("school-app:attendance:%s:%s" % (session.id, member.id))
        log = self.env["dojo.attendance.log"].sudo().search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if not log:
            return {"undone": False, "student": self._student_row(ctx, session, member)}
        referenced = (self.env["dojo.kiosk.command.receipt"].sudo().search_count([("attendance_id", "=", log.id)])
                      or self.env["dojo.kiosk.outbox"].sudo().search_count([("attendance_id", "=", log.id)]))
        if referenced:
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
        return {"undone": True, "student": self._student_row(ctx, session, member)}

    def _kiosk(self, token):
        if not isinstance(token, str) or not 8 <= len(token) <= 128:
            raise SchoolAppProblem("KIOSK_NOT_PAIRED", 401)
        config = self.env["dojo.kiosk.config"].sudo().search([("kiosk_token", "=", token), ("active", "=", True)], limit=1)
        if not config or not config.company_id:
            raise SchoolAppProblem("KIOSK_NOT_PAIRED", 401)
        return config

    def _display_session(self, config, session_id):
        company = config.company_id
        start, end = self._bounds(company, self._today(company))
        session = self.env["dojo.class.session"].sudo().search([
            ("id", "=", self._id(session_id)), ("company_id", "=", company.id), ("template_id.active", "=", True),
            ("start_datetime", ">=", start), ("start_datetime", "<", end)], limit=1)
        if not session:
            raise SchoolAppProblem("NOT_FOUND", 404)
        if session.state != "open" or session.end_datetime <= fields.Datetime.now():
            raise SchoolAppProblem("SESSION_CLOSED", 409)
        return session

    def _display_session_row(self, company, session):
        row = self._session_row(company, session)
        return {key: row[key] for key in ("id", "title", "program", "starts_at", "ends_at", "instructor", "booked", "checked_in")}

    def display_sessions(self, config):
        company = config.company_id
        today = self._today(company)
        start, end = self._bounds(company, today)
        sessions = self.env["dojo.class.session"].sudo().search([
            ("company_id", "=", company.id), ("template_id.active", "=", True), ("state", "=", "open"),
            ("start_datetime", ">=", start), ("start_datetime", "<", end),
            ("end_datetime", ">", fields.Datetime.now())], order="start_datetime asc, id asc")
        return {"school": company.name or "", "kiosk": config.name or "", "date": today.isoformat(),
                "sessions": [self._display_session_row(company, session) for session in sessions]}

    def display_roster(self, config, session_id):
        session = self._display_session(config, session_id)
        enrollments = session.enrollment_ids.filtered(
            lambda enrollment: enrollment.status == "registered" and enrollment.member_id.active
            and enrollment.member_id.company_id == config.company_id)
        attended = set(self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("status", "in", ATTENDED)]).member_id.ids)
        roster = [{
            "student_id": enrollment.member_id.id,
            "name": self._short(enrollment.member_id.name),
            "initials": self._initials(enrollment.member_id.name),
            "belt": enrollment.member_id.current_rank_id.name or "",
            "belt_color": enrollment.member_id.current_rank_id.color or "",
            "checked_in": enrollment.member_id.id in attended,
        } for enrollment in enrollments]
        roster.sort(key=lambda row: row["name"].lower())
        return {"session": self._display_session_row(config.company_id, session), "roster": roster}

    def display_checkin(self, config, session_id, student_id):
        session = self._display_session(config, session_id)
        company = config.company_id
        member = self.env["dojo.member"].sudo().search([
            ("id", "=", self._id(student_id)), ("company_id", "=", company.id), ("active", "=", True)], limit=1)
        if not member:
            raise SchoolAppProblem("NOT_FOUND", 404)
        self._lock("school-app:attendance:%s:%s" % (session.id, member.id))
        enrollment = self.env["dojo.class.enrollment"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "=", member.id), ("status", "=", "registered")], limit=1)
        if not enrollment:
            raise SchoolAppProblem("NOT_ON_ROSTER", 422)
        title = session.template_id.name or session.name or ""
        first = self._split(member.name)[0] or "Student"
        existing = self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if existing:
            if existing.status not in ATTENDED:
                raise SchoolAppProblem("SEE_FRONT_DESK", 422)
            return {"checked_in": True, "already": True, "name": first, "class": title,
                    "status": existing.status, "checked_in_at": self._iso(company, existing.checkin_datetime)}
        tz = self._tz(company).zone
        result = self.env["dojo.kiosk.service"].sudo().with_company(company).with_context(
            tz=tz, allowed_company_ids=[company.id]).checkin_member(member.id, session.id)
        if not result.get("success"):
            raise SchoolAppProblem("SEE_FRONT_DESK", 422)
        log = self.env["dojo.attendance.log"].sudo().browse(result["log_id"])
        return {"checked_in": True, "already": False, "name": first, "class": title,
                "status": log.status, "checked_in_at": self._iso(company, log.checkin_datetime)}

    def _instructor_by_pin(self, config, pin, instructor_id=None):
        if not isinstance(pin, str) or not PIN.fullmatch(pin.strip()):
            raise SchoolAppProblem("INVALID")
        pin = pin.strip()
        self._lock("school-app:kiosk-pin:%s" % config.id)
        state = config._get_pin_lock_state()
        if state["locked"]:
            return None, failure("PIN_LOCKED", 429, retry_in_minutes=state["retry_in_minutes"])
        profiles = self.env["dojo.instructor.profile"].sudo().search([
            ("company_id", "=", config.company_id.id), ("active", "=", True),
            ("employee_id", "!=", False), ("employee_id.active", "=", True)])
        if instructor_id not in (None, ""):
            profiles = profiles.filtered(lambda profile: profile.id == self._id(instructor_id))
        matches = profiles.filtered(lambda profile: profile.employee_id.pin and hmac.compare_digest(
            profile.employee_id.pin.encode(), pin.encode()))
        if len(matches) == 1:
            config._clear_pin_attempts()
            return matches, None
        if len(matches) > 1:
            return None, failure("PIN_SHARED", 409)
        result = config._register_pin_failure(MAX_PIN_ATTEMPTS, PIN_LOCKOUT_MINUTES)
        if result["locked"]:
            return None, failure("PIN_LOCKED", 429, retry_in_minutes=result["retry_in_minutes"])
        return None, failure("PIN_INCORRECT", 403, remaining_tries=result["remaining_tries"])

    def _shift(self, company, profile, shift):
        return {
            "instructor": {"id": profile.id, "name": profile.name or ""},
            "clocked_in": bool(shift and not shift.check_out),
            "check_in": self._iso(company, shift.check_in) if shift else None,
            "check_out": self._iso(company, shift.check_out) if shift and shift.check_out else None,
            "hours": round(shift.worked_hours or 0, 2) if shift else 0,
        }

    def display_instructor_verify(self, config, pin, instructor_id=None):
        profile, problem = self._instructor_by_pin(config, pin, instructor_id)
        if problem:
            return problem
        current = self.env["hr.attendance"].sudo().search([
            ("employee_id", "=", profile.employee_id.id), ("check_out", "=", False)], limit=1)
        return {"verified": True, **self._shift(config.company_id, profile, current)}

    def display_instructor_clock(self, config, pin, direction, instructor_id=None):
        if direction not in ("in", "out"):
            raise SchoolAppProblem("INVALID")
        profile, problem = self._instructor_by_pin(config, pin, instructor_id)
        if problem:
            return problem
        employee = profile.employee_id
        self._lock("school-app:employee:%s" % employee.id)
        Attendance = self.env["hr.attendance"].sudo().with_company(config.company_id)
        current = Attendance.search([("employee_id", "=", employee.id), ("check_out", "=", False)], limit=1)
        now = fields.Datetime.now()
        if direction == "in":
            if current:
                raise SchoolAppProblem("ALREADY_CLOCKED_IN", 409)
            shift = Attendance.create({"employee_id": employee.id, "check_in": now, "in_mode": "kiosk"})
        else:
            if not current:
                raise SchoolAppProblem("NOT_CLOCKED_IN", 409)
            if current.state == "refused" or current.source_attendance_id:
                raise SchoolAppProblem("TIMESHEET_REVIEW", 409)
            current.write({"check_out": now, "out_mode": "kiosk"})
            shift = current
        shift.invalidate_recordset()
        return self._shift(config.company_id, profile, shift)

    def _range(self, company, start, end, days):
        last = self._day(company, end) if end else self._today(company)
        first = self._day(company, start) if start else last - timedelta(days=days - 1)
        if first > last or (last - first).days > 366:
            raise SchoolAppProblem("INVALID")
        return first, last, self._bounds(company, first)[0], self._bounds(company, last)[1]

    def _profiles(self, ctx, own=False):
        domain = [("company_id", "=", ctx.company.id), ("active", "=", True), ("employee_id", "!=", False)]
        if own or ctx.role == "instructor":
            domain.append(("user_id", "=", ctx.user.id))
        return self.env["dojo.instructor.profile"].sudo().search(domain, order="name asc, id asc")

    def _shift_row(self, company, shift, names):
        return {
            "id": shift.id,
            "instructor_id": names[shift.employee_id.id][0],
            "instructor": names[shift.employee_id.id][1],
            "check_in": self._iso(company, shift.check_in),
            "check_out": self._iso(company, shift.check_out) if shift.check_out else None,
            "hours": round(shift.worked_hours or 0, 2),
            "needs_review": shift.out_mode in ("auto_check_out", "technical"),
            "correctable": bool(shift.check_out) and shift.state == "draft" and not shift.source_attendance_id,
        }

    def timesheets(self, ctx, start=None, end=None):
        self._allow(ctx, "timesheets.read")
        first, last, lo, hi = self._range(ctx.company, start, end, 14)
        profiles = self._profiles(ctx)
        names = {profile.employee_id.id: (profile.id, profile.name or "") for profile in profiles}
        Attendance = self.env["hr.attendance"].sudo()
        shifts = Attendance.search([("employee_id", "in", profiles.employee_id.ids), ("check_in", ">=", lo),
                                    ("check_in", "<", hi)], order="check_in desc, id desc", limit=1000)
        open_shifts = set(Attendance.search([("employee_id", "in", profiles.employee_id.ids), ("check_out", "=", False)]).employee_id.ids)
        return {
            "from": first.isoformat(), "to": last.isoformat(), "can_correct": ctx.role == "admin",
            "instructors": [{"id": profile.id, "name": profile.name or "", "clocked_in": profile.employee_id.id in open_shifts}
                            for profile in profiles],
            "shifts": [self._shift_row(ctx.company, shift, names) for shift in shifts],
        }

    def _moment(self, company, value):
        if not isinstance(value, str):
            raise SchoolAppProblem("INVALID")
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise SchoolAppProblem("INVALID")
        if moment.tzinfo is None:
            moment = self._tz(company).localize(moment)
        return moment.astimezone(pytz.utc).replace(tzinfo=None)

    def correct_timesheet(self, ctx, data):
        self._allow(ctx, "timesheets.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        reason = self._text(data.get("reason"), 500)
        if not reason:
            raise SchoolAppProblem("INVALID")
        profiles = self._profiles(ctx)
        shift = self.env["hr.attendance"].sudo().with_company(ctx.company).search([
            ("id", "=", self._id(data.get("id") or data.get("shift_id"))),
            ("employee_id", "in", profiles.employee_id.ids), ("source_attendance_id", "=", False)], limit=1)
        if not shift:
            raise SchoolAppProblem("NOT_FOUND", 404)
        self._lock("school-app:employee:%s" % shift.employee_id.id)
        if not shift.check_out or shift.state != "draft":
            raise SchoolAppProblem("TIMESHEET_REVIEW", 409)
        check_in = self._moment(ctx.company, data.get("check_in")) if data.get("check_in") else shift.check_in
        check_out = self._moment(ctx.company, data.get("check_out")) if data.get("check_out") else shift.check_out
        if not check_in < check_out <= fields.Datetime.now() or check_out - check_in > timedelta(hours=24):
            raise SchoolAppProblem("INVALID")
        before = (self._iso(ctx.company, shift.check_in), self._iso(ctx.company, shift.check_out))
        shift.with_context(tracking_disable=True).write({"check_in": check_in, "check_out": check_out, "out_mode": "manual"})
        shift.invalidate_recordset()
        after = (self._iso(ctx.company, shift.check_in), self._iso(ctx.company, shift.check_out))
        shift.employee_id.message_post(
            body=Markup("Shift corrected by %s: %s – %s became %s – %s. Reason: %s") % (
                escape(ctx.user.name), before[0], before[1], after[0], after[1], escape(reason)),
            subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
        names = {profile.employee_id.id: (profile.id, profile.name or "") for profile in profiles}
        return {"shift": self._shift_row(ctx.company, shift, names),
                "before": {"check_in": before[0], "check_out": before[1]}, "reason": reason}

    def attendance_report(self, ctx, start=None, end=None):
        self._allow(ctx, "reports")
        company = ctx.company
        first, last, lo, hi = self._range(company, start, end, 30)
        sessions = self.env["dojo.class.session"].sudo().search(self._session_domain(ctx) + [
            ("state", "in", ("open", "done")), ("start_datetime", ">=", lo), ("start_datetime", "<", hi)])
        logs = self.env["dojo.attendance.log"].sudo().search([("session_id", "in", sessions.ids), ("status", "in", ATTENDED)])
        attended = Counter(log.session_id.id for log in logs)
        daily = Counter(self._local(company, log.checkin_datetime).date().isoformat() for log in logs)
        classes, instructors, peaks = {}, {}, Counter()
        for session in sessions:
            count = attended.get(session.id, 0)
            for bucket, key in ((classes, session.template_id.name or session.name or "Class"),
                                (instructors, session.instructor_profile_id.name or "Unassigned")):
                row = bucket.setdefault(key, {"name": key, "sessions": 0, "attended": 0})
                row["sessions"] += 1
                row["attended"] += count
            local = self._local(company, session.start_datetime)
            peaks[(local.strftime("%A"), local.hour)] += count
        for bucket in (classes, instructors):
            for row in bucket.values():
                row["average"] = round(row["attended"] / row["sessions"], 1) if row["sessions"] else 0
        day = first
        days = []
        while day <= last:
            days.append({"date": day.isoformat(), "attended": daily.get(day.isoformat(), 0)})
            day += timedelta(days=1)
        return {
            "from": first.isoformat(), "to": last.isoformat(),
            "totals": {"sessions": len(sessions), "attended": len(logs), "students": len(logs.member_id)},
            "daily": days,
            "by_class": sorted(classes.values(), key=lambda row: (-row["attended"], row["name"])),
            "by_instructor": sorted(instructors.values(), key=lambda row: (-row["attended"], row["name"])),
            "peak_times": [{"weekday": weekday, "hour": hour, "attended": count}
                           for (weekday, hour), count in peaks.most_common(10) if count],
        }

    def instructor_workload(self, ctx, start=None, end=None):
        self._allow(ctx, "reports")
        company = ctx.company
        first, last, lo, hi = self._range(company, start, end, 30)
        profiles = self._profiles(ctx)
        sessions = self.env["dojo.class.session"].sudo().search(self._session_domain(ctx) + [
            ("state", "in", ("open", "done")), ("start_datetime", ">=", lo), ("start_datetime", "<", hi),
            ("instructor_profile_id", "in", profiles.ids)])
        logs = self.env["dojo.attendance.log"].sudo().search([("session_id", "in", sessions.ids), ("status", "in", ATTENDED)])
        shifts = self.env["hr.attendance"].sudo().search([("employee_id", "in", profiles.employee_id.ids),
                                                          ("check_in", ">=", lo), ("check_in", "<", hi)])
        rows = []
        for profile in profiles:
            taught = sessions.filtered(lambda session: session.instructor_profile_id == profile)
            rows.append({
                "id": profile.id,
                "name": profile.name or "",
                "classes": len(taught),
                "scheduled_hours": round(sum((s.end_datetime - s.start_datetime).total_seconds() for s in taught) / 3600, 2),
                "clocked_hours": round(sum(shifts.filtered(lambda shift: shift.employee_id == profile.employee_id).mapped("worked_hours")), 2),
                "students_taught": len(logs.filtered(lambda log: log.session_id in taught).member_id),
            })
        rows.sort(key=lambda row: (-row["classes"], row["name"]))
        return {"from": first.isoformat(), "to": last.isoformat(), "instructors": rows}

    def _ranks(self, company):
        return self.env["dojo.belt.rank"].sudo().search([("company_id", "in", [False, company.id]), ("active", "=", True)],
                                                        order="sequence asc, id asc", limit=200)

    def promotion_eligibility(self, ctx, query=None):
        self._allow(ctx, "promotions.read")
        domain = self._member_domain(ctx) + [("membership_state", "in", CURRENT)]
        query = self._text(query, 80)
        if query:
            domain.append(("name", "ilike", query))
        members = self.env["dojo.member"].sudo().with_company(ctx.company).search(domain, order="name asc, id asc", limit=1000)
        rows = []
        for member in members:
            next_rank = member._get_next_belt_rank()
            threshold = next_rank.attendance_threshold if next_rank else 0
            attended = member.attendance_since_last_rank
            status = "top_rank" if not next_rank else "no_threshold" if not threshold else "eligible" if attended >= threshold else "not_yet"
            rows.append({"student_id": member.id, "name": member.name or "", "belt": member.current_rank_id.name or "",
                         "belt_color": member.current_rank_id.color or "",
                         "next_belt": next_rank.name if next_rank else None, "next_belt_id": next_rank.id if next_rank else None,
                         "attended": attended, "required": threshold or None, "status": status})
        order = {"eligible": 0, "not_yet": 1, "no_threshold": 2, "top_rank": 3}
        rows.sort(key=lambda row: (order[row["status"]], row["name"].lower()))
        return {"students": rows,
                "ranks": [{"id": rank.id, "name": rank.name, "color": rank.color or "", "sequence": rank.sequence,
                           "required": rank.attendance_threshold or None} for rank in self._ranks(ctx.company)]}

    def promote(self, ctx, data):
        self._allow(ctx, "promotions.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        ids = data.get("student_ids") if data.get("student_ids") is not None else [data.get("student_id")]
        if not isinstance(ids, list) or not 0 < len(ids) <= 200:
            raise SchoolAppProblem("INVALID")
        notes = self._text(data.get("notes"), 500)
        chosen = None
        if data.get("rank_id"):
            if len(ids) != 1:
                raise SchoolAppProblem("INVALID")
            chosen = self._ranks(ctx.company).filtered(lambda rank: rank.id == self._id(data["rank_id"]))
            if not chosen:
                raise SchoolAppProblem("INVALID")
        awarded_by = self.env["dojo.instructor.profile"].sudo().search([
            ("user_id", "=", ctx.user.id), ("company_id", "=", ctx.company.id)], limit=1)
        promoted, skipped = [], []
        for value in ids:
            member = self._member(ctx, value)
            self._lock("school-app:member:%s" % member.id)
            before = member.current_rank_id
            rank = chosen or member._get_next_belt_rank()
            if not rank:
                skipped.append({"student_id": member.id, "name": member.name or "", "reason": "TOP_RANK"})
                continue
            if before and rank.sequence <= before.sequence:
                skipped.append({"student_id": member.id, "name": member.name or "", "reason": "NOT_HIGHER"})
                continue
            row = self.env["dojo.member.rank"].sudo().create({
                "member_id": member.id, "rank_id": rank.id, "date_awarded": self._today(ctx.company),
                "awarded_by": awarded_by.id or False, "notes": notes or "Promoted in the school app by %s" % ctx.user.name,
                "company_id": ctx.company.id})
            member.invalidate_recordset()
            member.with_context(mail_create_nosubscribe=True).message_post(
                body=Markup("Promoted from %s to %s by %s.") % (escape(before.name or "no belt"), escape(rank.name), escape(ctx.user.name)),
                subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
            promoted.append({"student_id": member.id, "name": member.name or "", "from": before.name or None,
                             "to": rank.name, "history_id": row.id})
        return {"promoted": promoted, "skipped": skipped}

    def promotion_history(self, ctx, limit=None, student_id=None):
        self._allow(ctx, "promotions.read")
        limit = max(1, min(self._id(limit) if limit else 100, 500))
        domain = [("member_id.company_id", "=", ctx.company.id)]
        if student_id:
            domain.append(("member_id", "=", self._member(ctx, student_id, archived=True).id))
        Rank = self.env["dojo.member.rank"].sudo()
        rows = Rank.with_context(active_test=False).search(domain, order="date_awarded desc, id desc", limit=limit)
        latest = {}
        for row in Rank.search([("member_id", "in", rows.member_id.ids)], order="date_awarded desc, id desc"):
            latest.setdefault(row.member_id.id, row.id)
        return {"history": [{
            "id": row.id, "student_id": row.member_id.id, "name": row.member_id.name or "",
            "belt": row.rank_id.name or "", "belt_color": row.rank_id.color or "",
            "date": fields.Date.to_string(row.date_awarded), "notes": row.notes or "",
            "source": row.test_registration_id.test_id.name if row.test_registration_id else "Individual award",
            "undone": not row.active, "undone_reason": row.reversal_reason or None,
            "can_undo": row.active and latest.get(row.member_id.id) == row.id,
        } for row in rows]}

    def promotion_undo(self, ctx, data):
        self._allow(ctx, "promotions.write")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        reason = self._text(data.get("reason"), 500) or "Undone in the school app"
        Rank = self.env["dojo.member.rank"].sudo()
        row = Rank.search([("id", "=", self._id(data.get("id") or data.get("history_id"))),
                           ("member_id.company_id", "=", ctx.company.id), ("active", "=", True)], limit=1)
        if not row:
            raise SchoolAppProblem("NOT_FOUND", 404)
        member = self._member(ctx, row.member_id.id)
        self._lock("school-app:member:%s" % member.id)
        if Rank.search([("member_id", "=", member.id)], order="date_awarded desc, id desc", limit=1) != row:
            raise SchoolAppProblem("UNDO_NOT_LATEST", 409)
        before = member.current_rank_id.name or None
        row.write({"active": False, "reversed_at": fields.Datetime.now(), "reversed_by": ctx.user.id, "reversal_reason": reason})
        member.invalidate_recordset()
        member.with_context(mail_create_nosubscribe=True).message_post(
            body=Markup("Promotion to %s undone by %s. Reason: %s") % (escape(row.rank_id.name), escape(ctx.user.name), escape(reason)),
            subtype_xmlid="mail.mt_note", author_id=ctx.user.partner_id.id)
        return {"undone": True, "student_id": member.id, "from": before, "to": member.current_rank_id.name or None}
