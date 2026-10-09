{
    "name": "Dojo Connect AI",
    "version": "20.0.1.0.0",
    "summary": "AI receptionist (Kai) with CRM lead generation from phone calls",
    "description": """
        Bridges Twilio phone calls (connect module) with ElevenLabs Conversational AI
        and auto-generates CRM leads. Incoming calls are answered by Kai, a voice AI
        agent that can check session availability, look up members, book trials, and
        transfer to humans. Missed calls auto-create leads. All transcripts are posted
        to CRM chatter.
    """,
    "author": "Dojo",
    "category": "Phone",
    "license": "LGPL-3",
    "depends": [
        "connect",
        "dojo_crm",
        "ai_assistant",
        "dojo_ai_caller",
    ],
    "data": [
        # Security
        "security/ir.model.access.csv",
        # Data
        "data/utm_medium_data.xml",
        "data/crm_tag_data.xml",
        # Views
        "views/connect_ai_agent_views.xml",
        "views/connect_call_views.xml",
        "views/connect_number_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
