import base64
import binascii
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from markupsafe import escape

from odoo import http
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.http import request
from odoo.http.session import authenticate, logout
from odoo.tools.mimetypes import guess_mimetype
from odoo.tools.misc import file_open, hash_sign, verify_hash_signed

from ..models.school_app import SchoolAppProblem

ADDON = Path(__file__).resolve().parent.parent
BUNDLED_PAGE = ADDON / "static" / "app" / "school-app.html"
ADAPTER = ADDON / "static" / "src" / "adapter.js"
KIOSK_COOKIE = "dojang_kiosk"
KIOSK_COOKIE_AGE = 365 * 24 * 3600
INSTRUCTOR_COOKIE = "dojang_instructor"
INSTRUCTOR_SCOPE = "dojo_school_app.instructor"
INSTRUCTOR_MINUTES = 3
MAX_BODY = 6 * 1024 * 1024
BAKED_TOKEN = re.compile(r"(get\('device'\)\s*\|\|\s*)'[0-9a-fA-F]{16,128}'")
SCHOOL_TZ = re.compile(r"const SCHOOL_TZ = '[^']*';")
HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin"),
    ("X-Frame-Options", "SAMEORIGIN"),
]


class SchoolAppController(http.Controller):

    def _service(self):
        return request.env["dojo.school.app"].sudo()

    def _ctx(self):
        return self._service()._resolve(request.env.user)

    def _json(self, payload, status=200, cookies=()):
        response = request.make_json_response(payload, status=status, headers=HEADERS)
        for cookie in cookies:
            response.set_cookie(**cookie)
        return response

    def _problem(self, problem):
        return self._json({"detail": problem.message, "code": problem.code}, problem.status)

    def _respond(self, payload):
        if isinstance(payload, dict) and "_status" in payload:
            status = payload.pop("_status")
            return self._json(payload, status)
        return self._json(payload)

    def _run(self, action, public=False):
        try:
            with request.env.cr.savepoint():
                return self._respond(action())
        except SchoolAppProblem as problem:
            return self._problem(problem)
        except (UserError, ValidationError) as error:
            if public:
                return self._problem(SchoolAppProblem("SEE_FRONT_DESK", 422))
            return self._json({"detail": str(error.args[0] if error.args else error), "code": "RULE"}, 422)

    def _body(self):
        incoming = request.httprequest
        if incoming.mimetype != "application/json":
            raise SchoolAppProblem("INVALID", 415)
        if (incoming.content_length or 0) > MAX_BODY:
            raise SchoolAppProblem("INVALID", 413)
        origin = incoming.headers.get("Origin")
        if origin and urlsplit(origin).netloc != incoming.host:
            raise SchoolAppProblem("NOT_ALLOWED", 403)
        try:
            data = json.loads(incoming.get_data(as_text=True) or "{}")
        except ValueError:
            raise SchoolAppProblem("INVALID")
        if not isinstance(data, dict):
            raise SchoolAppProblem("INVALID")
        return data

    def _kiosk_token(self):
        return request.httprequest.headers.get("X-Device-Token") or request.httprequest.cookies.get(KIOSK_COOKIE)

    def _kiosk(self):
        return self._service()._kiosk(self._kiosk_token())

    def _paired(self):
        try:
            return self._kiosk()
        except SchoolAppProblem:
            return None

    def _cookie(self, name, value, age):
        return {"key": name, "value": value, "max_age": age, "httponly": True, "samesite": "Strict",
                "secure": request.httprequest.scheme == "https", "path": "/"}

    def _instructor(self, config):
        token = request.httprequest.cookies.get(INSTRUCTOR_COOKIE)
        if not token:
            return None
        try:
            values = verify_hash_signed(request.env(su=True), INSTRUCTOR_SCOPE, token)
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
            return None
        if not isinstance(values, dict) or values.get("kiosk") != config.id:
            return None
        return values.get("profile")

    def _image(self, data, cache):
        raw = data.content if hasattr(data, "content") else base64.b64decode(data)
        return request.make_response(raw, headers=[("Content-Type", guess_mimetype(raw, default="image/png")),
                                                   ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff")])

    def _default_logo(self):
        try:
            with file_open("base/static/img/res_company_logo.png", "rb") as handle:
                return handle.read()
        except (FileNotFoundError, ValueError):
            return b""

    def _html(self, body, status=200):
        return request.make_response(body, status=status, headers=[("Content-Type", "text/html; charset=utf-8"), *HEADERS])

    def _notice(self, title, message, status=200):
        page = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/><title>Dojang</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0c;color:#f0f0f5;
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}main{max-width:420px;padding:32px;text-align:center}
h1{font-size:22px;margin:0 0 12px}p{color:#9999b0;line-height:1.5;margin:0 0 20px}
a{display:inline-block;padding:12px 22px;border-radius:999px;background:#e8192c;color:#fff;text-decoration:none;font-weight:600}</style>
</head><body><main><h1>%s</h1><p>%s</p><a href="/dojang">Back to Dojang</a></main></body></html>""" % (escape(title), escape(message))
        return self._html(page, status)

    def _page_source(self):
        configured = os.environ.get("DOJANG_SCHOOL_APP_HTML") or request.env["ir.config_parameter"].sudo().get_str(
            "dojo_school_app.html_path")
        for candidate in filter(None, [configured, str(BUNDLED_PAGE)]):
            path = Path(candidate)
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return None

    def _page(self, boot):
        html = self._page_source()
        if html is None:
            return self._notice("School app not installed", "Ask your administrator to add the school app page to this Odoo.", 503)
        html = BAKED_TOKEN.sub(r"\1''", html)
        html = SCHOOL_TZ.sub("const SCHOOL_TZ = %s;" % json.dumps(boot["timezone"]), html)
        adapter = ADAPTER.read_text(encoding="utf-8")
        script = "<script>window.DOJANG_BOOT=%s;</script>\n<script>%s</script>\n" % (
            json.dumps(boot).replace("</", "<\\/"), adapter.replace("</", "<\\/"))
        marker = html.lower().rfind("</body>")
        html = html[:marker] + script + html[marker:] if marker >= 0 else html + script
        return self._html(html)

    def _boot(self, config):
        ctx = self._ctx()
        company = (config.company_id if config else (ctx.company if ctx else request.env.company)).sudo()
        return {"kiosk": bool(config), "school": company.name or "",
                "timezone": self._service()._tz(company).zone}

    @http.route("/dojang", type="http", auth="public", methods=["GET"], sitemap=False)
    def landing(self, **kw):
        if self._paired():
            return request.redirect("/dojang/kiosk")
        ctx = self._ctx()
        return request.redirect(self._service()._home(ctx) if ctx else "/dojang/app")

    @http.route("/dojang/app", type="http", auth="public", methods=["GET"], sitemap=False)
    def app(self, **kw):
        ctx = self._ctx()
        if ctx and ctx.role == "parent":
            return request.redirect(self._service()._home(ctx))
        return self._page(self._boot(self._paired()))

    @http.route("/dojang/kiosk", type="http", auth="public", methods=["GET"], sitemap=False)
    def kiosk(self, **kw):
        config = self._paired()
        if not config:
            return self._notice("Kiosk not connected", "Open this device's kiosk link from Odoo once to connect it.", 401)
        return self._page(self._boot(config))

    @http.route("/dojang/kiosk/<string:token>", type="http", auth="public", methods=["GET"], sitemap=False)
    def kiosk_pair(self, token, **kw):
        try:
            self._service()._kiosk(token)
        except SchoolAppProblem as problem:
            return self._notice("Kiosk not found", problem.message, 404)
        response = request.redirect("/dojang/kiosk")
        response.set_cookie(**self._cookie(KIOSK_COOKIE, token, KIOSK_COOKIE_AGE))
        return response

    @http.route("/dojang/kiosk-start", type="http", auth="public", methods=["GET"], sitemap=False)
    def kiosk_start(self, **kw):
        ctx = self._ctx()
        if not ctx or ctx.role not in ("admin", "front_desk", "instructor"):
            return request.redirect("/dojang/app")
        config = request.env["dojo.kiosk.config"].sudo().search([
            ("company_id", "=", ctx.company.id), ("active", "=", True), ("kiosk_token", "!=", False)], order="id asc", limit=1)
        if not config:
            return self._notice("No kiosk set up", "Create a kiosk for this school in Odoo (Kiosk Settings) first.", 404)
        logout(request.session, keep_db=True)
        response = request.redirect("/dojang/kiosk")
        response.set_cookie(**self._cookie(KIOSK_COOKIE, config.kiosk_token, KIOSK_COOKIE_AGE))
        return response

    @http.route("/img/uft-logo-login.png", type="http", auth="public", methods=["GET"], sitemap=False)
    def logo(self, **kw):
        config = self._paired()
        company = config.company_id if config else request.env.company
        logo = company.sudo().logo
        if logo and logo.content != self._default_logo():
            return self._image(logo, "public, max-age=3600")
        initials = "".join(word[0] for word in (company.sudo().name or "Dojang").split()[:3]).upper()
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" rx="60" fill="#111116"/>'
               '<circle cx="60" cy="60" r="56" fill="none" stroke="#c9a84c" stroke-width="4"/>'
               '<text x="60" y="73" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-size="%d" font-weight="700" '
               'fill="#c9a84c">%s</text></svg>') % (40 if len(initials) < 3 else 34, escape(initials))
        return request.make_response(svg, headers=[("Content-Type", "image/svg+xml"), ("Cache-Control", "public, max-age=3600")])

    @http.route("/api/auth/me", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def auth_me(self, **kw):
        return self._run(lambda: self._service().me(self._ctx()))

    @http.route("/api/auth/login", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def auth_login(self, **kw):
        try:
            data = self._body()
        except SchoolAppProblem as problem:
            return self._problem(problem)
        login = data.get("login") or data.get("username") or data.get("email")
        password = data.get("password")
        if not isinstance(login, str) or not isinstance(password, str) or not login.strip() or not password:
            return self._json({"detail": "Enter your email and password.", "code": "INVALID"}, 400)
        try:
            authenticate(request.session, request.env, {"login": login.strip(), "password": password, "type": "password"})
        except AccessDenied:
            return self._json({"detail": "Invalid credentials.", "code": "LOGIN_FAILED"}, 401)
        if not request.session.uid:
            return self._json({"detail": "Finish signing in with two-step verification at /web/login.", "code": "MFA_REQUIRED"}, 401)
        ctx = self._ctx()
        if ctx is None:
            logout(request.session, keep_db=True)
            return self._json({"detail": "This account doesn't have access to Dojang yet.", "code": "NO_ROLE"}, 403)
        return self._json(self._service().me(ctx))

    @http.route("/api/auth/logout", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def auth_logout(self, **kw):
        try:
            self._body()
        except SchoolAppProblem as problem:
            return self._problem(problem)
        logout(request.session, keep_db=True)
        return self._json({"ok": True})

    @http.route("/api/display/sessions", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_sessions(self, **kw):
        return self._run(lambda: self._service().display_sessions(self._kiosk()), public=True)

    @http.route("/api/display/roster", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_roster(self, session_id=None, **kw):
        return self._run(lambda: self._service().display_roster(self._kiosk(), session_id), public=True)

    @http.route("/api/display/photo", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_photo(self, id=None, session_id=None, **kw):
        try:
            image = self._service().display_photo(self._kiosk(), id, session_id)
        except SchoolAppProblem:
            return request.not_found()
        return self._image(image, "private, max-age=600")

    @http.route("/api/display/checkin", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_checkin(self, **kw):
        def action():
            data = self._body()
            return self._service().display_checkin(self._kiosk(), data.get("session_id"), data.get("student_id"))
        return self._run(action, public=True)

    @http.route("/api/display/instructor/verify-pin", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_verify_pin(self, **kw):
        try:
            data = self._body()
            config = self._kiosk()
            with request.env.cr.savepoint():
                profile, payload = self._service().display_verify_pin(config, data.get("pin"))
        except SchoolAppProblem as problem:
            return self._problem(problem)
        if not profile:
            return self._respond(payload)
        token = hash_sign(request.env(su=True), INSTRUCTOR_SCOPE, {"profile": profile.id, "kiosk": config.id},
                          expiration=timedelta(minutes=INSTRUCTOR_MINUTES))
        return self._json(payload, cookies=[self._cookie(INSTRUCTOR_COOKIE, token, INSTRUCTOR_MINUTES * 60)])

    @http.route("/api/display/instructors", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_instructors(self, **kw):
        def action():
            config = self._kiosk()
            return self._service().display_instructors(config, self._instructor(config))
        return self._run(action, public=True)

    def _clock(self, direction):
        def action():
            data = self._body()
            config = self._kiosk()
            return self._service().display_clock(config, self._instructor(config), data.get("instructor_id"), direction)
        response = self._run(action, public=True)
        if response.status_code == 200:
            response.set_cookie(**self._cookie(INSTRUCTOR_COOKIE, "", 0))
        return response

    @http.route("/api/display/instructor/clock-in", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_clock_in(self, **kw):
        return self._clock("in")

    @http.route("/api/display/instructor/clock-out", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_clock_out(self, **kw):
        return self._clock("out")

    @http.route("/api/admin/catalog", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_catalog(self, **kw):
        return self._run(lambda: self._service().catalog(self._ctx()))

    @http.route("/api/admin/students", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_students(self, **kw):
        return self._run(lambda: self._service().students(self._ctx()))

    @http.route("/api/admin/students/save", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_students_save(self, **kw):
        return self._run(lambda: self._service().save_student(self._ctx(), self._body()))

    @http.route("/api/admin/students/delete", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_students_delete(self, **kw):
        return self._run(lambda: self._service().archive_student(self._ctx(), self._body().get("id")))

    @http.route("/api/admin/students/photo", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_student_photo(self, id=None, **kw):
        try:
            image = self._service().student_photo(self._ctx(), id)
        except SchoolAppProblem:
            return request.not_found()
        return self._image(image, "private, max-age=600")

    @http.route("/api/admin/students/photo", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_student_photo_save(self, **kw):
        return self._run(lambda: self._service().save_student_photo(self._ctx(), self._body()))

    @http.route("/api/admin/attendance/student", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_attendance_student(self, student_id=None, limit=None, **kw):
        return self._run(lambda: self._service().student_attendance(self._ctx(), student_id, limit))

    @http.route("/api/admin/attendance/last", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_attendance_last(self, **kw):
        return self._run(lambda: self._service().attendance_last(self._ctx()))

    @http.route("/api/admin/attendance/range", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_attendance_range(self, since=None, limit=None, **kw):
        return self._run(lambda: self._service().attendance_range(self._ctx(), since, limit))

    @http.route("/api/admin/checkin/sessions", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_checkin_sessions(self, **kw):
        return self._run(lambda: self._service().checkin_sessions(self._ctx()))

    @http.route("/api/admin/checkin/roster", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_checkin_roster(self, session_id=None, **kw):
        return self._run(lambda: self._service().checkin_roster(self._ctx(), session_id))

    @http.route("/api/admin/checkin/mark", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_checkin_mark(self, **kw):
        def action():
            data = self._body()
            return self._service().checkin_mark(self._ctx(), data.get("session_id"), data.get("student_id"))
        return self._run(action)

    @http.route("/api/admin/checkin/undo", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_checkin_undo(self, **kw):
        def action():
            data = self._body()
            return self._service().checkin_undo(self._ctx(), data.get("session_id"), data.get("student_id"))
        return self._run(action)

    @http.route("/api/admin/email/mass", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_email_mass(self, **kw):
        return self._run(lambda: self._service().email_mass(self._ctx(), self._body()))

    @http.route("/api/admin/email/individual", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_email_individual(self, **kw):
        return self._run(lambda: self._service().email_individual(self._ctx(), self._body()))

    @http.route("/api/admin/email/followup", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_email_followup(self, **kw):
        return self._run(lambda: self._service().email_followup(self._ctx(), self._body()))

    @http.route("/api/admin/email/history", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_email_history(self, limit=None, **kw):
        return self._run(lambda: self._service().email_history(self._ctx(), limit))

    @http.route("/api/admin/automations", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_automations(self, **kw):
        return self._run(lambda: self._service().automations(self._ctx()))

    @http.route("/api/admin/automations", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_automations_save(self, **kw):
        return self._run(lambda: self._service().save_automations(self._ctx(), self._body()))

    @http.route("/api/admin/automations/test", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_automations_test(self, kind=None, **kw):
        def action():
            self._body()
            return self._service().test_automation(self._ctx(), kind)
        return self._run(action)

    @http.route("/api/admin/instructor-timesheets", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_timesheets(self, **kw):
        return self._run(lambda: self._service().timesheets(self._ctx()))

    @http.route("/api/admin/instructor-timesheets/<int:shift_id>", type="http", auth="public", methods=["PATCH", "POST"],
                csrf=False, sitemap=False)
    def admin_timesheet_correct(self, shift_id, **kw):
        return self._run(lambda: self._service().correct_timesheet(self._ctx(), shift_id, self._body()))

    @http.route("/api/admin/reports/attendance", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_report_attendance(self, **kw):
        return self._run(lambda: self._service().attendance_report(self._ctx()))

    @http.route("/api/admin/reports/instructor-workload", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_report_workload(self, **kw):
        return self._run(lambda: self._service().instructor_workload(self._ctx()))

    @http.route("/api/admin/reports/promotion-eligibility", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_report_eligibility(self, **kw):
        return self._run(lambda: self._service().promotion_eligibility(self._ctx()))

    @http.route("/api/admin/promotions/promote", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_promote(self, **kw):
        return self._run(lambda: self._service().promote(self._ctx(), self._body()))

    @http.route("/api/admin/promotions/history", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_promotion_history(self, limit=None, **kw):
        return self._run(lambda: self._service().promotion_history(self._ctx(), limit))

    @http.route("/api/admin/promotions/undo", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_promotion_undo(self, **kw):
        return self._run(lambda: self._service().promotion_undo(self._ctx(), self._body()))

    @http.route("/api/admin/test-rosters", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_test_rosters(self, limit=None, **kw):
        return self._run(lambda: self._service().test_rosters(self._ctx(), limit))

    @http.route("/api/admin/test-rosters", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_test_rosters_save(self, **kw):
        return self._run(lambda: self._service().save_test_roster(self._ctx(), self._body()))
