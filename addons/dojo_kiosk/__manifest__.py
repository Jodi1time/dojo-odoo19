{
    "name": "Dojang Kiosk",
    "summary": "Tablet check-in kiosk for Dojang members and instructors",
    "version": "20.0.1.1.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
        "dojo_subscriptions",
        "ai_assistant",
        "dojo_crm",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/dojo_kiosk_views.xml",
        "views/dojo_kiosk_announcement_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "dojo_kiosk/static/src/css/kiosk_admin.css",
            "dojo_kiosk/static/src/xml/kiosk_admin.xml",
            "dojo_kiosk/static/src/js/kiosk_admin.js",
        ],
    },
    "application": True,
    "installable": True,
    "auto_install": False,
}
