from odoo import http
from odoo.http import request
from ..models.dojo_kiosk_v2 import problem

class KioskV2Controller(http.Controller):
    def _call(self, action, token, params):
        header = request.httprequest.headers.get("Authorization", "")
        key = header[7:] if header.startswith("Bearer ") else ""
        return request.env["dojo.kiosk.service"].sudo()._v2_dispatch(action, token, key, params)

    @http.route("/kiosk/v2/sessions", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def sessions(self, token=None, **kw):
        return self._call("sessions", token, kw)

    @http.route("/kiosk/v2/session/roster", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def roster(self, token=None, sessionId=None, **kw):
        return self._call("roster", token, {"sessionId": sessionId})

    @http.route("/kiosk/v2/attendance/check-ins", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def check_in(self, token=None, command=None, **kw):
        if kw:
            return problem("INVALID_COMMAND")
        return self._call("checkin", token, {"command": command})

    @http.route("/kiosk/v2/staff/member", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def member(self, token=None, memberId=None, **kw):
        return self._call("member", token, {"memberId": memberId})

    @http.route("/kiosk/v2/staff/members", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def members(self, token=None, **kw):
        return self._call("members", token, {})

    @http.route("/kiosk/v2/staff/companion", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def companion(self, token=None, **params):
        return self._call("companion", token, params)
