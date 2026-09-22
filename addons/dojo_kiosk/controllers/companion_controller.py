from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError


class KioskCompanionController(http.Controller):
    """Versionable, token-gated API surface for the kiosk AI companion."""

    def _service(self):
        return request.env["dojo.kiosk.service"].sudo()

    def _validate(self, token):
        if not token:
            return None, {"success": False, "error": "token_required"}
        svc = self._service()
        try:
            svc.validate_token(token)
        except AccessError:
            return None, {"success": False, "error": "invalid_token"}
        return svc, None

    @http.route(
        "/kiosk/companion/context",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_context(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        return svc.get_companion_context(token, member_id=member_id)

    @http.route(
        "/kiosk/companion/credential",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_credential(self, token=None, credential=None, kind="barcode", **kw):
        svc, error = self._validate(token)
        if error:
            return error
        return svc.resolve_companion_credential(credential, kind=kind)

    @http.route(
        "/kiosk/companion/household",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_household(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required"}
        return svc.get_household_context(member_id)

    @http.route(
        "/kiosk/companion/membership",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_membership(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required"}
        return svc.get_membership_summary(member_id)

    @http.route(
        "/kiosk/companion/sessions",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_sessions(self, token=None, member_id=None, date=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required", "sessions": []}
        return svc.get_member_session_options(member_id, date=date)

    @http.route(
        "/kiosk/companion/book",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_book(self, token=None, member_id=None, session_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id or not session_id:
            return {"success": False, "error": "member_id_and_session_id_required"}
        return svc.book_member_session(member_id, session_id)

    @http.route(
        "/kiosk/companion/ask",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_ask(self, token=None, text=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error

        prompt = (text or "").strip()
        if not prompt:
            return {"success": False, "error": "text_required"}
        if len(prompt) > 500:
            return {"success": False, "error": "message_too_long"}

        member = request.env["dojo.member"].sudo().browse(member_id).exists() if member_id else False
        if member:
            prompt = (
                "[Kiosk context: selected member is %s, member id %s.]\n%s"
                % (member.name, member.id, prompt)
            )

        try:
            assistant = request.env["ai.assistant.service"].sudo()
            result = assistant.handle_command(
                prompt,
                role="kiosk",
                input_type="text",
                channel="lookup",
            )
        except Exception:
            return {
                "success": False,
                "error": "ai_unavailable",
                "response": "I couldn't reach the Dojang assistant right now.",
            }

        intent = result.get("intent") or {}
        intent_type = intent.get("intent_type") if isinstance(intent, dict) else None
        schema = (
            request.env["ai.intent.schema"].sudo().get_by_type(intent_type)
            if intent_type
            else request.env["ai.intent.schema"].browse()
        )

        # Public kiosk AI is read-only. Mutating intents hand back to deterministic
        # kiosk flows rather than executing through a public AI route.
        if (
            result.get("state") == "pending_confirmation"
            or (schema and schema.requires_confirmation)
        ):
            return {
                "success": True,
                "state": "action_required",
                "intent_type": intent_type or "",
                "response": (
                    "I can help with that, but the kiosk needs you to complete "
                    "the action using the secure on-screen flow."
                ),
                "handoff": (
                    "classes"
                    if intent_type in ("attendance_checkin", "attendance_checkout")
                    else "home"
                ),
            }

        return {
            "success": bool(result.get("success", True)),
            "state": result.get("state") or "executed",
            "intent_type": intent_type or "",
            "response": (
                result.get("response")
                or (result.get("result") or {}).get("message")
                or "I found that for you."
            ),
            "suggestions": result.get("suggestions") or [],
        }

    @http.route(
        "/kiosk/companion/testing",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_testing(self, token=None, member_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id:
            return {"success": False, "error": "member_id_required", "tests": []}
        return svc.get_testing_options(member_id)

    @http.route(
        "/kiosk/companion/cancel-booking",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def companion_cancel_booking(self, token=None, member_id=None, session_id=None, **kw):
        svc, error = self._validate(token)
        if error:
            return error
        if not member_id or not session_id:
            return {"success": False, "error": "member_id_and_session_id_required"}
        return svc.cancel_member_session(member_id, session_id)
