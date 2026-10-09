{
    'name': 'Dojo Management',
    'version': '20.0.1.0.0',
    'summary': 'Comprehensive Martial Arts School (Dojo) Management with Belt Ranking & Analytics',
    'description': """
Dojo Management - Complete Martial Arts School Administration
==============================================================

Comprehensive module to manage students, belt rankings, martial arts styles,
sessions, attendance, payments and advanced analytics for dojos.

Key Features:
* Student Management with Photos and Belt Ranks
* Belt Ranking System (White to Black Belt with Dan levels)
* Multiple Martial Arts Styles Support (Karate, Taekwondo, Judo, Jiu-Jitsu, etc.)
* Interactive Dashboard with Charts and KPIs
* Session Scheduling and Attendance Tracking
* Payment Management with Late Payment Alerts
* Belt Promotion Wizard
* Advanced Search and Filtering
* Comprehensive Reporting
""",
    'category': 'Dojo',
    'author': 'Samy_Sensei',
    'website': 'https://morisamy.github.io/Samy_Sensei/',
    'license': 'LGPL-3',
    
    'icon': 'static/description/icon.png',
    'images': ['static/description/cover.png'],
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/student_views.xml',
        'views/payment_views.xml',
        'views/attendance_views.xml',
        'views/session_views.xml',
        'views/dojo_dashboard.xml',
        'views/martial_arts_style_views.xml',
        'views/menu.xml',
        'wizard/attendance_wizard_views.xml',
        'wizard/attendance_wizard_access.xml',
        'wizard/belt_promotion_wizard_views.xml',
        'report/dojo_reports.xml',
        'data/demo_data.xml',
    ],
    'installable': False,  # kept as reference only
    'application': True,
    'price': 59.99,
    'currency': 'EUR',
}