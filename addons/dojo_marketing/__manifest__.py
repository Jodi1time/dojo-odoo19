{
    "name": "Dojo Promotions",
    "summary": "Promotional cards with QR codes — published to kiosk carousel and member portal",
    "version": "20.0.3.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
        "dojo_kiosk",
        "dojo_members_portal",
        "marketing_card",
        "automation_oca",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/dojo_marketing_card_views.xml",
        "views/dojo_member_badge_button.xml",
        "views/portal_marketing_banner.xml",
        "views/card_campaign_views_inherit.xml",
        "data/marketing_card_campaigns.xml",
        "data/automation_oca_membership.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "dojo_marketing/static/src/css/marketing_backend.scss",
        ],
        "web.assets_frontend": [
            "dojo_marketing/static/src/dojo_marketing.css",
        ],
    },
    "application": True,
    "installable": True,
    "post_init_hook": "post_init_hook",
}
