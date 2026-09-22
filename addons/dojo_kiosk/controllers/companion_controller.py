from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError


class KioskCompanionController(http.Controller):
    """Versionable, token-gated API surface for the kiosk AI companion."""

    def _service(self):
        return request.env["dojo.kiosk.service"].sudo()

    def _validate(self, token):
        if not token:
            return None, {"success": False, "error": "token_required"}
        svc = self._service()
        try:
            svc.validate_token(token)
        except AccessError:
            return None, {"success": False, "error": "invalid_token"}
        return svc, None

    @http.route(
        "/kiosk/companion/context",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_context(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        return svc.get_companion_context(token, member_id=member_id)

    @http.route(
        "/kiosk/companion/household",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_household(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required"}
        return svc.get_household_context(member_id)

    @http.route(
        "/kiosk/companion/membership",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_membership(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required"}
        return svc.get_membership_summary(member_id)

    @http.route(
        "/kiosk/companion/sessions",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_sessions(self, token=None, member_id=None, date=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required", "sessions": []}
        return svc.get_member_session_options(member_id, date=date)

    @http.route(
        "/kiosk/companion/book",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_book(self, token=None, member_id=None, session_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id or not session_id:
            return {"success": False, "error": "member_id_and_session_id_required"}
        return svc.book_member_session(member_id, session_id)

    @http.route(
        "/kiosk/companion/testing",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_testing(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required", "tests": []}
        return svc.get_testing_options(member_id)

    @http.route(
        "/kiosk/companion/cancel-booking",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_cancel_booking(self, token=None, member_id=None, session_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id or not session_id:
            return {"success": False, "error": "member_id_and_session_id_required"}
        return svc.cancel_member_session(member_id, session_id)
