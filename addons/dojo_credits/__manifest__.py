{
    "name": "Dojang Credits",
    "summary": "Class credit ledger — replaces hard-coded weekly session cap with a per-subscription credit pool",
    "version": "20.0.1.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
        "dojo_subscriptions",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/sequences.xml",
        "views/dojo_credit_transaction_views.xml",
        "views/dojo_subscription_plan_views.xml",
        "views/dojo_program_credit_views.xml",
        "views/dojo_subscription_credit_views.xml",
        "wizards/dojo_credit_adjustment_wizard_views.xml",
    ],
    "application": False,
    "auto_install": True,
    "installable": True,
}
