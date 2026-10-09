{
    'name': 'Dojo Social Media',
    'version': '20.0.1.0.0',
    'summary': 'Facebook/Instagram post scheduling and publishing for the dojo',
    'author': 'Dojang',
    'category': 'Dojo',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'auto_install': False,
    'depends': [
        'dojo_core',
        'base',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_social.xml',
        'views/dojo_social_account_views.xml',
        'views/dojo_social_post_views.xml',
        'views/dojo_social_menus.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
}
