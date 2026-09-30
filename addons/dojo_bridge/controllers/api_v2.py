"""Dojang Bridge API v2.

Business-action API contract for the Odoo 20 migration line.
This does not expose generic ORM operations. It reuses the existing
x.bridge.service domain logic and tenant/JWT boundary.
"""
import json
import logging
from datetime import datetime

from odoo import http
from odoo.http import request
from odoo.exceptions import UserError, ValidationError

from .auth_middleware import (
    require_bridge_auth,
    bridge_response,
    bridge_error,
)

_logger = logging.getLogger(__name__)


def _parse_json_body():
    try:
        raw = request.httprequest.get_data(as_text=True) or "{}"
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class BridgeApiV2Controller(http.Controller):
    """Stable domain-oriented v2 contract for Next.js / control-plane clients."""

    @http.route(
        "/bridge/v2/members/me",
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def member_me(
        self,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        if not b_member or not b_member.id:
            return bridge_error(
                "No member is linked to this identity.",
                status=404,
                code="member_not_linked",
            )

        try:
            data = b_env["x.bridge.service"].sudo().get_member_profile(
                b_member.id,
                b_company_id,
            )
        except UserError as exc:
            return bridge_error(
                str(exc),
                status=404,
                code="member_not_found",
            )

        return bridge_response({
            "identity": b_identity.to_api_dict(),
            "member": data,
        })

    @http.route(
        "/bridge/v2/classes/sessions",
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def sessions(
        self,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        params = request.httprequest.args
        from_raw = params.get("from")
        to_raw = params.get("to")
        program_raw = params.get("program_id")

        from_dt = _parse_dt(from_raw)
        to_dt = _parse_dt(to_raw)
        program_id = _parse_int(program_raw)

        if from_raw and from_dt is None:
            return bridge_error(
                "Invalid 'from' datetime.",
                status=400,
                code="invalid_datetime",
                details={"field": "from"},
            )
        if to_raw and to_dt is None:
            return bridge_error(
                "Invalid 'to' datetime.",
                status=400,
                code="invalid_datetime",
                details={"field": "to"},
            )
        if program_raw and program_id is None:
            return bridge_error(
                "Invalid program_id.",
                status=400,
                code="invalid_program_id",
            )

        try:
            data = b_env["x.bridge.service"].sudo().get_sessions(
                company_id=b_company_id,
                from_dt=from_dt,
                to_dt=to_dt,
                program_id=program_id,
                member_id=b_member.id if b_member else None,
            )
        except UserError as exc:
            return bridge_error(
                str(exc),
                status=400,
                code="session_query_failed",
            )

        return bridge_response({"sessions": data})

    @http.route(
        "/bridge/v2/classes/sessions/<int:session_id>",
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def session_detail(
        self,
        session_id,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        try:
            data = b_env["x.bridge.service"].sudo().get_session_detail(
                session_id=session_id,
                company_id=b_company_id,
                member_id=b_member.id if b_member else None,
            )
        except UserError as exc:
            return bridge_error(
                str(exc),
                status=404,
                code="session_not_found",
                details={"session_id": session_id},
            )
        return bridge_response({"session": data})

    @http.route(
        "/bridge/v2/classes/sessions/<int:session_id>/bookings",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def create_booking(
        self,
        session_id,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        if not b_member or not b_member.id:
            return bridge_error(
                "No member is linked to this identity.",
                status=403,
                code="member_not_linked",
            )

        try:
            data = b_env["x.bridge.service"].sudo().do_enroll(
                session_id=session_id,
                member_id=b_member.id,
                company_id=b_company_id,
            )
        except (UserError, ValidationError) as exc:
            return bridge_error(
                str(exc),
                status=409,
                code="booking_not_allowed",
                details={"session_id": session_id},
            )

        return bridge_response(
            {
                "booking": data,
                "idempotency_key": (
                    request.httprequest.headers.get("X-Idempotency-Key")
                    or None
                ),
            },
            status=200 if data.get("already_existed") else 201,
        )

    @http.route(
        "/bridge/v2/classes/sessions/<int:session_id>/bookings",
        type="http",
        auth="public",
        methods=["DELETE", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def cancel_booking(
        self,
        session_id,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        if not b_member or not b_member.id:
            return bridge_error(
                "No member is linked to this identity.",
                status=403,
                code="member_not_linked",
            )

        try:
            data = b_env["x.bridge.service"].sudo().do_cancel_enrollment(
                session_id=session_id,
                member_id=b_member.id,
                company_id=b_company_id,
            )
        except UserError as exc:
            return bridge_error(
                str(exc),
                status=404,
                code="booking_not_found",
                details={"session_id": session_id},
            )
        return bridge_response({"booking": data})

    @http.route(
        "/bridge/v2/attendance/check-ins",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
    )
    @require_bridge_auth
    def attendance_checkin(
        self,
        b_env=None,
        b_member=None,
        b_company_id=None,
        b_identity=None,
        b_payload=None,
        **kw,
    ):
        if not b_member or not b_member.id:
            return bridge_error(
                "No member is linked to this identity.",
                status=403,
                code="member_not_linked",
            )

        payload = _parse_json_body()
        if payload is None:
            return bridge_error(
                "Invalid JSON body.",
                status=400,
                code="invalid_json",
            )

        session_id = _parse_int(payload.get("session_id"))
        if not session_id:
            return bridge_error(
                "session_id is required.",
                status=400,
                code="session_id_required",
            )

        try:
            data = b_env["x.bridge.service"].sudo().do_checkin(
                session_id=session_id,
                member_id=b_member.id,
                company_id=b_company_id,
            )
        except (UserError, ValidationError) as exc:
            return bridge_error(
                str(exc),
                status=409,
                code="checkin_not_allowed",
                details={"session_id": session_id},
            )

        return bridge_response(
            {
                "receipt": data,
                "idempotency_key": (
                    request.httprequest.headers.get("X-Idempotency-Key")
                    or None
                ),
            },
            status=200 if data.get("already_existed") else 201,
        )
