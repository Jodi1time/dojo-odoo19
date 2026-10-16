"""
dojo_onboarding_wizard.py  (dojo_onboarding_stripe)
─────────────────────────────────────────────────────
Extends the onboarding wizard with a Stripe payment-capture step.

Step order after this module (without dojo_sign):
  guardian_contact → household → guardian_portal → payment  ← NEW
  → student_contact → member_details → enrollment → auto_enroll
  → subscription → student_portal → summary

The payment step lives in the guardian phase (per-household, not per-student).
Card is captured once during household setup. At student confirm time, the
subscription billing infrastructure (dojo_member_subscription_stripe)
auto-charges if a payment.token exists, or emails the invoice otherwise.

Skip logic:
  - Existing household WITH a saved card  → payment step skipped entirely
  - Existing household WITHOUT card        → payment step shown (optional)
  - New household                          → payment step always shown
  - Staff can check "Skip" to proceed without a card (invoice-only path)
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DojoOnboardingWizard(models.TransientModel):
    _inherit = 'dojo.onboarding.wizard'

    # ── Step selection — add 'payment' ────────────────────────────────────
    step = fields.Selection(
        selection_add=[('payment', 'Payment')],
        ondelete={'payment': 'set default'},
    )

    # ── Stripe payment capture state ──────────────────────────────────────
    stripe_client_secret = fields.Char(readonly=True)
    stripe_setup_intent_id = fields.Char(readonly=True)
    stripe_payment_method_id = fields.Char(readonly=True)
    stripe_card_display = fields.Char(readonly=True)
    stripe_customer_id = fields.Char(readonly=True)  # created in get_setup_intent
    payment_captured = fields.Boolean(default=False)
    skip_payment = fields.Boolean(string='Skip — proceed without saving a card', default=False)

    # ── Override step order: payment at end of guardian phase ──────────────
    @property
    def _STEP_ORDER(self):
        guardian_steps = list(self._GUARDIAN_STEPS) + ['payment']
        return guardian_steps + list(self._STUDENT_STEPS)

    # ── Step-skip logic ───────────────────────────────────────────────────
    def _should_skip_step(self, step_name):
        if step_name == 'payment':
            # Existing household that already has a card on file → skip
            if self.use_existing_household and self.household_id:
                if getattr(self.household_id, 'payment_token_count', 0) > 0:
                    return True
        return super()._should_skip_step(step_name)

    # ── Validation ────────────────────────────────────────────────────────
    def _validate_current_step(self):
        if self.step == 'payment':
            if not self.payment_captured and not self.skip_payment:
                raise UserError(_(
                    'Please save a payment method, or check '
                    '"Skip" to proceed without a card (invoices will be emailed).'
                ))
            return
        return super()._validate_current_step()

    # ── Navigation ────────────────────────────────────────────────────────
    def action_next(self):
        self.ensure_one()
        self._validate_current_step()

        # guardian_contact + existing household → use existing, then advance
        # to payment (or skip to student if card on file)
        if self.step == 'guardian_contact' and self.use_existing_household:
            self._use_existing_household()
            if self._should_skip_step('payment'):
                self._prefill_guardian_as_student_if_needed()
                self.wizard_phase = 'student'
                self.step = 'student_contact'
            else:
                self.step = 'payment'
            return self._reopen_wizard()

        # guardian_portal → create guardian + household, advance to payment
        if self.step == 'guardian_portal':
            self._create_guardian_and_household()
            self.step = 'payment'
            return self._reopen_wizard()

        # payment → attach card if captured, transition to student phase
        if self.step == 'payment':
            if self.payment_captured:
                self._attach_stripe_payment_method()
            self._prefill_guardian_as_student_if_needed()
            self.wizard_phase = 'student'
            self.step = 'student_contact'
            return self._reopen_wizard()

        return super().action_next()

    # ── Override student confirm: generate + charge invoice per student ───
    def action_confirm_student(self):
        """Run base confirm, then generate invoice.

        The subscription billing infrastructure (dojo_member_subscription_stripe)
        handles auto-charging when a payment.token exists on the household's
        guardian, or falls back to emailing the invoice.
        """
        result = super().action_confirm_student()

        member = self.created_member_id
        if not member:
            return result

        subscription = self.env['sale.subscription'].sudo().search(
            [('member_id', '=', member.id), ('state', 'in', ('active', 'pending'))],
            limit=1,
            order='create_date desc',
        )
        if subscription:
            try:
                subscription.action_generate_invoice()
                _logger.info(
                    "dojo_onboarding_stripe: invoice generated for member %s "
                    "(subscription %s, state=%s)",
                    member.id, subscription.id, subscription.state,
                )
            except Exception as exc:
                _logger.error(
                    "dojo_onboarding_stripe: failed to generate invoice for "
                    "member %s: %s", member.id, exc,
                )

        return result

    # ── Helpers ───────────────────────────────────────────────────────────
    def _get_stripe_provider(self):
        return self.env['payment.provider'].sudo().search(
            [('code', '=', 'stripe'), ('active', '=', True)],
            limit=1,
        )

    def _attach_stripe_payment_method(self):
        """Create an Odoo payment.token for the guardian from the captured Stripe PM.

        Called once at the end of the payment step (guardian phase) after the
        user has saved a card via the Stripe PaymentElement widget.
        The Stripe Customer was already created in get_setup_intent and the PM
        was attached to it by Stripe during stripe.confirmSetup().
        """
        self.ensure_one()

        provider = self._get_stripe_provider()
        if not provider:
            _logger.warning(
                "dojo_onboarding_stripe: No active Stripe provider — "
                "skipping payment token creation."
            )
            return

        household = self.created_household_id
        guardian = household.primary_guardian_id if household and household.is_household else None
        if not guardian:
            _logger.warning(
                "dojo_onboarding_stripe: No guardian on household — "
                "skipping."
            )
            return

        pm_id = self.stripe_payment_method_id
        cus_id = self.stripe_customer_id

        if not cus_id or not pm_id:
            _logger.warning(
                "dojo_onboarding_stripe: Missing stripe_customer_id or "
                "stripe_payment_method_id — skipping token creation."
            )
            return

        try:
            # Back-fill Odoo partner_id metadata and set default PM
            provider._send_api_request(
                'POST', f'customers/{cus_id}',
                data={
                    'metadata[odoo_partner_id]': str(guardian.id),
                    'invoice_settings[default_payment_method]': pm_id,
                },
            )
        except Exception as exc:
            _logger.warning(
                "dojo_onboarding_stripe: could not update customer metadata "
                "(cus=%s): %s", cus_id, exc
            )

        try:
            payment_method = self.env['payment.method'].sudo().search(
                [('code', '=', 'card'), ('provider_ids', 'in', [provider.id])],
                limit=1,
            )

            token_vals = {
                'provider_id': provider.id,
                'partner_id': guardian.id,
                'provider_ref': cus_id,
                'stripe_payment_method': pm_id,
                'active': True,
            }
            if payment_method:
                token_vals['payment_method_id'] = payment_method.id
            if self.stripe_card_display:
                token_vals['payment_details'] = self.stripe_card_display

            token = self.env['payment.token'].sudo().create(token_vals)
            _logger.info(
                "dojo_onboarding_stripe: created payment.token %s for "
                "guardian %s (cus=%s pm=%s)",
                token.id, guardian.name, cus_id, pm_id,
            )
        except Exception as exc:
            _logger.error(
                "dojo_onboarding_stripe: failed to create payment.token: %s",
                exc,
            )
