{
    'name': 'Dojo Website',
    'version': '20.0.1.0.0',
    'category': 'Dojo',
    'summary': 'Custom dojang website with trial lesson forms integrated into Odoo CRM',
    'depends': ['website', 'website_crm'],
    'data': [
        'views/templates.xml',
        'data/website_pages.xml',
        'data/website_menus.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'dojo_website/static/src/css/dojo_website.css',
            'dojo_website/static/src/js/dojo_website.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
