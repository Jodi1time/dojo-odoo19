"""
household_card_controller.py  (dojo_stripe)
────────────────────────────────────────────
JSONRPC endpoints for the Household "Add / Update Card" inline modal.

Flow:
  1. POST /dojo/stripe/household/setup
     → Creates (or reuses) a Stripe Customer for the household's primary
       guardian, creates a SetupIntent (usage=off_session), returns
       {client_secret, publishable_key, stripe_customer_id}.

  2. POST /dojo/stripe/household/confirm
     → After stripe.confirmSetup() succeeds in the browser:
       • Retrieves card details from Stripe (brand, last4, expiry).
       • Deactivates existing payment.token records for the guardian.
       • Creates a new payment.token (provider_ref = cus_...,
         stripe_payment_method = pm_...).
       Returns {success, display}.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class HouseholdCardController(http.Controller):

    def _get_stripe_provider(self):
        return request.env["payment.provider"].sudo().search(
            [("code", "=", "stripe"), ("active", "=", True)],
            limit=1,
        )

    # ── 1. Create SetupIntent ─────────────────────────────────────────────

    @http.route(
        "/dojo/stripe/household/setup",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def household_card_setup(self, household_id, **kwargs):
        """Find/create Stripe Customer for guardian and return a SetupIntent client_secret."""
        household = request.env["res.partner"].browse(int(household_id)).sudo()
        if not household.exists() or not household.is_household:
            return {"error": "Household not found."}

        guardian = household.primary_guardian_id
        if not guardian:
            return {
                "error": (
                    "No primary guardian is set on this household. "
                    "Please assign one before adding a card."
                )
            }

        provider = self._get_stripe_provider()
        if not provider:
            return {
                "error": (
                    "No active Stripe provider configured. "
                    "Go to Settings \u2192 Payments \u2192 Stripe to enable it."
                )
            }

        # ── Reuse existing Stripe Customer ID from saved payment.token ────
        existing_token = request.env["payment.token"].sudo().search(
            [
                ("provider_id", "=", provider.id),
                ("partner_id", "=", guardian.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        cus_id = existing_token.provider_ref if existing_token else None

        # Also check inactive tokens — reuse customer even if card was replaced
        if not cus_id:
            inactive_token = request.env["payment.token"].sudo().search(
                [
                    ("provider_id", "=", provider.id),
                    ("partner_id", "=", guardian.id),
                ],
                limit=1,
                order="id desc",
            )
            cus_id = inactive_token.provider_ref if inactive_token else None

        if not cus_id:
            # Create a new Stripe Customer bound to this guardian
            try:
                customer = provider._send_api_request(
                    "POST",
                    "customers",
                    data={
                        "name": guardian.name or "Member Guardian",
                        "email": guardian.email or "",
                        "phone": guardian.phone or "",
                        "metadata[odoo_partner_id]": str(guardian.id),
                        "metadata[household_id]": str(household.id),
                    },
                )
                cus_id = customer["id"]
            except Exception as exc:
                _logger.error(
                    "Failed to create Stripe Customer for household %s: %s",
                    household.id,
                    exc,
                )
                return {"error": str(exc)}

        # ── Create SetupIntent bound to this customer ─────────────────────
        try:
            setup_intent = provider._send_api_request(
                "POST",
                "setup_intents",
                data={
                    "customer": cus_id,
                    "usage": "off_session",
                    "payment_method_types[]": "card",
                },
            )
        except Exception as exc:
            _logger.error(
                "Failed to create SetupIntent for household %s: %s",
                household.id,
                exc,
            )
            return {"error": str(exc)}

        return {
            "client_secret": setup_intent.get("client_secret", ""),
            "publishable_key": provider.stripe_publishable_key or "",
            "stripe_customer_id": cus_id,
        }

    # ── 2. Store confirmed PaymentMethod ──────────────────────────────────

    @http.route(
        "/dojo/stripe/household/confirm",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def household_card_confirm(
        self, household_id, payment_method_id, stripe_customer_id, **kwargs
    ):
        """Deactivate old tokens and create a new payment.token for the guardian."""
        household = request.env["res.partner"].browse(int(household_id)).sudo()
        if not household.exists() or not household.is_household:
            return {"error": "Household not found."}

        guardian = household.primary_guardian_id
        if not guardian:
            return {"error": "No primary guardian on household."}

        provider = self._get_stripe_provider()
        if not provider:
            return {"error": "Stripe provider not configured."}

        # ── Retrieve card display details from Stripe ────────────────────
        brand, last4, exp_month, exp_year = "Card", "\u2022\u2022\u2022\u2022", "", ""
        try:
            pm_data = provider._send_api_request(
                "GET", f"payment_methods/{payment_method_id}"
            )
            card = pm_data.get("card", {})
            brand = card.get("brand", "card").title()
            last4 = card.get("last4", "\u2022\u2022\u2022\u2022")
            exp_month = str(card.get("exp_month", "")).zfill(2)
            exp_year = str(card.get("exp_year", ""))[-2:]
        except Exception as exc:
            _logger.warning(
                "Could not retrieve PM details from Stripe (%s) — using placeholder.", exc
            )

        display = f"{brand} \u2022\u2022\u2022\u2022 {last4} {exp_month}/{exp_year}".strip()

        # ── Update Stripe Customer: set default PM + back-fill metadata ──
        try:
            provider._send_api_request(
                "POST",
                f"customers/{stripe_customer_id}",
                data={
                    "metadata[odoo_partner_id]": str(guardian.id),
                    "invoice_settings[default_payment_method]": payment_method_id,
                },
            )
        except Exception as exc:
            _logger.warning("Could not update Stripe Customer metadata: %s", exc)

        # ── Deactivate all existing active tokens for this guardian ───────
        old_tokens = request.env["payment.token"].sudo().search(
            [
                ("provider_id", "=", provider.id),
                ("partner_id", "=", guardian.id),
                ("active", "=", True),
            ]
        )
        if old_tokens:
            old_tokens.write({"active": False})

        # ── Create new payment.token ──────────────────────────────────────
        payment_method = request.env["payment.method"].sudo().search(
            [("code", "=", "card"), ("provider_ids", "in", [provider.id])],
            limit=1,
        )
        token_vals = {
            "provider_id": provider.id,
            "partner_id": guardian.id,
            "provider_ref": stripe_customer_id,
            "stripe_payment_method": payment_method_id,
            "active": True,
            "payment_details": display,
        }
        if payment_method:
            token_vals["payment_method_id"] = payment_method.id

        try:
            token = request.env["payment.token"].sudo().create(token_vals)
            _logger.info(
                "Created payment.token %s for household %s (guardian: %s, cus=%s, pm=%s)",
                token.id,
                household.id,
                guardian.name,
                stripe_customer_id,
                payment_method_id,
            )
        except Exception as exc:
            _logger.error("Failed to create payment.token: %s", exc)
            return {
                "error": (
                    f"Card was saved in Stripe but could not be recorded in Odoo: {exc}"
                )
            }

        return {"success": True, "display": display}
