{
    'name': 'AI Voice Assistant',
    'version': '20.0.1.0.0',
    'summary': 'Hands‑free voice assistant for Odoo 19 (ElevenLabs STT + OpenAI / Gemini)',
    'description': """
AI Voice Assistant for Odoo 19
======================================

Turn any Odoo 19 database into a hands‑free voice assistant, without leaving the Odoo UI.
This module records your voice from the browser, sends the audio to ElevenLabs for
speech‑to‑text (STT), forwards the transcribed text directly to your preferred AI provider
(OpenAI or Google Gemini), and shows the answer instantly inside Odoo.

Unlike many experimental AI addons, this connector:

* Does **not** depend on Odoo’s internal `ai.provider` stack (no latin‑1 issues).
* Does **not** require any external proxy server or webhook – all calls are done from Odoo.
* Stores only text responses (no TTS), keeping the flow fast and stable.

Main features
-------------
* Voice input from the browser using the microphone (no plugins required).
* Reliable speech‑to‑text with ElevenLabs STT (audio is never stored outside Odoo except by ElevenLabs).
* Direct HTTP calls to:
  * **OpenAI** (Chat Completions API).
  * **Google Gemini** (Generative Language API).
* Clean, UTF‑8‑safe handling of AI responses to avoid encoding crashes on Odoo SH.
* Modern OWL‑based “Voice Assistant” widget that opens inside the Odoo backend.
* Real‑time display of:
  * “You said …” (transcribed text).
  * “AI response …” (answer from the selected provider).
* Conversation history stored in `voice.conversation` (per user).
* Configuration page under **AI ▸ Configuration ▸ ElevenLabs Voice Connector**:
  * ElevenLabs API key + language/voice.
  * OpenAI API key.
  * Google Gemini API key.
  * Provider selector (which AI to use for answers).

Compatibility & deployment
--------------------------
* Designed and tested for **Odoo 19.0** (including Odoo.sh).
* Uses only the standard `requests` Python library – no extra system packages needed.
* Safe to install on Odoo.sh and on‑premise instances (no custom services or cron jobs).
    """,
    'author': 'Dow Group',
    'category': 'Tools',
    'website': 'https://www.dowgroup.com',
    'license': 'OPL-1',
    'depends': [
        'base',
        'web',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',  # Load action first
        'views/voice_conversation_views.xml',   # Then menus that reference the action
        'views/voice_page_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'elevenlabs_connector/static/src/js/voice_assistant_widget.js',
            'elevenlabs_connector/static/src/xml/voice_assistant_widget.xml',
            'elevenlabs_connector/static/src/css/voice_assistant_widget.css',
        ],
        'web.assets_frontend': [
            'elevenlabs_connector/static/src/css/voice_page.css',
            'elevenlabs_connector/static/src/js/voice_page_frontend.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'currency': 'USD',
    'images': [
        'static/description/thumbnail.png',  # First image is used as module icon
        'static/description/photo1.png',
        'static/description/photo2.png',
        'static/description/photo3.png',
        'static/description/photo4.png',
        'static/description/photo5.png',
        'static/description/icon.png',
    ],
}

