# -*- coding: utf-8 -*-
{
    "name": "AI MCP Server",
    "version": "20.0.1.0.0",
    "category": "Technical",
    "summary": "Model Context Protocol server — exposes AI tools to external LLMs",
    "description": """
Implements the MCP (Model Context Protocol) Streamable HTTP transport so that
external LLMs like Claude Desktop, ChatGPT, Cursor, etc. can discover and call
AI tools.

Endpoint: POST /mcp  (JSON-RPC 2.0)

Supported methods:
  - initialize        — handshake + capability negotiation
  - tools/list        — auto-generated from ai.intent.schema
  - tools/call        — executes intent through domain agent
  - resources/list    — lists available data resources
  - resources/read    — reads a specific resource

Auth: X-Api-Key header (same key as /api/v1/ai/* endpoints).
""",
    "author": "Dojang",
    "depends": ["ai_assistant", "ai_vector"],
    "data": [
        "views/ai_mcp_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
