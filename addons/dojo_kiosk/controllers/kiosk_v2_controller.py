"""Fixed, credential-gated test integration routes; no generic ORM executor."""
from odoo import http
from odoo.http import request


class KioskV2Controller(http.Controller):
    def _call(self, action, token, params):
        from ..models.dojo_kiosk_v2 import problem
        header = request.httprequest.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return problem("FORBIDDEN")
        if request.httprequest.content_length and request.httprequest.content_length > 8192:
            return problem("INVALID_COMMAND")
        return request.env["dojo.kiosk.service"]._v2_dispatch(action, token, header[7:], params)

    @http.route("/kiosk/v2/sessions", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def sessions(self, token=None, **kw):
        return self._call("sessions", token, {})

    @http.route("/kiosk/v2/session/roster", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def roster(self, token=None, sessionId=None, **kw):
        return self._call("roster", token, {"sessionId": sessionId})

    @http.route("/kiosk/v2/attendance/check-ins", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def check_in(self, token=None, command=None, **kw):
        if kw:
            from ..models.dojo_kiosk_v2 import problem
            return problem("INVALID_COMMAND")
        return self._call("checkin", token, {"command": command})

    @http.route("/kiosk/v2/staff/member", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def member(self, token=None, memberId=None, **kw):
        return self._call("member", token, {"memberId": memberId})

    @http.route("/kiosk/v2/staff/members", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def members(self, token=None, **kw):
        return self._call("members", token, {})
