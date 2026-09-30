"""
auth_middleware.py
──────────────────
@require_bridge_auth — decorator for all authenticated bridge controllers.

Flow per request
────────────────
1.  Extract `Authorization: Bearer <token>` from headers.
2.  Decode the JWT *without* verification just to read the `tenant_db` claim.
3.  Open `odoo.registry(tenant_db)` to get the tenant-specific DB cursor.
4.  Load `x_bridge.jwt_secret` from `ir.config_parameter` inside that DB.
5.  Fully verify the JWT (HS256, exp, iss, aud, required claims).
6.  Assert `firebase_uid` + `company_id` claims are present.
7.  Look up `x.bridge.identity`; reject if inactive or missing.
8.  Stamp `last_seen`.
9.  Inject keyword args into the decorated function:
      b_env        – api.Environment(cr, SUPERUSER_ID, {}) in the tenant DB
      b_identity   – x.bridge.identity record
      b_member     – identity.member_id  (may be empty() before provisioning)
      b_company_id – int
      b_payload    – full decoded JWT dict
10. Call the decorated function (still inside the open cursor context).
11. Commit on success, rollback on exception.

Conventions for decorated controllers
───────────────────────────────────────
- Controllers should NOT commit the cursor themselves for simple reads.
- For mutations, call  b_env.cr.commit()  inside the controller (or let the
  decorator commit after the function returns — it will commit once on exit).
- Never use `request.env` inside a bridge controller; always use `b_env`.
"""
import functools
import json
import logging
import uuid

import odoo
from odoo import api, fields, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.http import request, Response

try:
    import jwt as _jwt  # PyJWT
    _HAS_JWT = True
except ImportError:
    _jwt = None
    _HAS_JWT = False

_logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _request_api_version() -> str:
    """Infer bridge API version from the current request path."""
    try:
        path = request.httprequest.path or ""
    except Exception:
        path = ""
    return "v2" if path.startswith("/bridge/v2/") else "v1"


def _request_id() -> str:
    """Use caller-provided request id when present, otherwise mint one."""
    try:
        supplied = (request.httprequest.headers.get("X-Request-Id") or "").strip()
    except Exception:
        supplied = ""
    return supplied[:128] if supplied else uuid.uuid4().hex


def _json_response(data: dict, status: int = 200, api_version: str | None = None) -> Response:
    """Build a plain JSON HTTP response with stable version/request metadata."""
    version = api_version or _request_api_version()
    request_id = None
    if version == "v2":
        request_id = (
            data.get("meta", {}).get("request_id")
            if isinstance(data, dict)
            else None
        ) or _request_id()
        if isinstance(data, dict):
            data.setdefault("meta", {})
            data["meta"].setdefault("api_version", "v2")
            data["meta"].setdefault("request_id", request_id)

    body = json.dumps(data, default=str)
    headers = [
        ("Content-Type", "application/json"),
        ("X-Bridge-Version", "2" if version == "v2" else "1"),
    ]
    if request_id:
        headers.append(("X-Request-Id", request_id))
    return Response(body, status=status, headers=headers)


def _v2_error_payload(code: str, message: str, details=None) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
        "meta": {
            "api_version": "v2",
            "request_id": _request_id(),
        },
    }


def _unauthorized(reason: str) -> Response:
    _logger.warning("Bridge auth rejected: %s", reason)
    if _request_api_version() == "v2":
        return _json_response(
            _v2_error_payload("unauthorized", reason),
            status=401,
            api_version="v2",
        )
    return _json_response({"error": "Unauthorized", "reason": reason}, status=401)


def _forbidden(reason: str) -> Response:
    _logger.warning("Bridge forbidden: %s", reason)
    if _request_api_version() == "v2":
        return _json_response(
            _v2_error_payload("forbidden", reason),
            status=403,
            api_version="v2",
        )
    return _json_response({"error": "Forbidden", "reason": reason}, status=403)


def _service_error(reason: str) -> Response:
    _logger.error("Bridge internal error: %s", reason)
    if _request_api_version() == "v2":
        return _json_response(
            _v2_error_payload("internal_error", reason),
            status=500,
            api_version="v2",
        )
    return _json_response({"error": "Internal Error", "reason": reason}, status=500)


_CORS_METHODS = "GET, POST, DELETE, OPTIONS"
_CORS_HEADERS_ALLOWED = "Authorization, Content-Type, X-Bridge-Signature, X-Requested-With, X-Request-Id, X-Idempotency-Key"
_CORS_MAX_AGE = "86400"


def _options_response() -> Response:
    """Return a proper CORS preflight 204 for OPTIONS requests."""
    origin = request.httprequest.headers.get("Origin", "*")
    # Load allowed origins from config if possible
    allowed_origin = origin  # reflect by default (dev convenience)
    try:
        db_name = request.db
        if db_name:
            registry = Registry(db_name)
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                from ..models.bridge_config import BridgeConfig
                params = env["ir.config_parameter"].sudo()
                allowed = BridgeConfig.cors_origins(params)
                if allowed and origin not in allowed:
                    allowed_origin = allowed[0]
    except Exception:
        pass
    return Response(
        "",
        status=204,
        headers=[
            ("Access-Control-Allow-Origin", allowed_origin),
            ("Access-Control-Allow-Methods", _CORS_METHODS),
            ("Access-Control-Allow-Headers", _CORS_HEADERS_ALLOWED),
            ("Access-Control-Allow-Credentials", "true"),
            ("Access-Control-Max-Age", _CORS_MAX_AGE),
            ("Vary", "Origin"),
        ],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core decorator
# ──────────────────────────────────────────────────────────────────────────────

def require_bridge_auth(fn):
    """
    Decorator.  Validates the Bearer JWT, performs tenant DB routing,
    resolves the identity, and injects bridge context into the handler.
    """
    @functools.wraps(fn)
    def _wrapper(controller_self, *args, **kwargs):
        # ── Short-circuit CORS preflight ───────────────────────────────────
        if request.httprequest.method == "OPTIONS":
            return _options_response()

        if not _HAS_JWT:
            return _service_error(
                "PyJWT is not installed. Add 'PyJWT' to requirements.txt."
            )

        # ── 1. Extract bearer token ────────────────────────────────────────
        auth_header = request.httprequest.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _unauthorized("Missing or malformed Authorization header.")
        raw_token = auth_header[7:].strip()
        if not raw_token:
            return _unauthorized("Empty bearer token.")

        # ── 2. Peek at tenant_db claim (unverified) ────────────────────────
        try:
            unverified = _jwt.decode(
                raw_token,
                options={"verify_signature": False},
                algorithms=["HS256"],
            )
        except _jwt.DecodeError:
            return _unauthorized("Malformed JWT structure.")

        tenant_db = (
            unverified.get("tenant_db")
            or unverified.get("tenant_id")
        )

        if not tenant_db:
            return _unauthorized("JWT is missing 'tenant_db' claim.")

        # ── 3. Resolve tenant DB via tenant_map (single-DB fallback) ──────
        # For the single-DB case we use the current request DB directly.
        # `request.db` is populated by Odoo's dispatcher before `auth="public"`
        # controllers run if a `db` query param or dbfilter matches.
        # We also support explicit tenant override via the JWT.
        try:
            registry = Registry(tenant_db)
        except KeyError:
            return _forbidden(f"Unknown tenant '{tenant_db}'.")
        except Exception:
            return _forbidden(f"Tenant '{tenant_db}' could not be resolved.")

        # ── 4-11. Open cursor, verify JWT, resolve identity ────────────────
        try:
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                params = env["ir.config_parameter"].sudo()

                # 4. Load per-DB JWT secret
                from ..models.bridge_config import BridgeConfig
                jwt_secret = BridgeConfig.jwt_secret(params)
                if not jwt_secret:
                    return _service_error(
                        "Bridge is not configured: missing x_bridge.jwt_secret. "
                        "Set it in Settings → Technical → System Parameters."
                    )

                # 5. Full JWT verification
                jwt_issuer = BridgeConfig.jwt_issuer(params)
                jwt_audience = BridgeConfig.jwt_audience(params)
                try:
                    payload = _jwt.decode(
                        raw_token,
                        jwt_secret,
                        algorithms=["HS256"],
                        issuer=jwt_issuer,
                        audience=jwt_audience,
                        options={
                            "require": ["exp", "iat", "iss", "aud"],
                        },
                        leeway=30,  # 30-second clock-skew tolerance
                    )
                except _jwt.ExpiredSignatureError:
                    return _unauthorized("JWT has expired.")
                except _jwt.InvalidIssuerError:
                    return _unauthorized("JWT issuer is invalid.")
                except _jwt.InvalidAudienceError:
                    return _unauthorized("JWT audience is invalid.")
                except _jwt.MissingRequiredClaimError as exc:
                    return _unauthorized(f"JWT missing required claim: {exc}")
                except _jwt.InvalidTokenError as exc:
                    return _unauthorized(f"JWT validation failed: {exc}")

                # 6. Extract business claims
                firebase_uid = payload.get("firebase_uid")
                company_id = payload.get("company_id")

                if not firebase_uid:
                    return _unauthorized("JWT missing 'firebase_uid' claim.")
                if not company_id:
                    return _unauthorized("JWT missing 'company_id' claim.")

                try:
                    company_id = int(company_id)
                except (TypeError, ValueError):
                    return _unauthorized("JWT 'company_id' must be an integer.")

                # Verify the company actually exists in this DB
                company = env["res.company"].browse(company_id).exists()
                if not company:
                    return _forbidden(
                        f"Company {company_id} not found in tenant '{tenant_db}'."
                    )

                # 7. Resolve identity
                identity = env["x.bridge.identity"].search(
                    [
                        ("firebase_uid", "=", firebase_uid),
                        ("company_id", "=", company_id),
                        ("is_active", "=", True),
                    ],
                    limit=1,
                )
                if not identity:
                    return _unauthorized(
                        "No active bridge identity found for this user. "
                        + (
                            "Call /bridge/v2/auth/resolve first."
                            if _request_api_version() == "v2"
                            else "Call /bridge/v1/auth/resolve first."
                        )
                    )

                # 8. Stamp last_seen (lightweight write)
                identity.write({"last_seen": fields.Datetime.now()})

                # 9. Inject context kwargs
                kwargs["b_env"] = env
                kwargs["b_identity"] = identity
                kwargs["b_member"] = identity.member_id
                kwargs["b_company_id"] = company_id
                kwargs["b_payload"] = payload

                # 10. Call the controller
                try:
                    result = fn(controller_self, *args, **kwargs)
                    # 11. Commit on success
                    cr.commit()
                    return result
                except Exception:
                    cr.rollback()
                    raise

        except Response:
            # A Response exception (unusual but defensively handled)
            raise
        except Exception as exc:
            _logger.exception(
                "Bridge: unhandled exception in auth wrapper for %s: %s",
                fn.__name__,
                exc,
            )
            return _service_error(f"Unexpected error: {exc}")

    return _wrapper


# ──────────────────────────────────────────────────────────────────────────────
# Shared utilities used by all controllers
# ──────────────────────────────────────────────────────────────────────────────

def bridge_response(data: dict | list, status: int = 200) -> Response:
    """Wrap a data payload in the version-appropriate bridge envelope."""
    if _request_api_version() == "v2":
        envelope = {
            "ok": status < 400,
            "data": data,
            "meta": {
                "api_version": "v2",
                "request_id": _request_id(),
            },
        }
        return _json_response(envelope, status=status, api_version="v2")

    envelope = {
        "ok": status < 400,
        "data": data,
    }
    return _json_response(envelope, status=status, api_version="v1")


def bridge_error(
    message: str,
    status: int = 400,
    code: str | None = None,
    details: dict | None = None,
    **extra,
) -> Response:
    if _request_api_version() == "v2":
        payload = _v2_error_payload(
            code or "request_failed",
            message,
            details=details or extra,
        )
        return _json_response(payload, status=status, api_version="v2")

    payload = {"ok": False, "error": message, **extra}
    return _json_response(payload, status=status, api_version="v1")
