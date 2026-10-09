{
    "name": "Firebase Integration",
    "summary": "Email relay via Gmail/Firebase Cloud Functions + FCM web push notifications for the member portal.",
    "version": "20.0.1.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
        "mail",
        "dojo_members_portal",
        "dojo_subscriptions",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_firebase.xml",
        "views/res_config_settings.xml",
        "views/portal_push_inject.xml",
    ],
    "application": False,
    "auto_install": False,
    "installable": True,
}
