from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError


class KioskCompanionController(http.Controller):
    """Small, versionable API surface for the kiosk AI companion shell."""

    @http.route(
        "/kiosk/companion/context",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_context(self, token=None, member_id=None, **kw):
        if not token:
            return {"error": "token_required"}
        svc = request.env["dojo.kiosk.service"].sudo()
        try:
            return svc.get_companion_context(token, member_id=member_id)
        except AccessError:
            return {"error": "invalid_token"}
