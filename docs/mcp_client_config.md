# MCP Client Configuration — Dojo MCP Server

## Endpoint

```
POST http://localhost:8070/mcp
```

Protocol: MCP 2025-03-26, Streamable HTTP transport (JSON-RPC 2.0)

## Authentication

Each person uses **their own Odoo API key**. Create one in Odoo under your avatar → **Preferences → Account Security → New API Key** (scope RPC). The shared `ai_assistant.api_key` setting no longer opens the MCP server.

| Header          | Value                                                                                         |
| --------------- | --------------------------------------------------------------------------------------------- |
| `Authorization` | `Bearer YOUR_ODOO_API_KEY` (`X-Api-Key: YOUR_ODOO_API_KEY` is also accepted)                  |
| `X-Mcp-Role`    | Optional. Can only **narrow** your access, e.g. an admin testing as `instructor` or `kiosk`   |

Your role comes from your Odoo groups: **Dojo Admin** or Settings access gives `admin`, **Dojo Instructor** (including Front Desk) gives `instructor`. Accounts without either, and portal users, are refused. Tool calls run as your Odoo user and are logged with your login.

## Claude Desktop

Create/edit `~/.config/Claude/claude_desktop_config.json`:

### Option A — Direct HTTP (Claude Desktop 4.x+)

```json
{
  "mcpServers": {
    "dojo": {
      "url": "http://localhost:8070/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ODOO_API_KEY"
      }
    }
  }
}
```

### Option B — Via mcp-remote proxy (older Claude Desktop)

```json
{
  "mcpServers": {
    "dojo": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/mcp-remote", "http://localhost:8070/mcp"],
      "env": {
        "MCP_HEADERS": "{\"Authorization\": \"Bearer YOUR_ODOO_API_KEY\"}"
      }
    }
  }
}
```

## VS Code (Copilot MCP)

Add to `.vscode/settings.json`:

```json
{
  "mcp": {
    "servers": {
      "dojo": {
        "type": "http",
        "url": "http://localhost:8070/mcp",
        "headers": {
          "Authorization": "Bearer YOUR_ODOO_API_KEY"
        }
      }
    }
  }
}
```

## Available Capabilities

| Capability | Count | Description                                                       |
| ---------- | ----- | ----------------------------------------------------------------- |
| Tools      | 48    | Auto-generated from intent schemas; role-filtered                 |
| Resources  | 3     | `dojo://schedule/today`, `dojo://members/active`, `dojo://agents` |

## Quick Verification

```bash
# Initialize handshake (no auth required)
curl -s -X POST http://localhost:8070/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# List tools (auth required)
curl -s -X POST http://localhost:8070/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ODOO_API_KEY" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Call a tool
curl -s -X POST http://localhost:8070/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ODOO_API_KEY" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"schedule_today","arguments":{}}}'

# Read a resource
curl -s -X POST http://localhost:8070/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ODOO_API_KEY" \
  -d '{"jsonrpc":"2.0","id":4,"method":"resources/read","params":{"uri":"dojo://agents"}}'
```
