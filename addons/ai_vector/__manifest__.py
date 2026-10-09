{
    'name': 'AI Vector Intelligence',
    'version': '20.0.1.0.0',
    'summary': 'Vector embedding layer for AI intent routing and multi-agent orchestration',
    'description': """
        Adds semantic vector routing to the AI Assistant:

        - pgvector-backed intent embeddings for fast similarity search
        - Replaces full intent prompt injection with top-K filtered prompts
        - Domain agent definitions for multi-agent orchestration
        - Embedding cache via Odoo ormcache
        - "Did you mean?" suggestions based on similarity scores

        Phase 1: Vector Intent Router (50-90% token reduction)
        Phase 2: Domain Agent Split (modular expert agents)
    """,
    'author': 'Dojang',
    'category': 'Technical',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
    'post_migrate': 'post_migrate',
    'depends': [
        'ai_assistant',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_vector.xml',
        # Intent schema records (per-agent, loaded before agent data)
        'data/intents_core.xml',
        'data/intents_attendance.xml',
        'data/intents_enrollment.xml',
        'data/intents_subscriptions.xml',
        'data/intents_communications.xml',
        'data/intents_marketing.xml',
        'data/intents_belt_rank.xml',
        'data/intents_calendar.xml',
        'data/intents_sales.xml',
        'data/intents_pos.xml',
        'data/intents_accounting.xml',
        'data/intents_hr.xml',
        'data/intents_discuss.xml',
        'data/intents_meta.xml',
        'data/ai_agent_data.xml',
    ],
}
