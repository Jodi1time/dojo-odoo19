"""
issuing_controller.py
─────────────────────
JSON-RPC endpoints for Stripe Issuing:
  • Reveal full card details (number, CVC, expiry) via Issuing Elements nonce
  • Digital wallet provisioning (Apple Pay / Google Pay ephemeral key)
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class IssuingController(http.Controller):

    # ── Reveal card details (Issuing Elements nonce) ───────────────────────
    @http.route(
        '/dojo/stripe/issuing/reveal',
        type='jsonrpc',
        auth='user',
        methods=['POST'],
    )
    def issuing_reveal(self, employee_id):
        """Return the data needed by Stripe Issuing Elements to display
        the full card number, CVC, and expiry in the browser.

        Requires: the logged-in user must be a Dojo admin or the employee
        linked to their own user.
        """
        employee = self._get_authorized_employee(int(employee_id))
        if not employee.stripe_card_id:
            return {'error': 'No Stripe Issuing card exists for this employee.'}

        stripe, api_key = employee._get_stripe_api()

        # Create an ephemeral key scoped to the issuing card – needed by
        # Stripe.js IssuingCardNumberDisplay / IssuingCardCvcDisplay.
        nonce = request.httprequest.headers.get('X-Stripe-Issuing-Nonce', '')
        if not nonce:
            return {'error': 'Missing Stripe Issuing nonce header.'}

        eph = stripe.EphemeralKey.create(
            issuing_card=employee.stripe_card_id,
            nonce=nonce,
            api_key=api_key,
        )

        provider = request.env['payment.provider'].sudo().search(
            [('code', '=', 'stripe'), ('active', '=', True)],
            limit=1,
        )

        return {
            'ephemeral_key_secret': eph['secret'],
            'stripe_card_id': employee.stripe_card_id,
            'publishable_key': provider.stripe_publishable_key if provider else '',
            'card_brand': employee.issuing_card_brand or '',
            'card_last4': employee.issuing_card_last4 or '',
            'card_expiry': employee.issuing_card_expiry or '',
            'card_status': employee.issuing_card_status or '',
        }

    # ── Digital wallet provisioning (Apple Pay / Google Pay) ───────────────
    @http.route(
        '/dojo/stripe/issuing/wallet',
        type='jsonrpc',
        auth='user',
        methods=['POST'],
    )
    def issuing_wallet(self, employee_id):
        """Return the ephemeral key + card ID needed by Stripe.js
        issuingCardPushProvisioning for Apple Pay / Google Pay.
        """
        employee = self._get_authorized_employee(int(employee_id))
        if not employee.stripe_card_id:
            return {'error': 'No Stripe Issuing card exists for this employee.'}

        data = employee.action_get_wallet_ephemeral_key()
        return data

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _get_authorized_employee(employee_id):
        """Fetch the employee record, checking the caller is allowed."""
        employee = request.env['hr.employee'].sudo().browse(employee_id)
        if not employee.exists():
            raise request.not_found()

        # Allow dojo admins unconditionally
        if request.env.user.has_group('dojo_core.group_dojo_admin'):
            return employee

        # Allow employees viewing their own record
        if employee.user_id and employee.user_id.id == request.env.uid:
            return employee

        raise request.not_found()
