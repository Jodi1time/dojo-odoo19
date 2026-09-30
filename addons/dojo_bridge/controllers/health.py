"""
health.py
─────────
GET /bridge/v1/health — no auth required.

Used by NestJS health checks, load balancers, and monitoring tools.
Returns basic connectivity and bridge readiness status so the Control Plane
can detect early if the Odoo bridge is misconfigured (e.g. missing JWT secret).
"""
import json
import logging

import odoo
from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class BridgeHealthController(http.Controller):

    @http.route(
        ["/bridge/v1/health", "/bridge/v2/health"],
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def health(self, **kw):
        """
        Liveness + readiness check.

        Returns:
          200 { status: "ok", db: "<name>", bridge_configured: true/false }
          503 if the DB is unreachable
        """
        origin = request.httprequest.headers.get("Origin", "")
        db_name = request.db

        status_payload = {
            "status": "ok",
            "service": "dojo-bridge",
            "version": "v2" if request.httprequest.path.startswith("/bridge/v2/") else "v1",
            "db": db_name,
            "bridge_configured": False,
        }

        if db_name:
            try:
                registry = Registry(db_name)
                with registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    params = env["ir.config_parameter"].sudo()

                    from ..models.bridge_config import BridgeConfig
                    jwt_secret = BridgeConfig.jwt_secret(params)
                    status_payload["bridge_configured"] = bool(jwt_secret)

                    # Verify the bridge identity table exists (module installed)
                    try:
                        env["x.bridge.identity"].search([], limit=0)
                        status_payload["identity_table"] = True
                    except Exception:
                        status_payload["identity_table"] = False

            except Exception as exc:
                _logger.warning("Bridge health: DB check failed: %s", exc)
                status_payload["status"] = "degraded"
                status_payload["db_error"] = str(exc)

        body = json.dumps(status_payload, default=str)
        headers = [
            ("Content-Type", "application/json"),
            ("Cache-Control", "no-store"),
        ]
        if origin:
            headers.append(("Access-Control-Allow-Origin", origin))

        http_status = 200 if status_payload["status"] == "ok" else 503
        return Response(body, status=http_status, headers=headers)
