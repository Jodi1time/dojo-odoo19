"""1Club/Dreams-inspired kiosk companion capability layer.

This extends the existing kiosk service without replacing the proven check-in,
roster, attendance, or instructor workflows. Odoo remains the source of truth.
"""
from odoo import api, fields, models


class DojoKioskCompanionService(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    @api.model
    def get_companion_context(self, token, member_id=None):
        config = self.validate_token(token)
        sessions = self.get_todays_sessions()
        member = self.env["dojo.member"].browse(member_id).exists() if member_id else False

        def has_model(name):
            return name in self.env

        capabilities = {
            "check_in": True,
            "classes": True,
            "member_search": True,
            "trial_check_in": "crm.lead" in self.env,
            "membership": has_model("dojo.subscription") or has_model("sale.subscription"),
            "family": has_model("dojo.household") or has_model("dojo.guardian.relationship"),
            "events_testing": has_model("dojo.belt.test"),
            "pos": has_model("pos.order"),
            "access_credentials": has_model("dojo.access.credential"),
            "facility_map": has_model("dojo.facility") or has_model("dojo.location"),
            "ai_companion": True,
        }

        next_actions = [
            {"id": "check_in", "label": "Check in", "icon": "how_to_reg", "enabled": True},
            {"id": "classes", "label": "Today's classes", "icon": "calendar_month", "enabled": True},
            {"id": "find_member", "label": "Find member", "icon": "person_search", "enabled": True},
            {"id": "membership", "label": "Membership", "icon": "card_membership", "enabled": capabilities["membership"]},
            {"id": "family", "label": "Family", "icon": "family_restroom", "enabled": capabilities["family"]},
            {"id": "events", "label": "Testing & events", "icon": "emoji_events", "enabled": capabilities["events_testing"]},
            {"id": "map", "label": "Facility map", "icon": "map", "enabled": capabilities["facility_map"]},
            {"id": "help", "label": "Ask Dojang", "icon": "auto_awesome", "enabled": True},
        ]

        payload = {
            "kiosk": {"id": config.id, "name": config.name, "view_mode": config.view_mode},
            "capabilities": capabilities,
            "next_actions": next_actions,
            "sessions": sessions,
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
        if member:
            payload["member"] = {
                "id": member.id,
                "name": member.name,
                "member_number": member.member_number or "",
                "rank": member.current_rank_id.name if member.current_rank_id else "",
                "membership_state": getattr(member, "membership_state", "") or "",
                "sessions": self.get_enrolled_sessions_today(member.id),
            }
        return payload
