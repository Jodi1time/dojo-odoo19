{
    'name': 'Dojang AI Assistant',
    'version': '20.0.1.0.0',
    'summary': 'Reusable AI voice assistant service for Dojang modules',
    'description': """
        Provides a centralized AI assistant service that can be used across different Dojang modules:
        
        Features:
        - Natural language command processing
        - Two-phase confirmation flow (parse → confirm → execute)
        - Structured intent parsing with role-based permissions
        - Audit logging of all AI actions
        - Undo capability for reversible operations
        - Support for text and voice input
        
        Supported Intents:
        - Member lookup, create, update
        - Class enrollment/unenrollment
        - Attendance check-in/check-out
        - Belt rank promotion
        - Subscription management
        - Parent/guardian communication
        
        Can be integrated with:
        - Instructor Dashboard
        - Kiosk Module
        - Any other Dojang frontend
    """,
    'author': 'Dojang',
    'category': 'Dojo',
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'auto_install': False,
    'depends': [
        'dojo_core',
        'base',
        'mail',
        'calendar',
        'sms',
        'mass_mailing',
        'mass_mailing_sms',
        'elevenlabs_connector',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/ai_assistant_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_assistant/static/src/xml/voice_assistant.xml',
            'ai_assistant/static/src/js/voice_assistant.js',
            'ai_assistant/static/src/css/dojo_voice_assistant_page.css',
            'ai_assistant/static/src/xml/dojo_voice_assistant_page.xml',
            'ai_assistant/static/src/js/dojo_voice_assistant_page.js',
            'ai_assistant/static/src/css/walkie_talkie.css',
            'ai_assistant/static/src/xml/walkie_talkie.xml',
            'ai_assistant/static/src/js/walkie_talkie.js',
            # PROTOTYPE: Channel Beta mode assets
            'ai_assistant/static/src/css/walkie_channel.css',
            'ai_assistant/static/src/xml/walkie_channel.xml',
            'ai_assistant/static/src/js/walkie_channel.js',
            # PROTOTYPE: Elder Beta mode assets
            'ai_assistant/static/src/css/walkie_elder.css',
            'ai_assistant/static/src/xml/walkie_elder.xml',
            'ai_assistant/static/src/js/walkie_elder.js',
            # Discuss side panel
            'ai_assistant/static/src/css/discuss_ai_panel.css',
            'ai_assistant/static/src/xml/discuss_ai_panel.xml',
            'ai_assistant/static/src/js/discuss_ai_panel.js',
        ],
    },
    'demo': [],
    'external_dependencies': {},
}
