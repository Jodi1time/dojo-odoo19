{
    'name': 'Dojang Onboarding',
    'version': '20.0.1.0.0',
    'summary': 'Step-by-step member onboarding wizard for admins and instructors',
    'description': """
        Provides a multi-step onboarding wizard to register a new dojo member
        end-to-end: contact info, household assignment, class enrollment,
        subscription plan, and optional portal login creation.
    """,
    'author': 'Dojo',
    'category': 'Dojo',
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'auto_install': True,
    'depends': [
        'dojo_core',
        'dojo_subscriptions',
        'portal',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/dojo_onboarding_wizard_views.xml',
        'views/dojo_onboarding_views.xml',
    ],
}
