{
    "name": "Dojang Calendar",
    "version": "20.0.1.0.0",
    "summary": "Class Calendar — sync Dojang sessions to calendar.event with roster & attendance controls",
    "author": "Dojang",
    "category": "Dojo",
    "depends": ["dojo_core", "calendar"],
    "data": [
        "security/ir.model.access.csv",
        "views/calendar_event_view_inherit.xml",
        "views/calendar_class_action.xml",
    ],
    "post_init_hook": "_backfill_session_calendar_events",
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
