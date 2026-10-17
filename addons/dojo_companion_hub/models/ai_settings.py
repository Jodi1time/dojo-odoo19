"""Site-scoped AI activation, proven with a synthetic draft in this worker."""
import json

from odoo import fields, models
from odoo.addons.ai_assistant.models.openai_conversation import configuration, ProviderUnavailable
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, session_version
from .service import HubProblem, exact


class HubAiSettings(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _ai_settings(self, site, grant, payload):
        self._role(grant, "owner manager")
        exact(payload, "")
        provider = self.env["ai.processor"].sudo()._get_provider()
        configured, model = False, None
        try:
            _, model = configuration(self.env["ir.config_parameter"].sudo().get_str)
            configured = provider in ("openai", "odoo_native")
        except ProviderUnavailable:
            pass
        last = self.env["dojo.hub.receipt"].sudo().search([
            ("site_id", "=", site.id), ("operation", "=", "enable_ai")], order="id desc", limit=1)
        evidence = json.loads(last.response_json) if last else None
        return {"enabled": bool(site.ai_enabled and site.kiosk_config_id.integration_companion_ai_enabled),
                "configured": configured, "model": model, "canEnable": grant.role == "owner",
                "siteVersion": session_version(site), "configVersion": session_version(site.kiosk_config_id),
                "lastCheck": {"at": iso(last.create_date), "model": evidence.get("model"),
                              "mode": evidence.get("mode")} if last else None}

    def _enable_ai(self, site, grant, payload):
        self._role(grant, "owner")
        exact(payload, "expectedSiteVersion expectedConfigVersion")
        self._lock(site)
        self._lock(site.kiosk_config_id)
        if any(type(payload[key]) is not int for key in payload):
            raise HubProblem("INVALID_COMMAND", 400)
        if (payload["expectedSiteVersion"] != session_version(site)
                or payload["expectedConfigVersion"] != session_version(site.kiosk_config_id)):
            raise HubProblem("VERSION_CONFLICT")
        settings = self._ai_settings(site, grant, {})
        if not settings["configured"]:
            raise HubProblem("AI_CONFIGURATION_REQUIRED")
        site.write({"ai_enabled": True})
        site.kiosk_config_id.write({"integration_companion_ai_enabled": True})
        # Failure raises out of the action savepoint and rolls both flags back.
        summary, reply, mode = self.env["dojo.kiosk.service"]._followup_draft(
            site.kiosk_config_id, "Synthetic connection check: a guardian asks staff to review makeup class options.",
            source="verified_guardian")
        if mode != "AI-assisted draft - human review required":
            raise HubProblem("AI_CHECK_FAILED")
        return {"state": "ai_enabled", "model": settings["model"], "mode": mode,
                "checkedAt": iso(fields.Datetime.now()), "summary": summary, "reply": reply}
