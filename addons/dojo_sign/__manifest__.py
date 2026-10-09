{
    'name': 'Dojang Sign & Waivers',
    'version': '20.0.2.0.0',
    'summary': 'Inline waiver signing during member onboarding (Community-compatible)',
    'description': """
        Replaces the Odoo Enterprise Sign dependency with an inline, blocking waiver
        step inside the member onboarding wizard.  Uses bi_all_digital_sign's
        Binary-image approach to store the drawn signature and embeds it in a
        QWeb PDF that is attached to the newly created member record.

        Features:
        - ``waiver`` step injected into the onboarding wizard after subscription
          selection and before portal-access creation.
        - Admin-editable waiver content stored in a singleton ``dojo.waiver.config``
          model (Dojo > Configuration > Waiver Text).
        - Canvas draw-to-sign widget (Odoo native ``widget="signature"``).
        - Legal authority checkbox must be ticked before advancing.
        - On wizard completion the signed PDF is generated via QWeb and stored as
          an ``ir.attachment`` linked to the member.
        - Waiver tab on the member backend form shows status, signature preview,
          and a button to download the PDF.
        - No daily cron required — signing is blocking and synchronous.
        - Works on Odoo Community (no Enterprise modules required).
    """,
    'author': 'Dojo',
    'category': 'Dojo',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'auto_install': False,
    'depends': [
        'dojo_onboarding',
        'bi_all_digital_sign',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/dojo_waiver_config_views.xml',
        'views/dojo_member_waiver_views.xml',
        'views/dojo_onboarding_waiver_views.xml',
        'report/waiver_report.xml',
    ],
}
