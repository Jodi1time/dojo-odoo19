{
    "name": "Dojang Stripe",
    "summary": "Stripe Billing (member subscriptions) + Stripe Issuing (employee cards)",
    "description": """
Provides two distinct Stripe object types:

  res.partner (household)  →  stripe.Customer (Billing)
                  →  stripe.PaymentMethod (card-on-file for recurring billing)

  hr.employee     →  stripe.issuing.Cardholder (individual)
                  →  stripe.issuing.Card (virtual)

The sale.subscription action_generate_invoice() override charges the
household's saved payment method immediately after posting the Odoo invoice.
Falls back to invoice-by-email if no Stripe customer / PM is configured.

Stripe keys are read from the Stripe payment provider configured in
Invoicing → Configuration → Payment Providers → Stripe.
""",
    "version": "20.0.2.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojo Platform",
    "depends": [
        "dojo_subscriptions",
        "hr",
        "account_payment",
        "payment_stripe",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/dojo_stripe_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "dojo_stripe/static/src/issuing.js",
            "dojo_stripe/static/src/household_card_capture.js",
        ],
    },
    "installable": True,
    "auto_install": False,
    "application": False,
}
