import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DojoConvertLeadWizard(models.TransientModel):
    """
    Convert a crm.lead into a dojo.member.
    Pre-fills member fields from the lead's partner data; creates the member +
    household (if needed) and optionally creates a subscription.
    Moves the lead to the Converted stage and archives it.
    """

    _name = "dojo.convert.lead.wizard"
    _description = "Convert CRM Lead to Dojang Member"

    # ------------------------------------------------------------------
    # Source
    # ------------------------------------------------------------------

    lead_id = fields.Many2one(
        "crm.lead",
        string="Lead",
        required=True,
        readonly=True,
        ondelete="cascade",
    )

    # ------------------------------------------------------------------
    # Member fields (pre-filled from lead partner)
    # ------------------------------------------------------------------

    first_name = fields.Char(string="First Name", required=True)
    last_name = fields.Char(string="Last Name")
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    mobile = fields.Char(string="Mobile")
    date_of_birth = fields.Date(string="Date of Birth")
    role = fields.Selection(
        [("student", "Student"), ("parent", "Parent / Guardian"), ("both", "Standalone")],
        string="Role",
        default="student",
        required=True,
        help="Student: trains at the dojo. Parent: guardian account only. Standalone: trains and is own guardian.",
    )

    # ------------------------------------------------------------------
    # Guardian / Household
    # ------------------------------------------------------------------

    create_household = fields.Boolean(
        string="Create New Household",
        default=True,
    )
    household_id = fields.Many2one(
        "res.partner",
        string="Existing Household",
        domain=[("is_household", "=", True)],
    )
    guardian_name = fields.Char(
        string="Guardian / Parent Name",
        help="Required when creating a new household for a student member.",
    )
    guardian_email = fields.Char(string="Guardian Email")
    guardian_mobile = fields.Char(string="Guardian Mobile / Phone")

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    create_subscription = fields.Boolean(
        string="Create Subscription",
        default=True,
    )
    plan_id = fields.Many2one(
        "dojo.subscription.plan",
        string="Subscription Plan",
        domain="[('active', '=', True)]",
    )
    subscription_start_date = fields.Date(
        string="Start Date",
        default=fields.Date.today,
    )

    # ------------------------------------------------------------------
    # Stripe payment capture
    # ------------------------------------------------------------------

    stripe_client_secret = fields.Char(readonly=True)
    stripe_setup_intent_id = fields.Char(readonly=True)
    stripe_payment_method_id = fields.Char(readonly=True)
    stripe_card_display = fields.Char(readonly=True)
    stripe_customer_id = fields.Char(readonly=True)
    payment_captured = fields.Boolean(default=False)
    skip_payment = fields.Boolean(
        string="Skip — proceed without saving a card",
        default=False,
    )

    # ------------------------------------------------------------------
    # Portal
    # ------------------------------------------------------------------

    create_portal_login = fields.Boolean(
        string="Create Portal Account",
        default=True,
        help="Grant the member (and guardian, if created) access to the member portal. "
             "An email will be sent for them to set their own password.",
    )

    # ------------------------------------------------------------------
    # Defaults from lead
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        lead_id = self.env.context.get("default_lead_id") or self.env.context.get("active_id")
        if lead_id:
            lead = self.env["crm.lead"].browse(lead_id)
            partner = lead.partner_id
            if partner:
                name_parts = (partner.name or "").split(" ", 1)
                res["first_name"] = name_parts[0]
                res["last_name"] = name_parts[1] if len(name_parts) > 1 else ""
                res["email"] = partner.email or lead.email_from or ""
                res["phone"] = partner.phone or lead.phone or ""
                res["mobile"] = getattr(partner, 'mobile', None) or ""
            else:
                name_parts = (lead.contact_name or lead.partner_name or "").split(" ", 1)
                res["first_name"] = name_parts[0]
                res["last_name"] = name_parts[1] if len(name_parts) > 1 else ""
                res["email"] = lead.email_from or ""
                res["phone"] = lead.phone or ""
                res["mobile"] = ""
            res["lead_id"] = lead.id
        return res

    # ------------------------------------------------------------------
    # Convert action
    # ------------------------------------------------------------------

    def action_convert(self):
        self.ensure_one()
        lead = self.lead_id

        if lead.dojo_member_id:
            raise UserError(_("This lead has already been converted to a member."))

        # ---- Build member name ----
        full_name = " ".join(filter(None, [self.first_name, self.last_name]))

        # ---- Resolve or create res.partner ----
        partner = lead.partner_id
        if not partner:
            partner = self.env["res.partner"].create(
                {
                    "name": full_name,
                    "email": self.email,
                    "phone": self.phone,
                    "company_type": "person",
                }
            )
        else:
            partner.write(
                {
                    "name": full_name,
                    "email": self.email or partner.email,
                    "phone": self.phone or partner.phone,
                }
            )

        # ---- Resolve / create household ----
        household = None
        guardian_partner = None
        # Standalone ('both') members are self-sufficient adults — no household/guardian needed.
        if self.create_household and self.role != "both":
            if not self.guardian_name and self.role == "student":
                raise UserError(
                    _("Please provide a guardian name when creating a household for a student.")
                )
            if self.guardian_name:
                guardian_partner = self.env["res.partner"].create(
                    {
                        "name": self.guardian_name,
                        "email": self.guardian_email,
                        "phone": self.guardian_mobile,
                        "company_type": "person",
                        "is_guardian": True,
                    }
                )
                household = self.env["res.partner"].create(
                    {
                        "name": f"{self.last_name or full_name} Household",
                        "is_household": True,
                        "is_company": True,
                        "primary_guardian_id": guardian_partner.id,
                    }
                )
                guardian_partner.write({"parent_id": household.id})
            else:
                household = self.env["res.partner"].create(
                    {
                        "name": f"{self.last_name or full_name} Household",
                        "is_household": True,
                        "is_company": True,
                    }
                )
        elif self.household_id and self.role != "both":
            household = self.household_id

        # ---- Create member or partner ----
        if self.role == "parent":
            # Pure guardian — create dojo.member so they can access the portal
            partner.write({
                "is_guardian": True,
            })
            if household:
                partner.write({"parent_id": household.id})
            if household and not household.primary_guardian_id:
                household.primary_guardian_id = partner.id
            member = self.env["dojo.member"].create({
                "partner_id": partner.id,
                "membership_state": "active",
            })
        else:
            member_vals = {
                "partner_id": partner.id,
                "date_of_birth": self.date_of_birth,
                "membership_state": "active",
            }
            member = self.env["dojo.member"].create(member_vals)
            if self.role == "both":
                partner.write({"is_guardian": True})
            if household:
                partner.write({"parent_id": household.id})
            if household and not household.primary_guardian_id:
                if self.role == "both":
                    household.primary_guardian_id = partner.id

        # ---- Create subscription ----
        if self.create_subscription and self.plan_id and member:
            plan = self.plan_id
            start = self.subscription_start_date or fields.Date.today()
            sub = self.env["sale.subscription"].create(
                {
                    "member_id": member.id,
                    "plan_id": plan.id,
                    "date_start": start,
                    "recurring_next_date": start,
                }
            )
            sub.action_set_active()

        # ---- Link back to lead ----
        lead.dojo_member_id = member.id if member else False
        lead.trial_attended = True

        # ---- Move lead to Won stage ----
        converted_stage = self.env["crm.stage"].search(
            [("name", "=", "Won")], limit=1
        )
        if converted_stage:
            lead.stage_id = converted_stage.id

        lead.active = False  # archive

        # ---- Attach Stripe payment method to household ----
        if self.payment_captured and self.stripe_payment_method_id and household:
            self._attach_stripe_payment_method(household)

        # ---- Copy trial waiver from lead to the new member ---------------
        if lead.lead_has_signed_waiver and member:
            try:
                member.sudo().apply_waiver(
                    signature=lead.lead_waiver_signature,
                    signed_by=lead.lead_waiver_signed_by,
                    signed_on=lead.lead_waiver_signed_on,
                )
            except Exception:
                _logger.warning(
                    "dojo_crm: could not copy waiver from lead %d to member %d",
                    lead.id,
                    member.id,
                    exc_info=True,
                )

        # ---- Grant portal access ----
        if self.create_portal_login:
            partners_to_invite = []
            if partner.email:
                partners_to_invite.append(partner)
            # Also invite the guardian if we just created them
            if self.create_household and guardian_partner and guardian_partner.email:
                partners_to_invite.append(guardian_partner)
            for p in partners_to_invite:
                try:
                    p._grant_portal_access_credentials()
                    user = self.env["res.users"].sudo().search(
                        [("partner_id", "=", p.id)], limit=1
                    )
                    if user:
                        user.sudo().action_reset_password()
                except Exception:
                    _logger.warning(
                        "dojo_crm: could not grant portal access to partner %d",
                        p.id,
                        exc_info=True,
                    )

        _logger.info(
            "dojo_crm: lead %d converted to member %d (%s)",
            lead.id,
            member.id,
            member.display_name,
        )

        # Open the new member record
        return {
            "type": "ir.actions.act_window",
            "name": _("New Member"),
            "res_model": "dojo.member",
            "res_id": member.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------
    # Stripe helpers
    # ------------------------------------------------------------------

    def _get_stripe_provider(self):
        return self.env["payment.provider"].sudo().search(
            [("code", "=", "stripe"), ("active", "=", True)],
            limit=1,
        )

    def _attach_stripe_payment_method(self, household):
        """Create an Odoo payment.token for the guardian from the captured Stripe PM."""
        self.ensure_one()

        provider = self._get_stripe_provider()
        if not provider:
            _logger.warning("dojo_crm: No active Stripe provider — skipping token creation.")
            return

        guardian = household.primary_guardian_id if household.is_household else None
        if not guardian:
            _logger.warning("dojo_crm: No guardian on household — skipping token creation.")
            return

        pm_id = self.stripe_payment_method_id
        cus_id = self.stripe_customer_id
        if not cus_id or not pm_id:
            _logger.warning("dojo_crm: Missing stripe IDs — skipping token creation.")
            return

        try:
            provider._send_api_request(
                "POST", f"customers/{cus_id}",
                data={
                    "metadata[odoo_partner_id]": str(guardian.id),
                    "invoice_settings[default_payment_method]": pm_id,
                },
            )
        except Exception as exc:
            _logger.warning(
                "dojo_crm: could not update Stripe customer metadata (cus=%s): %s",
                cus_id, exc,
            )

        try:
            payment_method = self.env["payment.method"].sudo().search(
                [("code", "=", "card"), ("provider_ids", "in", [provider.id])],
                limit=1,
            )
            token_vals = {
                "provider_id": provider.id,
                "partner_id": guardian.id,
                "provider_ref": cus_id,
                "stripe_payment_method": pm_id,
                "active": True,
            }
            if payment_method:
                token_vals["payment_method_id"] = payment_method.id
            if self.stripe_card_display:
                token_vals["payment_details"] = self.stripe_card_display

            token = self.env["payment.token"].sudo().create(token_vals)
            _logger.info(
                "dojo_crm: created payment.token %s for guardian %s (cus=%s pm=%s)",
                token.id, guardian.name, cus_id, pm_id,
            )
        except Exception as exc:
            _logger.error("dojo_crm: failed to create payment.token: %s", exc)
