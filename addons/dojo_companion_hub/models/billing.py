"""Reviewed invoice checkout using Odoo's native portal and reconciliation."""
import math
from urllib.parse import urlsplit

from odoo import models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import record_id, session_version
from .service import HubProblem, exact


class HubBilling(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _billing_invoice(self, site, grant, member_id, invoice_id):
        self._role(grant, "owner manager")
        member = self._member(site, grant, member_id)
        subscriptions = self.env["sale.subscription"].sudo().search([
            ("member_id", "=", member.id), ("company_id", "=", site.company_id.id)])
        invoice = (subscriptions.invoice_ids | subscriptions.household_invoice_ids).filtered(
            lambda row: row.id == record_id(invoice_id) and row.company_id == site.company_id
            and row.state == "posted" and row.move_type == "out_invoice")
        if not invoice:
            raise HubProblem("INVOICE_UNAVAILABLE", 404)
        return member, invoice

    def _payment_availability(self, invoice):
        if not self.env["ir.config_parameter"].sudo().get_bool("account_payment.enable_portal_payment"):
            return "PAYMENT_SETUP_REQUIRED"
        if invoice.move_type != "out_invoice" or invoice.state != "posted" or invoice.amount_residual <= 0:
            return "INVOICE_NOT_PAYABLE"
        if not invoice._has_to_be_paid():
            return "PAYMENT_PENDING_OR_SETTLED"
        providers = self.env["payment.provider"].sudo()._find_available_providers(
            invoice.company_id.id, invoice.partner_id.id, invoice.amount_residual,
            currency_id=invoice.currency_id.id).filtered(
                lambda provider: provider.is_published and provider.code != "none")
        # Odoo 20 uses active/is_live/is_published, not the old state field.
        methods = providers._find_available_payment_methods(
            invoice.partner_id.id, currency_id=invoice.currency_id.id)
        if not providers or not methods:
            return "PAYMENT_PROVIDER_UNAVAILABLE"
        return None

    def _payment_link(self, site, grant, payload):
        exact(payload, "memberId invoiceId expectedVersion expectedAmount")
        member, invoice = self._billing_invoice(site, grant, payload["memberId"], payload["invoiceId"])
        self._lock(invoice)
        amount = payload["expectedAmount"]
        if (type(payload["expectedVersion"]) is not int
                or type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0):
            raise HubProblem("INVALID_COMMAND", 400)
        if (payload["expectedVersion"] != session_version(invoice)
                or invoice.currency_id.compare_amounts(amount, invoice.amount_residual)):
            raise HubProblem("VERSION_CONFLICT")
        unavailable = self._payment_availability(invoice)
        if unavailable:
            raise HubProblem(unavailable)
        # The standard wizard signs the exact invoice/amount. It does not create
        # a payment, charge a token, email the invoice, or mark anything paid.
        wizard = self.env["payment.link.wizard"].sudo().with_company(site.company_id).with_context(
            active_model="account.move", active_id=invoice.id).create({"amount": invoice.amount_residual})
        url = wizard.link
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path.lstrip("/").rstrip("/") != "my/invoices/%s" % invoice.id):
            raise HubProblem("PAYMENT_URL_NOT_CONFIGURED")
        return {"memberId": str(member.id), "invoiceId": str(invoice.id), "invoiceNumber": invoice.name,
                "checkoutUrl": url, "amount": invoice.amount_residual, "currency": invoice.currency_id.name,
                "state": "checkout_ready", "paymentState": invoice.payment_state}
