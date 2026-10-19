# -*- coding: utf-8 -*-
"""
MCP (Model Context Protocol) Streamable HTTP Server.

Single endpoint ``POST /mcp`` that speaks JSON-RPC 2.0.
External LLMs (Claude Desktop, Cursor, ChatGPT plugins) connect here
to discover and call AI tools.

MCP spec reference: https://modelcontextprotocol.io/specification/2025-03-26

Supported methods
-----------------
initialize          — handshake + capability advertisement
tools/list          — auto-generated from ``ai.intent.schema``
tools/call          — routes through domain agent → intent handler
resources/list      — available data resources (schedules, members, etc.)
resources/read      — read a specific resource by URI
"""

import json
import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

_API_KEY_SCOPE = "rpc"
_ROLE_RANK = {"kiosk": 1, "instructor": 2, "admin": 3}

# MCP protocol version we support
_MCP_PROTOCOL_VERSION = "2025-03-26"

# Server metadata
_SERVER_INFO = {
    "name": "ai-mcp-server",
    "version": "1.0.0",
}


def _jsonrpc_response(result, req_id):
    """Build a JSON-RPC 2.0 success response."""
    return Response(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, default=str),
        status=200,
        content_type="application/json",
    )


def _jsonrpc_error(code, message, req_id=None, data=None):
    """Build a JSON-RPC 2.0 error response."""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return Response(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "error": err}, default=str),
        status=200,  # JSON-RPC errors still use HTTP 200
        content_type="application/json",
    )


class AiMCPServer(http.Controller):
    """MCP Streamable HTTP transport — single POST endpoint."""

    # ── Auth ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _authenticate():
        """Identify the caller from a personal Odoo API key.

        Returns (uid, role, error). The role comes from the user's Odoo
        groups; X-Mcp-Role may only narrow it, never widen it.
        """
        headers = request.httprequest.headers
        auth = headers.get("Authorization", "")
        key = auth[7:].strip() if auth[:7].lower() == "bearer " else headers.get("X-Api-Key", "").strip()
        if not key:
            return None, None, "Missing API key. Send a personal Odoo API key as 'Authorization: Bearer <key>'."
        uid = request.env["res.users.apikeys"].sudo()._check_credentials(scope=_API_KEY_SCOPE, key=key)
        if not uid:
            return None, None, "Invalid API key"
        user = request.env["res.users"].sudo().browse(uid)
        if not user.active or user.share:
            return None, None, "This account can't use the MCP server"
        if user.has_group("base.group_system") or user.has_group("dojo_core.group_dojo_admin"):
            granted = "admin"
        elif user.has_group("dojo_core.group_dojo_instructor"):
            granted = "instructor"
        else:
            return None, None, "This account has no Dojang staff role"
        asked = headers.get("X-Mcp-Role", "").strip().lower()
        if asked in _ROLE_RANK and _ROLE_RANK[asked] <= _ROLE_RANK[granted]:
            return uid, asked, None
        return uid, granted, None

    # ── Main endpoint ────────────────────────────────────────────────────────

    @http.route(
        "/mcp",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS", "GET"],
        csrf=False,
    )
    def handle(self, **kw):
        """
        MCP Streamable HTTP transport entry point.

        GET  /mcp  → SSE stream (not implemented — return 405)
        POST /mcp  → JSON-RPC 2.0 dispatch
        """
        if request.httprequest.method == "OPTIONS":
            return Response("", status=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Api-Key, X-Mcp-Role",
            })

        if request.httprequest.method == "GET":
            return Response("SSE transport not supported. Use POST.", status=405)

        # ── Parse JSON-RPC ───────────────────────────────────────────────────
        try:
            body = json.loads(request.httprequest.get_data(as_text=True))
        except (json.JSONDecodeError, TypeError):
            return _jsonrpc_error(-32700, "Parse error: invalid JSON")

        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}

        if not method:
            return _jsonrpc_error(-32600, "Invalid request: missing 'method'", req_id)

        # ── Auth (skip for initialize) ───────────────────────────────────────
        role = None
        if method != "initialize":
            uid, role, err = self._authenticate()
            if err:
                return _jsonrpc_error(-32001, err, req_id)
            request.update_env(user=uid)
            if method == "tools/call":
                _logger.info("MCP tools/call %s by %s as %s",
                             (params.get("name") if isinstance(params, dict) else None),
                             request.env.user.login, role)

        # ── Dispatch ─────────────────────────────────────────────────────────
        dispatch = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "resources/list": self._handle_resources_list,
            "resources/read": self._handle_resources_read,
            "ping": self._handle_ping,
        }

        handler = dispatch.get(method)
        if not handler:
            return _jsonrpc_error(
                -32601,
                f"Method not found: {method}",
                req_id,
            )

        try:
            result = handler(params, role)
            return _jsonrpc_response(result, req_id)
        except Exception as e:
            _logger.error("MCP method %s failed: %s", method, e, exc_info=True)
            return _jsonrpc_error(-32603, f"Internal error: {e}", req_id)

    # ── Method handlers ──────────────────────────────────────────────────────

    def _handle_initialize(self, params, role):
        """MCP initialize handshake."""
        return {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": _SERVER_INFO,
        }

    def _handle_ping(self, params, role):
        return {}

    def _handle_tools_list(self, params, role):
        """Return available tools auto-generated from intent schemas."""
        generator = request.env["ai.mcp.tool.generator"].sudo()
        tools = generator.generate_tool_list(role)
        return {"tools": tools}

    def _handle_tools_call(self, params, role):
        """
        Execute a tool (intent) by name.

        Params:
            name: str — intent_type (tool name)
            arguments: dict — resolved parameters
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if not tool_name:
            return {
                "content": [{"type": "text", "text": "Error: missing tool 'name'"}],
                "isError": True,
            }

        # Verify the intent exists and the role can access it
        IntentSchema = request.env["ai.intent.schema"].sudo()
        schema = IntentSchema.search([
            ("intent_type", "=", tool_name),
            ("active", "=", True),
        ], limit=1)

        if not schema:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

        if not schema.check_role_permission(role):
            return {
                "content": [{"type": "text", "text": f"Permission denied: role '{role}' cannot use '{tool_name}'"}],
                "isError": True,
            }

        # Route through domain agent if available
        try:
            agent_obj = None
            if "ai.agent" in request.env:
                agent_obj = (
                    request.env["ai.agent"]
                    .sudo()
                    .get_agent_for_intent(tool_name)
                )

            if agent_obj:
                result = agent_obj.execute(tool_name, arguments, role=role)
            else:
                # Fallback: direct service execution
                service = request.env["ai.assistant.service"].sudo()
                result = service._execute_intent(tool_name, {}, arguments, None)

            # Format as MCP tool result
            success = result.get("success", False)
            text_parts = []

            if result.get("message"):
                text_parts.append(result["message"])
            if result.get("data"):
                text_parts.append(json.dumps(result["data"], default=str, indent=2))
            if result.get("error"):
                text_parts.append(f"Error: {result['error']}")

            return {
                "content": [{"type": "text", "text": "\n".join(text_parts) or "Done."}],
                "isError": not success,
            }

        except Exception as e:
            _logger.error("MCP tool call %s failed: %s", tool_name, e, exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Execution failed: {e}"}],
                "isError": True,
            }

    def _handle_resources_list(self, params, role):
        """List available data resources."""
        resources = [
            {
                "uri": "ai://schedule/today",
                "name": "Today's Schedule",
                "description": "All class sessions scheduled for today",
                "mimeType": "application/json",
            },
            {
                "uri": "ai://members/active",
                "name": "Active Members",
                "description": "List of all active members",
                "mimeType": "application/json",
            },
            {
                "uri": "ai://agents",
                "name": "AI Agents",
                "description": "Available domain agents and their intent counts",
                "mimeType": "application/json",
            },
        ]
        return {"resources": resources}

    def _handle_resources_read(self, params, role):
        """Read a specific resource by URI."""
        uri = params.get("uri", "")

        readers = {
            "ai://schedule/today": self._read_schedule_today,
            "ai://members/active": self._read_active_members,
            "ai://agents": self._read_agents,
        }

        reader = readers.get(uri)
        if not reader:
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": f"Unknown resource: {uri}",
                }],
            }

        try:
            data = reader(role)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(data, default=str, indent=2),
                }],
            }
        except Exception as e:
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": f"Error reading resource: {e}",
                }],
            }

    # ── Resource readers ─────────────────────────────────────────────────────

    def _read_schedule_today(self, role):
        """Return today's class sessions."""
        from datetime import date
        today = date.today()
        sessions = (
            request.env["dojo.class.session"]
            .sudo()
            .search([("date", "=", today)], order="start_time")
        )
        return [{
            "id": s.id,
            "name": s.display_name,
            "date": str(s.date),
            "start_time": s.start_time,
            "end_time": s.end_time,
        } for s in sessions]

    def _read_active_members(self, role):
        """Return active members (limited to 100)."""
        members = (
            request.env["dojo.member"]
            .sudo()
            .search([("membership_state", "=", "active")], limit=100)
        )
        return [{
            "id": m.id,
            "name": m.display_name,
            "membership_state": m.membership_state,
        } for m in members]

    def _read_agents(self, role):
        """Return available AI agents."""
        agents = (
            request.env["ai.agent"]
            .sudo()
            .search([("active", "=", True)], order="sequence")
        )
        return [{
            "name": a.name,
            "domain": a.domain,
            "intent_count": a.intent_count,
            "has_system_prompt": bool(a.system_prompt_template),
        } for a in agents]
