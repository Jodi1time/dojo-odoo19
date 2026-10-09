{
    'name': 'Dojang Onboarding — Stripe Payment',
    'version': '20.0.1.0.0',
    'summary': 'Collects a Stripe payment method during member onboarding',
    'description': "Bridge between dojo_onboarding and dojo_stripe. "
                   "Adds a Stripe payment capture step to the onboarding wizard so staff "
                   "can collect the guardian's card during member registration.",
    'author': 'Dojo Platform',
    'category': 'Dojo',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'auto_install': False,
    'depends': [
        'dojo_onboarding',
        'dojo_stripe',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/dojo_onboarding_wizard_stripe_inherit.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dojo_onboarding_stripe/static/src/xml/onboarding_stripe_payment.xml',
            'dojo_onboarding_stripe/static/src/js/onboarding_stripe_payment.js',
        ],
    },
}
