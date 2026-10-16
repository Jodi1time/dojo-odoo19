"""
controllers/stripe_convert.py  (dojo_crm)
──────────────────────────────────────────
Two JSON endpoints used by the CrmConvertStripePayment OWL widget:

  POST /dojo/crm-convert/stripe/setup
    → Creates a Stripe SetupIntent, stores client_secret on the wizard.

  POST /dojo/crm-convert/stripe/confirm
    → Called after stripe.confirmSetup() succeeds in the browser.
      Stores card details on the wizard record.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CrmConvertStripeController(http.Controller):

    def _get_stripe_provider(self):
        return request.env["payment.provider"].sudo().search(
            [("code", "=", "stripe"), ("active", "=", True)],
            limit=1,
        )

    @http.route(
        "/dojo/crm-convert/stripe/setup",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def get_setup_intent(self, wizard_id, **kwargs):
        wizard = request.env["dojo.convert.lead.wizard"].browse(int(wizard_id)).sudo()
        if not wizard.exists():
            return {"error": "Wizard not found"}

        provider = self._get_stripe_provider()
        if not provider:
            return {"error": "No active Stripe provider configured. "
                             "Go to Settings → Payments → Stripe to enable it."}

        # Determine guardian info from wizard fields
        guardian_name = wizard.guardian_name or wizard.first_name or "New Member"
        guardian_email = wizard.guardian_email or wizard.email or ""
        guardian_phone = wizard.guardian_mobile or wizard.phone or ""

        # Create (or reuse) Stripe Customer
        cus_id = wizard.stripe_customer_id
        if not cus_id:
            try:
                customer = provider._send_api_request(
                    "POST", "customers",
                    data={
                        "name": guardian_name,
                        "email": guardian_email,
                        "phone": guardian_phone,
                        "metadata[wizard_id]": str(wizard.id),
                        "metadata[source]": "crm_convert",
                    },
                )
                cus_id = customer["id"]
                wizard.write({"stripe_customer_id": cus_id})
            except Exception as exc:
                _logger.error("Failed to create Stripe Customer: %s", exc)
                return {"error": str(exc)}

        # Create SetupIntent bound to customer
        try:
            setup_intent = provider._send_api_request(
                "POST", "setup_intents",
                data={
                    "customer": cus_id,
                    "usage": "off_session",
                    "payment_method_types[]": "card",
                },
            )
        except Exception as exc:
            _logger.error("Failed to create Stripe SetupIntent: %s", exc)
            return {"error": str(exc)}

        client_secret = setup_intent.get("client_secret", "")
        wizard.write({
            "stripe_setup_intent_id": setup_intent.get("id", ""),
            "stripe_client_secret": client_secret,
        })

        return {
            "client_secret": client_secret,
            "publishable_key": provider.stripe_publishable_key or "",
        }

    @http.route(
        "/dojo/crm-convert/stripe/confirm",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def confirm_payment_method(self, wizard_id, payment_method_id, **kwargs):
        wizard = request.env["dojo.convert.lead.wizard"].browse(int(wizard_id)).sudo()
        if not wizard.exists():
            return {"error": "Wizard not found"}

        provider = self._get_stripe_provider()
        if not provider:
            return {"error": "Stripe provider not configured"}

        brand = "Card"
        last4 = "••••"
        exp_month = ""
        exp_year = ""
        try:
            pm_data = provider._send_api_request(
                "GET", f"payment_methods/{payment_method_id}",
            )
            card = pm_data.get("card", {})
            brand = card.get("brand", "card").title()
            last4 = card.get("last4", "••••")
            exp_month = str(card.get("exp_month", "")).zfill(2)
            exp_year = str(card.get("exp_year", ""))[-2:]
        except Exception as exc:
            _logger.warning(
                "Could not retrieve PM details from Stripe (%s) — using placeholder.", exc
            )

        display = f"{brand} •••• {last4} {exp_month}/{exp_year}".strip()

        wizard.write({
            "stripe_payment_method_id": payment_method_id,
            "stripe_card_display": display,
            "payment_captured": True,
        })

        return {
            "success": True,
            "brand": brand,
            "last4": last4,
            "display": display,
        }
