"""
dojo_household_billing.py
──────────────────────────
Extends res.partner (household) with Stripe payment token integration.

Architecture (native payment_stripe)
──────────────────────────────────────
  res.partner (household)  ─►  payment.token  (via guardian partner_id)
                      └► provider_ref         = Stripe Customer ID (cus_…)
                      └► stripe_payment_method = Stripe PaymentMethod ID (pm_…)

  Charging:  payment.transaction (operation='offline')
             └► _send_payment_request() → Stripe PaymentIntent (off-session)

  The Stripe secret key is stored in payment.provider.stripe_secret_key
  (Settings → Payments → Stripe).
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class DojoHouseholdBilling(models.Model):
    _inherit = "res.partner"

    # ── Computed: count of saved payment tokens for the primary guardian ──
    payment_token_count = fields.Integer(
        string="Saved Payment Methods",
        compute="_compute_payment_token_count",
    )

    @api.depends("primary_guardian_id")
    def _compute_payment_token_count(self):
        for record in self:
            guardian = record.primary_guardian_id
            if not guardian:
                record.payment_token_count = 0
                continue
            provider = self.env["payment.provider"].sudo().search(
                [("code", "=", "stripe"), ("active", "=", True)],
                limit=1,
            )
            if not provider:
                record.payment_token_count = 0
                continue
            record.payment_token_count = self.env["payment.token"].sudo().search_count(
                [
                    ("provider_id", "=", provider.id),
                    ("partner_id", "=", guardian.id),
                    ("active", "=", True),
                ]
            )

    # ── Actions ──────────────────────────────────────────────────────────
    def action_view_payment_tokens(self):
        """Open payment tokens for the primary guardian of this household."""
        self.ensure_one()
        guardian = self.primary_guardian_id
        if not guardian:
            raise UserError(_("No primary guardian set on this household."))
        return {
            "type": "ir.actions.act_window",
            "name": "Payment Methods",
            "res_model": "payment.token",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", guardian.id)],
            "context": {"default_partner_id": guardian.id},
        }

    def action_add_update_card(self):
        """Open the inline Stripe card-capture modal for this household."""
        self.ensure_one()
        if not self.primary_guardian_id:
            raise UserError(
                _("Please set a primary guardian before adding a payment method.")
            )
        label = _("Update Payment Method") if self.payment_token_count else _("Add Payment Method")
        return {
            "type": "ir.actions.client",
            "tag": "dojo_stripe.HouseholdCardCapture",
            "name": label,
            "target": "new",
            "context": {"dialog_size": "medium"},
            "params": {
                "household_id": self.id,
                "guardian_name": self.primary_guardian_id.name or self.name,
                "has_existing_card": self.payment_token_count > 0,
            },
        }

    def action_charge_invoice(self, invoice):
        """Charge an open invoice using the guardian's saved Stripe payment token.

        Uses Odoo's native payment_stripe provider:
          - payment.provider  (code='stripe')  holds the Stripe secret key.
          - payment.token  holds cus_… (provider_ref) + pm_… (stripe_payment_method).
          - payment.transaction with operation='offline' triggers off-session charge.

        Args:
            invoice (account.move): Posted Odoo invoice to charge.

        Returns:
            payment.transaction: The created transaction record.
        """
        self.ensure_one()

        # ── Stripe provider ──────────────────────────────────────────────
        provider = self.env["payment.provider"].sudo().search(
            [("code", "=", "stripe"), ("active", "=", True)],
            limit=1,
        )
        if not provider:
            raise UserError(_("No active Stripe payment provider configured."))

        # ── Guardian ─────────────────────────────────────────────────────
        guardian = self.primary_guardian_id
        if not guardian:
            raise UserError(_("No primary guardian set on this household."))

        # ── Payment token ────────────────────────────────────────────────
        token = self.env["payment.token"].sudo().search(
            [
                ("provider_id", "=", provider.id),
                ("partner_id", "=", guardian.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        if not token:
            raise UserError(
                _(
                    "No saved Stripe payment method found for the guardian of this "
                    "household. They can add one via My Account → Payment Methods, "
                    "or use the 'Manage Payment Methods' button to send them a link."
                )
            )

        # ── Amount guard ─────────────────────────────────────────────────
        if invoice.amount_residual <= 0:
            raise UserError(
                _("Invoice %s has no outstanding balance.") % invoice.name
            )

        # ── Resolve payment_method (required NOT NULL in Odoo 19) ────────
        payment_method = token.payment_method_id
        if not payment_method:
            payment_method = self.env["payment.method"].sudo().search(
                [("code", "=", "card"), ("provider_ids", "in", [provider.id])],
                limit=1,
            )
        if not payment_method:
            raise UserError(
                _("Could not find a 'card' payment method for the Stripe provider. "
                  "Please check the Stripe provider configuration.")
            )

        # ── Create off-session transaction ───────────────────────────────
        reference = self.env["payment.transaction"].sudo()._compute_reference(
            provider.code,
            prefix=invoice.name or "INV",
        )
        tx = self.env["payment.transaction"].sudo().create(
            {
                "provider_id": provider.id,
                "payment_method_id": payment_method.id,
                "token_id": token.id,
                "operation": "offline",
                "amount": invoice.amount_residual,
                "currency_id": invoice.currency_id.id,
                "partner_id": guardian.id,
                "invoice_ids": [(4, invoice.id)],
                "reference": reference,
            }
        )
        tx._send_payment_request()
        return tx

