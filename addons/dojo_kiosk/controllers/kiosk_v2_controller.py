from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError


class KioskV2Controller(http.Controller):

    def _service(self):
        return request.env["dojo.kiosk.service"].sudo()

    def _invalid_token(self):
        return {"success": False, "code": "INVALID_KIOSK_TOKEN"}

    @http.route(
        "/kiosk/v2/session",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def session_context(self, token=None, session_id=None, **kw):
        if not token or not session_id:
            return {"success": False, "code": "INVALID_COMMAND"}
        try:
            return self._service().get_session_first_context(token, int(session_id))
        except (AccessError, ValueError, TypeError):
            return self._invalid_token()

    @http.route(
        "/kiosk/v2/session/identify",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def identify(self, token=None, session_id=None, query=None, **kw):
        if not token or not session_id:
            return {"success": False, "code": "INVALID_COMMAND", "members": []}
        try:
            return self._service().search_session_roster(
                token, int(session_id), query or ""
            )
        except (AccessError, ValueError, TypeError):
            return self._invalid_token()

    @http.route(
        "/kiosk/v2/attendance/check-ins",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def check_in(
        self,
        token=None,
        session_id=None,
        member_id=None,
        idempotency_key=None,
        correlation_id=None,
        **kw,
    ):
        if not all([
            token,
            session_id,
            member_id,
            idempotency_key,
            correlation_id,
        ]):
            return {"success": False, "code": "INVALID_COMMAND"}

        try:
            return self._service().session_first_checkin(
                token=token,
                session_id=int(session_id),
                member_id=int(member_id),
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        except (AccessError, ValueError, TypeError):
            return self._invalid_token()
