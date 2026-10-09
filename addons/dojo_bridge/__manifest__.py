{
    "name": "Dojang Bridge",
    "summary": "Headless API bridge: Control Plane (NestJS) ↔ Odoo Business Plane",
    "description": """
Exposes a versioned, stateless REST API secured by HS256 JWTs issued by the
NestJS Control Plane. Handles multi-tenant DB routing, Firebase UID to
dojo.member identity resolution, and typed webhook ingestion.

All routes use auth=none. Odoo session auth is never involved.
The JWT issued by NestJS (after Firebase verification) is the sole gate.
""",
    "version": "20.0.2.0.0",
    "author": "Dojang Platform",
    "category": "Hidden",
    "depends": [
        "dojo_core",
        "dojo_subscriptions",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/bridge_identity_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
