from psycopg2 import errors as pg_errors
from odoo import http
from odoo.http import request
from ..models.service import HubProblem
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import KioskProblem


class HubController(http.Controller):
    @http.route("/companion/hub/action", type="jsonrpc", auth="user", methods=["POST"], max_content_length=16384)
    def action(self, siteId=None, operation=None, payload=None, requestKey=None, **kw):
        if kw or (request.httprequest.content_length or 0) > 16384:
            return {"error": {"code": "INVALID_COMMAND", "status": 400}}
        return request.env["dojo.hub.service"]._dispatch(siteId, operation, payload, requestKey)

    @http.route("/companion/hub/inbound/<int:site_id>", type="http", auth="public", methods=["POST"], csrf=False, save_session=False, max_content_length=16384)
    def inbound(self, site_id):
        headers = {"Cache-Control": "no-store"}
        if (request.httprequest.content_length or 0) > 16384:
            return request.make_json_response({"error": "EVENT_TOO_LARGE"}, status=413, headers=headers)
        # Odoo 20's HTTPRequest facade exposes get_data, not Werkzeug's stream.
        # The route limit bounds the read, including requests without a length.
        body = request.httprequest.get_data(cache=False)
        if len(body) > 16384:
            return request.make_json_response({"error": "EVENT_TOO_LARGE"}, status=413, headers=headers)
        try:
            with request.env.cr.savepoint():
                value = request.env["dojo.hub.service"]._ingest(str(site_id), body,
                    request.httprequest.headers.get("X-Dojang-Timestamp", ""), request.httprequest.headers.get("X-Dojang-Signature", ""))
                return request.make_json_response(value, headers=headers)
        except HubProblem as exc:
            return request.make_json_response({"error": exc.code}, status=exc.status, headers=headers)
        except KioskProblem:
            return request.make_json_response({"error": "INVALID_EVENT"}, status=400, headers=headers)
        except (pg_errors.UniqueViolation, pg_errors.SerializationFailure, pg_errors.LockNotAvailable, pg_errors.DeadlockDetected):
            return request.make_json_response({"error": "RETRY_SAME_EVENT"}, status=409, headers=headers)
