import json
from pathlib import Path
from urllib.parse import urlsplit

from markupsafe import escape

from odoo import http
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.http import request
from odoo.http.session import authenticate, logout

from ..models.school_app import SchoolAppProblem

APP_FILE = Path(__file__).resolve().parent.parent / "static" / "app" / "school-app.html"
KIOSK_COOKIE = "dojang_kiosk"
KIOSK_COOKIE_AGE = 365 * 24 * 3600
MAX_BODY = 65536
HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "SAMEORIGIN"),
]


class SchoolAppController(http.Controller):

    def _service(self):
        return request.env["dojo.school.app"].sudo()

    def _ctx(self):
        return self._service()._resolve(request.env.user)

    def _json(self, payload, status=200):
        return request.make_json_response(payload, status=status, headers=HEADERS)

    def _respond(self, payload):
        if isinstance(payload, dict) and "_status" in payload:
            status = payload.pop("_status")
            return self._json(payload, status)
        return self._json({"ok": True, **payload})

    def _problem(self, problem):
        return self._json({"ok": False, "code": problem.code, "error": problem.message}, problem.status)

    def _run(self, action, public=False):
        try:
            with request.env.cr.savepoint():
                return self._respond(action())
        except SchoolAppProblem as problem:
            return self._problem(problem)
        except (UserError, ValidationError) as error:
            if public:
                return self._problem(SchoolAppProblem("SEE_FRONT_DESK", 422))
            return self._json({"ok": False, "code": "RULE", "error": str(error.args[0] if error.args else error)}, 422)

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

    def _kiosk(self):
        return self._service()._kiosk(request.httprequest.cookies.get(KIOSK_COOKIE))

    def _html(self, body, status=200):
        return request.make_response(body, status=status, headers=[("Content-Type", "text/html; charset=utf-8"), *HEADERS])

    def _notice(self, title, message, status=200):
        page = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/><title>Dojang</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0b0c;color:#f4f4f5;
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}main{max-width:420px;padding:32px;text-align:center}
h1{font-size:22px;margin:0 0 12px}p{color:#a1a1aa;line-height:1.5;margin:0 0 20px}
a{display:inline-block;padding:12px 22px;border-radius:999px;background:#dc2626;color:#fff;text-decoration:none;font-weight:600}</style>
</head><body><main><h1>%s</h1><p>%s</p><a href="/dojang">Back to Dojang</a></main></body></html>""" % (escape(title), escape(message))
        return self._html(page, status)

    def _app(self, boot):
        if not APP_FILE.is_file():
            if boot["mode"] == "kiosk":
                return self._notice("Kiosk connected", "This kiosk is paired with %s. The check-in screen appears here once the school app is installed." % boot.get("school", ""))
            return self._notice("Signed in", "You're signed in as %s. Your school app appears here once it's installed." % boot.get("role_label", ""))
        html = APP_FILE.read_text(encoding="utf-8")
        script = "<script>window.DOJANG_BOOT=%s;</script>" % json.dumps(boot).replace("</", "<\\/")
        marker = html.lower().find("</head>")
        html = html[:marker] + script + html[marker:] if marker >= 0 else script + html
        return self._html(html)

    @http.route("/dojang", type="http", auth="public", methods=["GET"], sitemap=False)
    def landing(self, **kw):
        ctx = self._ctx()
        return request.redirect(self._service()._home(ctx) if ctx else "/dojang/app")

    @http.route("/dojang/app", type="http", auth="public", methods=["GET"], sitemap=False)
    def app(self, **kw):
        ctx = self._ctx()
        if ctx and ctx.role == "parent":
            return request.redirect(self._service()._home(ctx))
        if not APP_FILE.is_file() and not ctx:
            return request.redirect("/web/login?redirect=/dojang")
        boot = {"mode": "staff", **self._service().me(ctx)}
        return self._app(boot)

    @http.route("/dojang/kiosk/<string:token>", type="http", auth="public", methods=["GET"], sitemap=False)
    def kiosk_pair(self, token, **kw):
        try:
            self._service()._kiosk(token)
        except SchoolAppProblem as problem:
            return self._notice("Kiosk not found", problem.message, 404)
        response = request.redirect("/dojang/kiosk")
        response.set_cookie(KIOSK_COOKIE, token, max_age=KIOSK_COOKIE_AGE, httponly=True, samesite="Strict",
                            secure=request.httprequest.scheme == "https", path="/")
        return response

    @http.route("/dojang/kiosk/start", type="http", auth="public", methods=["GET"], sitemap=False)
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
        response.set_cookie(KIOSK_COOKIE, config.kiosk_token, max_age=KIOSK_COOKIE_AGE, httponly=True, samesite="Strict",
                            secure=request.httprequest.scheme == "https", path="/")
        return response

    @http.route("/dojang/kiosk", type="http", auth="public", methods=["GET"], sitemap=False)
    def kiosk(self, **kw):
        try:
            config = self._kiosk()
        except SchoolAppProblem as problem:
            return self._notice("Kiosk not connected", problem.message, 401)
        return self._app({"mode": "kiosk", "authenticated": False, "school": config.company_id.name or "",
                          "kiosk": config.name or ""})

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
            return self._json({"ok": False, "code": "INVALID", "error": "Enter your email and password."}, 400)
        try:
            authenticate(request.session, request.env, {"login": login.strip(), "password": password, "type": "password"})
        except AccessDenied:
            return self._json({"ok": False, "code": "LOGIN_FAILED", "error": "Wrong email or password."}, 401)
        if not request.session.uid:
            return self._json({"ok": False, "code": "MFA_REQUIRED", "error": "Finish signing in with two-step verification.",
                               "redirect": "/web/login?redirect=/dojang"}, 401)
        ctx = self._ctx()
        if ctx is None:
            logout(request.session, keep_db=True)
            return self._json({"ok": False, "code": "NO_ROLE", "error": "This account doesn't have access to Dojang yet."}, 403)
        return self._json({"ok": True, **self._service().me(ctx)})

    @http.route("/api/auth/logout", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def auth_logout(self, **kw):
        try:
            self._body()
        except SchoolAppProblem as problem:
            return self._problem(problem)
        logout(request.session, keep_db=True)
        return self._json({"ok": True, "home": "/dojang"})

    @http.route("/api/display/sessions", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_sessions(self, **kw):
        return self._run(lambda: self._service().display_sessions(self._kiosk()), public=True)

    @http.route("/api/display/roster", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def display_roster(self, session_id=None, **kw):
        return self._run(lambda: self._service().display_roster(self._kiosk(), session_id), public=True)

    @http.route("/api/display/checkin", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_checkin(self, **kw):
        def action():
            data = self._body()
            return self._service().display_checkin(self._kiosk(), data.get("session_id"),
                                                    data.get("student_id") or data.get("member_id"))
        return self._run(action, public=True)

    @http.route("/api/display/instructor/verify-pin", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_verify_pin(self, **kw):
        def action():
            data = self._body()
            return self._service().display_instructor_verify(self._kiosk(), data.get("pin"), data.get("instructor_id"))
        return self._run(action, public=True)

    @http.route("/api/display/instructor/clock-in", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_clock_in(self, **kw):
        def action():
            data = self._body()
            return self._service().display_instructor_clock(self._kiosk(), data.get("pin"), "in", data.get("instructor_id"))
        return self._run(action, public=True)

    @http.route("/api/display/instructor/clock-out", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def display_clock_out(self, **kw):
        def action():
            data = self._body()
            return self._service().display_instructor_clock(self._kiosk(), data.get("pin"), "out", data.get("instructor_id"))
        return self._run(action, public=True)

    @http.route("/api/admin/dashboard", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_dashboard(self, **kw):
        return self._run(lambda: self._service().dashboard(self._ctx()))

    @http.route("/api/admin/students", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_students(self, include_archived=None, q=None, **kw):
        return self._run(lambda: self._service().students(self._ctx(), include_archived in ("1", "true"), q))

    @http.route("/api/admin/students/save", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_students_save(self, **kw):
        return self._run(lambda: self._service().save_student(self._ctx(), self._body()))

    @http.route("/api/admin/students/delete", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_students_delete(self, **kw):
        def action():
            data = self._body()
            return self._service().archive_student(self._ctx(), data.get("id") or data.get("student_id"))
        return self._run(action)

    @http.route("/api/admin/checkin/sessions", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_checkin_sessions(self, date=None, **kw):
        return self._run(lambda: self._service().checkin_sessions(self._ctx(), date))

    @http.route("/api/admin/checkin/roster", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_checkin_roster(self, session_id=None, **kw):
        return self._run(lambda: self._service().checkin_roster(self._ctx(), session_id))

    @http.route("/api/admin/checkin/mark", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_checkin_mark(self, **kw):
        def action():
            data = self._body()
            return self._service().checkin_mark(self._ctx(), data.get("session_id"),
                                                data.get("student_id") or data.get("member_id"), data.get("status"))
        return self._run(action)

    @http.route("/api/admin/checkin/undo", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def admin_checkin_undo(self, **kw):
        def action():
            data = self._body()
            return self._service().checkin_undo(self._ctx(), data.get("session_id"),
                                                data.get("student_id") or data.get("member_id"))
        return self._run(action)

    @http.route("/api/admin/attendance/student", type="http", auth="public", methods=["GET"], csrf=False, sitemap=False)
    def admin_student_attendance(self, student_id=None, limit=None, **kw):
        return self._run(lambda: self._service().student_attendance(self._ctx(), student_id, limit))
