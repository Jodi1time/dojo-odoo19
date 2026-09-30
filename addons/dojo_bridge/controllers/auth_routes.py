"""
auth_routes.py
──────────────
POST /bridge/v1/auth/resolve

Called by the NestJS Control Plane immediately after a user signs in with
Firebase.  NestJS has already verified the Firebase ID token and issued its
own internal HS256 JWT.  This endpoint:

  1. Validates the internal JWT (via @require_bridge_auth).
  2. Resolves or provisions a x.bridge.identity record.
  3. Returns the Odoo identity + member metadata so NestJS can build
     its own session context (member_id, role, membership_state, etc.).

This is the ONLY endpoint that does NOT require a pre-existing identity —
it creates one if needed.  All other bridge endpoints require the identity
to already exist (`require_bridge_auth` will 401 if not found).
"""
import json
import logging

import odoo
from odoo import api, fields, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo import http
from odoo.http import request, Response

from .auth_middleware import bridge_response, bridge_error, _unauthorized, _service_error

try:
    import jwt as _jwt
    _HAS_JWT = True
except ImportError:
    _jwt = None
    _HAS_JWT = False

_logger = logging.getLogger(__name__)


class BridgeAuthController(http.Controller):

    @http.route(
        ["/bridge/v1/auth/resolve", "/bridge/v2/auth/resolve"],
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
    )
    def auth_resolve(self, **kw):
        """
        Accepts: Authorization: Bearer <internal_jwt>
        Returns: { ok: true, data: { identity_id, member_id, member_number, role, ... } }

        Unlike other endpoints, this one provisions the identity if it does
        not yet exist.  The JWT still must be valid and fully verified.
        """
        if request.httprequest.method == "OPTIONS":
            from .auth_middleware import _options_response
            return _options_response()

        if not _HAS_JWT:
            return _service_error("PyJWT is not installed.")

        origin = request.httprequest.headers.get("Origin", "")

        # ── Token extraction ───────────────────────────────────────────────
        auth_header = request.httprequest.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _unauthorized("Missing or malformed Authorization header.")
        raw_token = auth_header[7:].strip()

        # ── Peek at tenant_db (unverified) ─────────────────────────────────
        try:
            unverified = _jwt.decode(
                raw_token,
                options={"verify_signature": False},
                algorithms=["HS256"],
            )
        except _jwt.DecodeError:
            return _unauthorized("Malformed JWT structure.")

        tenant_db = unverified.get("tenant_db") or unverified.get("tenant_id")
        if not tenant_db:
            return _unauthorized("JWT missing 'tenant_db' claim.")

        # ── Open tenant DB ─────────────────────────────────────────────────
        try:
            registry = Registry(tenant_db)
        except Exception:
            from .auth_middleware import _forbidden
            return _forbidden(f"Unknown tenant '{tenant_db}'.")

        try:
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                params = env["ir.config_parameter"].sudo()

                from ..models.bridge_config import BridgeConfig
                jwt_secret = BridgeConfig.jwt_secret(params)
                if not jwt_secret:
                    return _service_error("Bridge not configured: missing x_bridge.jwt_secret.")

                # ── Full JWT verification ──────────────────────────────────
                jwt_issuer = BridgeConfig.jwt_issuer(params)
                jwt_audience = BridgeConfig.jwt_audience(params)
                try:
                    payload = _jwt.decode(
                        raw_token,
                        jwt_secret,
                        algorithms=["HS256"],
                        issuer=jwt_issuer,
                        audience=jwt_audience,
                        options={"require": ["exp", "iat", "iss", "aud"]},
                        leeway=30,
                    )
                except _jwt.ExpiredSignatureError:
                    return _unauthorized("JWT has expired.")
                except _jwt.InvalidTokenError as exc:
                    return _unauthorized(f"JWT validation failed: {exc}")

                # ── Extract + validate claims ──────────────────────────────
                firebase_uid = payload.get("firebase_uid")
                company_id = payload.get("company_id")

                if not firebase_uid:
                    return _unauthorized("JWT missing 'firebase_uid'.")
                if not company_id:
                    return _unauthorized("JWT missing 'company_id'.")
                try:
                    company_id = int(company_id)
                except (TypeError, ValueError):
                    return _unauthorized("'company_id' must be an integer.")

                company = env["res.company"].browse(company_id).exists()
                if not company:
                    from .auth_middleware import _forbidden
                    return _forbidden(f"Company {company_id} not found.")

                # ── Resolve or provision identity ──────────────────────────
                identity = env["x.bridge.identity"].resolve_or_create(
                    firebase_uid=firebase_uid,
                    company_id=company_id,
                    jwt_payload=payload,
                )

                if not identity.is_active:
                    return _unauthorized("This identity has been deactivated.")

                result = identity.to_api_dict()
                cr.commit()

            # Build response (cursor is now closed)
            resp = bridge_response(result)
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Vary"] = "Origin"
            return resp

        except Response:
            raise
        except Exception as exc:
            _logger.exception("Bridge auth/resolve error: %s", exc)
            return _service_error(f"Unexpected error: {exc}")
