from odoo import http
from odoo.http import request
from ..models.dojo_kiosk_v2 import problem


class CompanionReviewController(http.Controller):
    @http.route("/kiosk/v2/staff/companion-review", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def review(self, token=None, command=None, **kw):
        auth = request.httprequest.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or kw:
            return problem("FORBIDDEN")
        if request.httprequest.content_length and request.httprequest.content_length > 8192:
            return problem("INVALID_COMMAND")
        return request.env["dojo.kiosk.service"]._v2_review_attendance(token, auth[7:], command)
