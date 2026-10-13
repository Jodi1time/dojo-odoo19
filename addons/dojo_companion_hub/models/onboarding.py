from odoo import fields, models
from .service import exact


class HubOnboarding(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "setup_checklist": {"handler": self._setup_checklist, "roles": "owner manager", "write": False},
            "member_onboarding": {"handler": self._member_onboarding, "roles": "owner manager", "write": False},
        })
        return operations

    def _item(self, key, label, done, detail, where):
        return {"key": key, "label": label, "done": bool(done), "detail": detail, "where": where}

    def _setup_checklist(self, site, grant, p):
        exact(p, "")
        company = site.company_id
        now = fields.Datetime.now()
        kiosk = site.kiosk_config_id
        templates = self.env["dojo.class.template"].sudo().search_count([("company_id", "=", company.id), ("active", "=", True)])
        upcoming = self.env["dojo.class.session"].sudo().search_count([("company_id", "=", company.id), ("start_datetime", ">=", now), ("state", "=", "open")])
        plans = self.env["dojo.subscription.plan"].sudo().search_count([("company_id", "=", company.id), ("active", "=", True)])
        ranks = self.env["dojo.belt.rank"].sudo().search_count([("company_id", "in", [company.id, False]), ("active", "=", True)])
        waiver = self.env["dojo.waiver.config"].sudo().search([], limit=1)
        grants = self.env["dojo.hub.grant"].sudo().search([("site_id", "=", site.id)])
        roles = set(grants.mapped("role"))
        portal = self.env["ir.config_parameter"].sudo().get_bool("account_payment.enable_portal_payment")
        providers = self.env["payment.provider"].sudo().search_count([("company_id", "=", company.id), ("is_published", "=", True),
            ("code", "!=", "none")]) if "payment.provider" in self.env else 0
        guardian_pins = self.env["dojo.kiosk.guardian.pin"].sudo().search_count([("company_id", "=", company.id), ("active", "=", True)])
        items = [
            self._item("school", "School details and time zone", company.partner_id.tz and company.country_id,
                "Time zone %s · country %s" % (company.partner_id.tz or "not set", company.country_id.name or "not set"), "Odoo › Settings › Companies"),
            self._item("staff", "Staff access", {"owner", "manager"} & roles,
                ", ".join("%s %s" % (sum(1 for g in grants if g.role == r), r) for r in sorted(roles)) or "No staff access granted", "Odoo › Companion hub grants"),
            self._item("classes", "Classes and schedule", templates and upcoming, "%s classes · %s upcoming sessions" % (templates, upcoming), "Odoo › Dojo › Classes"),
            self._item("plans", "Membership plans", plans, "%s active plans" % plans, "Odoo › Dojo › Subscription plans"),
            self._item("ranks", "Belt ranks", ranks, "%s active ranks" % ranks, "Odoo › Dojo › Belt ranks"),
            self._item("waiver", "Waiver text", waiver and waiver.content_html, waiver.name if waiver else "Not configured", "Odoo › Dojo › Configuration › Waiver text"),
            self._item("kiosk", "Check-in kiosk", kiosk and kiosk.active and kiosk.integration_enabled,
                "%s%s" % (kiosk.name or "No kiosk linked", " · connected" if kiosk.integration_enabled else " · not connected"), "Odoo › Dojo › Kiosk"),
            self._item("guardian_pins", "Parent kiosk PINs", guardian_pins, "%s parents can check in with a PIN" % guardian_pins, "Hub › member › Guardian kiosk access"),
            self._item("payments", "Online payments", portal and providers,
                "Portal payments %s · %s published providers" % ("on" if portal else "off", providers), "Odoo › Invoicing › Payment providers"),
            self._item("messaging", "Messaging provider", site.outbound_enabled and site.provider_origin and site._secret("provider_token_env"),
                "Connected" if site.outbound_enabled and site.provider_origin else "Not connected: broadcasts stay queued", "Server configuration"),
        ]
        return {"items": items, "done": sum(1 for i in items if i["done"]), "total": len(items)}

    def _member_onboarding(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        partner = member.partner_id
        household = partner.parent_id if partner.parent_id.is_household else self.env["res.partner"]
        guardians = self._household_guardians(site, member) if household else self.env["res.partner"]
        contact = partner if not partner.is_minor else household.primary_guardian_id
        subscriptions = self.env["sale.subscription"].sudo().search([("member_id", "=", member.id), ("company_id", "=", site.company_id.id)])
        booked = self.env["dojo.class.enrollment"].sudo().search_count([("member_id", "=", member.id), ("status", "=", "registered")])
        attended = self.env["dojo.attendance.log"].sudo().search_count([("member_id", "=", member.id), ("status", "in", ["present", "late"])])
        pins = self.env["dojo.kiosk.guardian.pin"].sudo().search_count([("company_id", "=", site.company_id.id), ("partner_id", "in", guardians.ids),
            ("active", "=", True)])
        consents = self.env["dojo.hub.consent"].sudo().search_count([("site_id", "=", site.id), ("partner_id", "in", (partner | guardians).ids)])
        items = [
            self._item("profile", "Student profile", partner.name and member.member_number, member.member_number or "No member number", "Profile"),
            self._item("contact", "Reachable contact", contact and (contact.phone or contact.email),
                "%s · %s" % (contact.name, "phone or email on file" if contact.phone or contact.email else "no phone or email") if contact else "No contact person", "Contact details"),
            self._item("guardian", "Household guardian", guardians or not partner.is_minor,
                ", ".join(guardians.mapped("name")) or ("Not needed for an adult" if not partner.is_minor else "Minor without a linked guardian"), "Household and guardians"),
            self._item("waiver", "Signed waiver", member.waiver_state == "signed", member.waiver_signed_by or "Not signed", "Waiver signing"),
            self._item("membership", "Membership", subscriptions.filtered(lambda s: s.state in ("active", "pending")),
                ", ".join("%s (%s)" % (s.plan_id.name or "Plan", s.state) for s in subscriptions[:3]) or "No membership", "Membership and billing"),
            self._item("class", "First class booked or attended", booked or attended, "%s booked · %s attended" % (booked, attended), "Book a class"),
            self._item("consent", "Messaging consent recorded", consents, "%s consent records" % consents, "Messaging & consent"),
            self._item("kiosk", "Parent kiosk PIN", pins or not partner.is_minor, "%s active PINs" % pins if partner.is_minor else "Not needed for an adult", "Guardian kiosk access"),
        ]
        return {"memberId": str(member.id), "items": items, "done": sum(1 for i in items if i["done"]), "total": len(items)}
