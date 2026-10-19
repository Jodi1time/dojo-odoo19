"""
controllers/main.py  (dojo_onboarding_stripe)
──────────────────────────────────────────────
Two JSON endpoints used by the OnboardingStripePayment OWL widget:

  POST /dojo/onboarding/stripe/setup
    → Creates a Stripe SetupIntent, stores client_secret on the wizard,
      returns {client_secret, publishable_key}.

  POST /dojo/onboarding/stripe/confirm
    → Called after stripe.confirmSetup() succeeds in the browser.
      Retrieves card details from Stripe, stores (stripe_payment_method_id,
      stripe_card_display, payment_captured) on the wizard.
      Returns {success, brand, last4, display}.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class OnboardingStripeController(http.Controller):

    # ── Helper ────────────────────────────────────────────────────────────
    def _get_stripe_provider(self):
        return request.env['payment.provider'].sudo().search(
            [('code', '=', 'stripe'), ('active', '=', True)],
            limit=1,
        )

    # ── 1. Create SetupIntent ─────────────────────────────────────────────
    @http.route(
        '/dojo/onboarding/stripe/setup',
        type='jsonrpc', auth='user', methods=['POST'],
    )
    def get_setup_intent(self, wizard_id, **kwargs):
        """
        1. Create a Stripe Customer for the guardian (using the wizard's current
           guardian data) — this avoids a separate attach call later.
        2. Create a SetupIntent bound to that customer so that Stripe
           automatically attaches the confirmed PM to the customer when
           stripe.confirmSetup() resolves in the browser.

        Returns:
            client_secret   – passed to stripe.elements({clientSecret})
            publishable_key – used to initialise Stripe.js
        """
        wizard = request.env['dojo.onboarding.wizard'].browse(int(wizard_id)).sudo()
        if not wizard.exists():
            return {'error': 'Wizard not found'}

        provider = self._get_stripe_provider()
        if not provider:
            return {'error': 'No active Stripe provider configured. '
                             'Go to Settings → Payments → Stripe to enable it.'}

        # ── Determine guardian info from wizard fields ───────────────────
        guardian_name = wizard.guardian_name or 'New Member'
        guardian_email = wizard.guardian_email or ''
        guardian_phone = wizard.guardian_phone or ''

        # If using existing household, pull from the guardian partner
        if wizard.use_existing_household and wizard.created_guardian_partner_id:
            gp = wizard.created_guardian_partner_id
            guardian_name = gp.name or guardian_name
            guardian_email = gp.email or guardian_email
            guardian_phone = gp.phone or guardian_phone

        # ── Create (or reuse) Stripe Customer ────────────────────────────
        cus_id = wizard.stripe_customer_id
        if not cus_id:
            try:
                customer = provider._send_api_request(
                    'POST', 'customers',
                    data={
                        'name': guardian_name,
                        'email': guardian_email,
                        'phone': guardian_phone,
                        'metadata[wizard_id]': str(wizard.id),
                    },
                )
                cus_id = customer['id']
                wizard.write({'stripe_customer_id': cus_id})
            except Exception as exc:
                _logger.error("Failed to create Stripe Customer: %s", exc)
                return {'error': str(exc)}

        # ── Create SetupIntent bound to the customer ─────────────────────
        try:
            setup_intent = provider._send_api_request(
                'POST', 'setup_intents',
                data={
                    'customer': cus_id,
                    'usage': 'off_session',
                    'payment_method_types[]': 'card',
                },
            )
        except Exception as exc:
            _logger.error("Failed to create Stripe SetupIntent: %s", exc)
            return {'error': str(exc)}

        client_secret = setup_intent.get('client_secret', '')
        wizard.write({
            'stripe_setup_intent_id': setup_intent.get('id', ''),
            'stripe_client_secret': client_secret,
        })

        return {
            'client_secret': client_secret,
            'publishable_key': provider.stripe_publishable_key or '',
        }

    # ── 2. Store confirmed PaymentMethod ──────────────────────────────────
    @http.route(
        '/dojo/onboarding/stripe/confirm',
        type='jsonrpc', auth='user', methods=['POST'],
    )
    def confirm_payment_method(self, wizard_id, payment_method_id, **kwargs):
        """
        After stripe.confirmSetup() resolves in the browser:
          1. Retrieve the PaymentMethod from Stripe to get card details.
          2. Write stripe_payment_method_id + stripe_card_display +
             payment_captured on the wizard.

        Returns: {success, brand, last4, display}
        """
        wizard = request.env['dojo.onboarding.wizard'].browse(int(wizard_id)).sudo()
        if not wizard.exists():
            return {'error': 'Wizard not found'}

        provider = self._get_stripe_provider()
        if not provider:
            return {'error': 'Stripe provider not configured'}

        # Retrieve card details for a friendly display name
        brand = 'Card'
        last4 = '••••'
        exp_month = ''
        exp_year = ''
        try:
            pm_data = provider._send_api_request(
                'GET', f'payment_methods/{payment_method_id}',
            )
            card = pm_data.get('card', {})
            brand = card.get('brand', 'card').title()
            last4 = card.get('last4', '••••')
            exp_month = str(card.get('exp_month', '')).zfill(2)
            exp_year = str(card.get('exp_year', ''))[-2:]  # last 2 digits
        except Exception as exc:
            _logger.warning(
                "Could not retrieve PM details from Stripe (%s) — "
                "using placeholder display.", exc
            )

        display = f"{brand} •••• {last4} {exp_month}/{exp_year}".strip()

        wizard.write({
            'stripe_payment_method_id': payment_method_id,
            'stripe_card_display': display,
            'payment_captured': True,
        })

        return {
            'success': True,
            'brand': brand,
            'last4': last4,
            'display': display,
        }
