from odoo import http
from odoo.http import request
from ..models.dojo_kiosk_v2 import problem


class CompanionFollowUpController(http.Controller):
    @http.route("/kiosk/v2/staff/companion-workflow", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def workflow(self, token=None, operation=None, payload=None, **kw):
        auth = request.httprequest.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or kw:
            return problem("FORBIDDEN")
        if request.httprequest.content_length and request.httprequest.content_length > 8192:
            return problem("INVALID_COMMAND")
        return request.env["dojo.kiosk.service"]._companion_dispatch(token, auth[7:], operation, payload)
