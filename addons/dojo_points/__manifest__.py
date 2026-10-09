{
    "name": "Dojang Points & Gamification",
    "summary": "Pokémon GO-style points system: auto-award on attendance, streaks, belt promotions, milestones. Instructors can award points manually. Tier titles unlock as members level up.",
    "version": "20.0.1.0.0",
    "category": "Dojo",
    "license": "LGPL-3",
    "author": "Dojang",
    "depends": [
        "dojo_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/dojo_points_config_data.xml",
        "views/dojo_points_config_view.xml",
        "views/dojo_points_transaction_view.xml",
        "views/dojo_points_award_wizard_view.xml",
        "views/dojo_member_points_view_inherit.xml",
        "views/dojo_points_menu.xml",
    ],
    "application": False,
    "auto_install": False,
    "installable": True,
}
