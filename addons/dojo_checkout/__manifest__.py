{
    "name": "Dojang Checkout Pages",
    "summary": "Public checkout flow: plan selection, day picker, upsells, invoice or pay-now, portal upgrade",
    "version": "20.0.2.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
        "dojo_subscriptions",
        "dojo_members_portal",
        "dojo_crm",
        "website",
        "payment",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/dojo_checkout_upsell_views.xml",
        "views/dojo_checkout_config_views.xml",
        "views/dojo_checkout_session_views.xml",
        "views/checkout_templates.xml",
        "views/portal_upgrade_inject.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "dojo_checkout/static/src/checkout.css",
            "dojo_checkout/static/src/checkout.js",
        ],
    },
    "application": False,
    "installable": True,
}
