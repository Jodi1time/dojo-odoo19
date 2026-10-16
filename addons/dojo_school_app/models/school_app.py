import hashlib
import hmac
import re
from datetime import date, datetime, time, timedelta

import pytz

from odoo import fields, models

HUB_ROLES = {"owner": "admin", "manager": "admin", "instructor": "instructor", "guardian": "parent", "member": "parent"}
PRIORITY = {"admin": 4, "front_desk": 3, "instructor": 2, "parent": 1}
STAFF = ("admin", "front_desk", "instructor")
LABELS = {"admin": "Owner / Admin", "front_desk": "Front Desk", "instructor": "Instructor", "parent": "Parent"}
PAGES = {
    "admin": ("dashboard", "students", "checkin", "email", "automation", "timesheets", "reports"),
    "front_desk": ("dashboard", "students", "checkin", "email"),
    "instructor": ("checkin", "students", "timesheets"),
    "parent": (),
}
ALLOWED = {
    "catalog": STAFF,
    "students.read": STAFF,
    "students.write": ("admin", "front_desk"),
    "students.archive": ("admin",),
    "students.belt": ("admin",),
    "checkin.read": STAFF,
    "checkin.write": STAFF,
    "attendance.read": STAFF,
    "email": ("admin", "front_desk"),
    "automations": ("admin",),
    "timesheets.read": ("admin", "instructor"),
    "timesheets.write": ("admin",),
    "reports": ("admin",),
    "promotions": ("admin",),
    "rosters": ("admin",),
}
CURRENT = ("active", "trial")
ATTENDED = ("present", "late")
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
    "PIN_INCORRECT": "Incorrect PIN.",
    "PIN_LOCKED": "Too many tries. Please wait and try again.",
    "PIN_SHARED": "This PIN belongs to more than one instructor. Ask an admin to give each instructor their own PIN.",
    "PIN_AGAIN": "Enter your PIN again.",
    "ALREADY_CLOCKED_IN": "You're already clocked in.",
    "NOT_CLOCKED_IN": "You're not clocked in.",
    "TIMESHEET_REVIEW": "This shift was approved, so it can't be changed here.",
    "UNDO_WINDOW": "Only an admin can undo a check-in from another day.",
    "UNDO_NEEDS_REVIEW": "This check-in has a kiosk receipt. Correct it in attendance review.",
    "UNKNOWN_BELT": "That belt isn't set up in Odoo.",
    "UNKNOWN_CLASS": "That class isn't set up in Odoo.",
    "BELT_ADMIN_ONLY": "Only an admin can change a belt. Use Mass Promote.",
    "ALREADY_UNDONE": "This promotion was already undone.",
    "EMAIL_NOT_SET_UP": "Email isn't set up in Odoo yet. Add an outgoing mail server first.",
    "NO_EMAIL": "Add an email address to your Odoo user first.",
    "BAD_IMAGE": "That image couldn't be used. Try a JPG or PNG under 3 MB.",
}


class SchoolAppProblem(Exception):
    def __init__(self, code, status=400, message=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message or MESSAGES.get(code, "Something went wrong.")


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
    return {"_status": status, "detail": MESSAGES[code], "code": code, **extra}


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
            raise SchoolAppProblem("SIGN_IN_REQUIRED", 401)
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

    def _id_list(self, values, limit=500):
        if not isinstance(values, list) or not 0 < len(values) <= limit:
            raise SchoolAppProblem("INVALID")
        return list(dict.fromkeys(self._id(value) for value in values))

    def _limit(self, value, default, ceiling):
        return max(1, min(self._id(value) if value not in (None, "") else default, ceiling))

    def _text(self, value, limit=120):
        if value is None or value is False:
            return ""
        if not isinstance(value, str):
            raise SchoolAppProblem("INVALID")
        value = " ".join(value.split())
        if len(value) > limit:
            raise SchoolAppProblem("INVALID")
        return value

    def _long_text(self, value, limit=10000):
        if value is None or value is False:
            return ""
        if not isinstance(value, str) or len(value) > limit:
            raise SchoolAppProblem("INVALID")
        return value.strip()

    def _email(self, value):
        email = self._text(value, 254)
        if email and not EMAIL.fullmatch(email):
            raise SchoolAppProblem("INVALID", message="That email address doesn't look right.")
        return email

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

    def _wall(self, company, value):
        return self._local(company, value).strftime("%Y-%m-%d %H:%M:%S") if value else None

    def _clock(self, company, value):
        return self._local(company, value).strftime("%-I:%M %p") if value else ""

    def _from_wall(self, company, value):
        if not isinstance(value, str):
            raise SchoolAppProblem("INVALID")
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise SchoolAppProblem("INVALID")
        if moment.tzinfo is None:
            moment = self._tz(company).localize(moment)
        return moment.astimezone(pytz.utc).replace(tzinfo=None)

    def _split(self, name):
        parts = (name or "").split()
        return (parts[0] if parts else "", " ".join(parts[1:]))

    def _short(self, name):
        first, last = self._split(name)
        return ((first + " " + last.split()[-1][0] + ".") if last else first) or "Student"

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

    def _members(self, ctx, archived=False):
        Member = self.env["dojo.member"].sudo().with_company(ctx.company)
        return Member.with_context(active_test=False) if archived else Member

    def _member(self, ctx, value, archived=False):
        member = self._members(ctx, archived).search(self._member_domain(ctx) + [("id", "=", self._id(value))], limit=1)
        if not member:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return member

    def _session(self, ctx, value):
        session = self.env["dojo.class.session"].sudo().with_company(ctx.company).search(
            self._session_domain(ctx) + [("id", "=", self._id(value))], limit=1)
        if not session:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return session

    def _session_payload(self, session):
        template = session.template_id
        program = template.program_id
        return {
            "id": session.id,
            "name": session.name or "",
            "state": session.state,
            "template_name": template.name or session.name or "",
            "program_name": program.name or "",
            "is_trial": bool(program and "is_trial" in program._fields and program.is_trial),
            "start": fields.Datetime.to_string(session.start_datetime),
            "end": fields.Datetime.to_string(session.end_datetime),
            "seats_taken": session.seats_taken,
            "capacity": session.capacity,
            "instructor": session.instructor_profile_id.name or "",
        }

    def _todays_sessions(self, company, domain=None):
        start, end = self._bounds(company, self._today(company))
        return self.env["dojo.class.session"].sudo().with_company(company).search(
            (domain or [("company_id", "=", company.id), ("template_id.active", "=", True)])
            + [("state", "in", ("open", "done")), ("start_datetime", ">=", start), ("start_datetime", "<", end)],
            order="start_datetime asc, id asc")

    def _photo_version(self, record):
        stamp = record.partner_id.write_date if "partner_id" in record._fields else record.write_date
        return hashlib.sha256(str(stamp).encode()).hexdigest()[:8]

    def _kiosk(self, token):
        if not isinstance(token, str) or not 8 <= len(token) <= 128:
            raise SchoolAppProblem("KIOSK_NOT_PAIRED", 401)
        config = self.env["dojo.kiosk.config"].sudo().search([("kiosk_token", "=", token), ("active", "=", True)], limit=1)
        if not config or not config.company_id:
            raise SchoolAppProblem("KIOSK_NOT_PAIRED", 401)
        return config

    def _display_session(self, config, session_id, open_only=False):
        company = config.company_id
        session = self._todays_sessions(company).filtered(lambda row: row.id == self._id(session_id))
        if not session:
            raise SchoolAppProblem("NOT_FOUND", 404)
        if open_only and (session.state != "open" or session.end_datetime <= fields.Datetime.now()):
            raise SchoolAppProblem("SESSION_CLOSED", 409)
        return session

    def display_sessions(self, config):
        company = config.company_id
        return {"school": company.name or "", "kiosk": config.name or "",
                "sessions": [self._session_payload(session) for session in self._todays_sessions(company)]}

    def _roster_members(self, config, session):
        return session.enrollment_ids.filtered(
            lambda enrollment: enrollment.status == "registered" and enrollment.member_id.active
            and enrollment.member_id.company_id == config.company_id).member_id

    def display_roster(self, config, session_id):
        session = self._display_session(config, session_id)
        members = self._roster_members(config, session)
        attended = set(self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("status", "in", ATTENDED)]).member_id.ids)
        students = [{
            "id": member.id,
            "name": self._short(member.name),
            "belt": member.current_rank_id.name or "",
            "photo_url": ("/api/display/photo?id=%d&session_id=%d&v=%s" % (member.id, session.id, self._photo_version(member))
                          if member.image_128 else None),
            "checked_in": member.id in attended,
        } for member in members]
        students.sort(key=lambda row: row["name"].lower())
        return {"session": self._session_payload(session), "students": students}

    def display_photo(self, config, member_id, session_id):
        session = self._display_session(config, session_id)
        member = self._roster_members(config, session).filtered(lambda row: row.id == self._id(member_id))
        if not member or not member.image_128:
            raise SchoolAppProblem("NOT_FOUND", 404)
        return member.image_256 if "image_256" in member._fields else member.image_128

    def display_checkin(self, config, session_id, student_id):
        session = self._display_session(config, session_id, open_only=True)
        company = config.company_id
        member = self.env["dojo.member"].sudo().search([
            ("id", "=", self._id(student_id)), ("company_id", "=", company.id), ("active", "=", True)], limit=1)
        if not member:
            raise SchoolAppProblem("NOT_FOUND", 404)
        self._lock("school-app:attendance:%s:%s" % (session.id, member.id))
        if member not in self._roster_members(config, session):
            raise SchoolAppProblem("NOT_ON_ROSTER", 422)
        title = session.template_id.name or session.name or ""
        first = self._split(member.name)[0] or "Student"
        existing = self.env["dojo.attendance.log"].sudo().search([
            ("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if existing:
            if existing.status not in ATTENDED:
                raise SchoolAppProblem("SEE_FRONT_DESK", 422)
            return {"checked_in": True, "already": True, "name": first, "class": title, "status": existing.status}
        result = self.env["dojo.kiosk.service"].sudo().with_company(company).with_context(
            tz=self._tz(company).zone, allowed_company_ids=[company.id]).checkin_member(member.id, session.id)
        if not result.get("success"):
            raise SchoolAppProblem("SEE_FRONT_DESK", 422)
        log = self.env["dojo.attendance.log"].sudo().browse(result["log_id"])
        return {"checked_in": True, "already": False, "name": first, "class": title, "status": log.status}

    def _profiles_for_kiosk(self, config):
        return self.env["dojo.instructor.profile"].sudo().search([
            ("company_id", "=", config.company_id.id), ("active", "=", True),
            ("employee_id", "!=", False), ("employee_id.active", "=", True)])

    def display_verify_pin(self, config, pin):
        if not isinstance(pin, str) or not PIN.fullmatch(pin.strip()):
            return None, failure("PIN_INCORRECT", 401)
        pin = pin.strip()
        self._lock("school-app:kiosk-pin:%s" % config.id)
        state = config._get_pin_lock_state()
        if state["locked"]:
            return None, failure("PIN_LOCKED", 429, retry_in_minutes=state["retry_in_minutes"])
        matches = self._profiles_for_kiosk(config).filtered(lambda profile: profile.employee_id.pin and hmac.compare_digest(
            profile.employee_id.pin.encode(), pin.encode()))
        if len(matches) == 1:
            config._clear_pin_attempts()
            return matches, {"verified": True}
        if len(matches) > 1:
            return None, failure("PIN_SHARED", 409)
        result = config._register_pin_failure(MAX_PIN_ATTEMPTS, PIN_LOCKOUT_MINUTES)
        if result["locked"]:
            return None, failure("PIN_LOCKED", 429, retry_in_minutes=result["retry_in_minutes"])
        return None, failure("PIN_INCORRECT", 401, remaining_tries=result["remaining_tries"])

    def _verified_profile(self, config, profile_id):
        if not profile_id:
            return self.env["dojo.instructor.profile"]
        return self._profiles_for_kiosk(config).filtered(lambda profile: profile.id == profile_id)

    def _open_shift(self, employee):
        return self.env["hr.attendance"].sudo().search([("employee_id", "=", employee.id), ("check_out", "=", False)], limit=1)

    def display_instructors(self, config, profile_id):
        profile = self._verified_profile(config, profile_id)
        return {"instructors": [{"id": profile.id, "name": profile.name or "", "photo_url": None,
                                 "currently_clocked_in": bool(self._open_shift(profile.employee_id))}] if profile else []}

    def display_clock(self, config, profile_id, instructor_id, direction):
        profile = self._verified_profile(config, profile_id)
        if not profile:
            raise SchoolAppProblem("PIN_AGAIN", 403)
        if instructor_id not in (None, "") and self._id(instructor_id) != profile.id:
            raise SchoolAppProblem("PIN_AGAIN", 403)
        employee = profile.employee_id
        self._lock("school-app:employee:%s" % employee.id)
        Attendance = self.env["hr.attendance"].sudo().with_company(config.company_id)
        current = self._open_shift(employee)
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
        return {"name": profile.name or "", "clocked_in": direction == "in",
                "worked_hours": round(shift.worked_hours or 0, 2)}
