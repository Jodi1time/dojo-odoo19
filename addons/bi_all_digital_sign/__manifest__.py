# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.
{
    'name': "All In One Digital Signature App | Digital Signature on Purchase, Invoice and Bill",
    "version" : "20.0.0.0",
    "category" : "Extra Tools",
    'summary': "eSign purchase digital signature on purchase order eSign invoice digital signature on invoice eSign bill digital signature on bill digital signature on PO digital signature on RFQ electronic signature on bill and invoice electronic signature on PO eSign" ,
    'description': """All In One Digital Signature Odoo App is designed to streamline and digitize the signing process for key business documents such as purchase orders, invoices, and bills. This app enables users to seamlessly add legally compliant digital signatures directly within the Odoo system, eliminating the need for manual paperwork and enhancing operational efficiency. Users can also see customer and vendor digital signature in purchase order, invoice and bill PDF report.""",
    'author': 'BROWSEINFO',
    'website': 'https://www.browseinfo.com/demo-request?app=bi_all_digital_sign&version=19&edition=Community',
    'depends': ['base','account','purchase'],
    'data': [
        'views/account_move_views.xml',
        'views/purchase_order_views.xml',
    ],
    "license":'OPL-1',
    'installable': True,
    'auto_install': False,
    "live_test_url" : 'https://www.browseinfo.com/demo-request?app=bi_all_digital_sign&version=19&edition=Community',
    "images":['static/description/Banner.gif'],
}
