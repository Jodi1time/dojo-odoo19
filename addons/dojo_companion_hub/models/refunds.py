from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, fingerprint, text

OPEN = ("awaiting_approval", "credit_note_posted")


class HubRefund(models.Model):
    _name = "dojo.hub.refund"
    _description = "Companion Reviewed Refund"
    _order = "id desc"

    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict")
    invoice_id = fields.Many2one("account.move", required=True, ondelete="restrict", index=True)
    credit_note_id = fields.Many2one("account.move", ondelete="restrict")
    refund_payment_id = fields.Many2one("account.payment", ondelete="restrict")
    transaction_id = fields.Many2one("payment.transaction", ondelete="restrict")
    amount = fields.Float(required=True)
    currency = fields.Char(required=True)
    method = fields.Selection([("provider", "Online payment provider"), ("manual", "Recorded at the desk")], required=True)
    state = fields.Selection([("awaiting_approval", "Awaiting owner approval"), ("credit_note_posted", "Credit note posted"),
        ("refunded", "Refunded"), ("cancelled", "Cancelled")], required=True, default="awaiting_approval")
    requested_by = fields.Many2one("res.users", required=True, ondelete="restrict")
    approved_by = fields.Many2one("res.users", ondelete="restrict")
    approved_at = fields.Datetime()
    note = fields.Text()
    _one_open_refund = models.UniqueIndex("(invoice_id) WHERE state IN ('awaiting_approval', 'credit_note_posted')")


class HubRefunds(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "billing_ledger": {"handler": self._billing_ledger, "roles": "owner manager", "write": False},
            "refund_preview": {"handler": self._refund_preview, "roles": "owner manager", "write": False},
            "request_refund": {"handler": self._request_refund, "roles": "owner manager", "write": True, "replay": self._replay_company_member},
            "approve_refund": {"handler": self._approve_refund, "roles": "owner", "write": True, "replay": self._replay_company_member},
            "record_refund_payment": {"handler": self._record_refund_payment, "roles": "owner manager", "write": True, "replay": self._replay_company_member},
            "cancel_refund": {"handler": self._cancel_refund, "roles": "owner manager", "write": True, "replay": self._replay_company_member},
        })
        return operations

    def _member_invoices(self, site, member):
        subscriptions = self.env["sale.subscription"].sudo().search([("member_id", "=", member.id), ("company_id", "=", site.company_id.id)])
        return (subscriptions.invoice_ids | subscriptions.household_invoice_ids).filtered(
            lambda i: i.company_id == site.company_id and i.state == "posted" and i.move_type == "out_invoice").sorted("id", reverse=True)[:50]

    def _payments(self, invoice):
        rows = []
        for payment in invoice.sudo()._get_reconciled_payments():
            tx = payment.payment_transaction_id if "payment_transaction_id" in payment._fields else self.env["payment.transaction"]
            rows.append({"id": str(payment.id), "amount": payment.amount, "date": str(payment.date), "journal": payment.journal_id.name,
                "evidence": "provider_receipt" if tx and tx.state == "done" and tx.provider_reference else "staff_recorded",
                "provider": tx.provider_id.name if tx else None, "providerReference": tx.provider_reference if tx else None,
                "transactionState": tx.state if tx else None, "transactionId": tx.id if tx else None})
        return rows

    def _refund_state(self, refund):
        if refund.state != "credit_note_posted":
            return refund.state
        if refund.method == "provider" and refund.transaction_id:
            done = self.env["payment.transaction"].sudo().search_count([("source_transaction_id", "=", refund.transaction_id.id),
                ("operation", "=", "refund"), ("state", "=", "done")])
            return "refunded_by_provider" if done else "awaiting_provider_refund"
        return "awaiting_refund_payment"

    def _refund_row(self, refund):
        return {"id": str(refund.id), "invoiceId": str(refund.invoice_id.id), "amount": refund.amount, "currency": refund.currency,
            "method": refund.method, "state": self._refund_state(refund), "creditNote": refund.credit_note_id.name or None,
            "requestedBy": refund.requested_by.name, "approvedBy": refund.approved_by.name or None, "approvedAt": iso(refund.approved_at),
            "refundPayment": refund.refund_payment_id.name or None, "version": self._version(refund)}

    def _billing_ledger(self, site, grant, p):
        exact(p, "memberId")
        member = self._member(site, grant, p["memberId"])
        rows = []
        for invoice in self._member_invoices(site, member):
            payments = self._payments(invoice)
            refunds = self.env["dojo.hub.refund"].sudo().search([("site_id", "=", site.id), ("invoice_id", "=", invoice.id)])
            settled = invoice.payment_state in ("paid", "in_payment")
            rows.append({"id": str(invoice.id), "number": invoice.name, "date": str(invoice.invoice_date) if invoice.invoice_date else None,
                "total": invoice.amount_total, "remaining": invoice.amount_residual, "currency": invoice.currency_id.name,
                "paymentState": invoice.payment_state, "settled": settled,
                "settlementEvidence": sorted({row["evidence"] for row in payments}) if settled else [],
                "payments": payments, "refunds": [self._refund_row(r) for r in refunds],
                "refundable": settled and not refunds.filtered(lambda r: r.state in OPEN + ("refunded",)), "version": self._version(invoice)})
        return {"memberId": str(member.id), "invoices": rows, "canApprove": grant.role == "owner"}

    def _refund_plan(self, site, grant, p):
        member = self._member(site, grant, p["memberId"])
        invoice = self._member_invoices(site, member).filtered(lambda i: i.id == record_id(p["invoiceId"]))
        if not invoice:
            raise HubProblem("INVOICE_UNAVAILABLE", 404)
        if invoice.payment_state not in ("paid", "in_payment"):
            raise HubProblem("REFUND_NOT_SETTLED", 422)
        if self.env["dojo.hub.refund"].sudo().search_count([("invoice_id", "=", invoice.id), ("state", "in", list(OPEN) + ["refunded"])]):
            raise HubProblem("REFUND_ALREADY_OPEN")
        if invoice.reversal_move_ids.filtered(lambda m: m.state != "cancel"):
            raise HubProblem("REFUND_ALREADY_CREDITED")
        payments = self._payments(invoice)
        provider = [row for row in payments if row["evidence"] == "provider_receipt"]
        summary = {"invoiceId": str(invoice.id), "number": invoice.name, "amount": invoice.amount_total, "currency": invoice.currency_id.name,
            "method": "provider" if provider else "manual", "payments": payments}
        token = fingerprint({"summary": summary, "version": self._version(invoice), "user": self.env.uid, "site": site.id})
        return member, invoice, summary, token, provider

    def _refund_preview(self, site, grant, p):
        exact(p, "memberId invoiceId")
        _, invoice, summary, token, _ = self._refund_plan(site, grant, p)
        return {**summary, "version": self._version(invoice), "reviewToken": token,
            "effects": ["A full credit note is posted only after an owner approves.",
                "Online payments: the money is returned only when the payment provider confirms the refund." if summary["method"] == "provider"
                else "Desk payments: return the money, then record the refund payment here."]}

    def _request_refund(self, site, grant, p):
        exact(p, "memberId invoiceId reason reviewToken")
        reason = text(p["reason"], 500)
        member, invoice, summary, token, provider = self._refund_plan(site, grant, p)
        if p["reviewToken"] != token:
            raise HubProblem("REFUND_REVIEW_CHANGED")
        self._lock(invoice)
        refund = self.env["dojo.hub.refund"].sudo().create({"site_id": site.id, "member_id": member.id, "invoice_id": invoice.id,
            "amount": summary["amount"], "currency": summary["currency"], "method": summary["method"],
            "transaction_id": provider[0]["transactionId"] if provider else False, "requested_by": self.env.uid,
            "note": "%s %s requested: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)})
        return {"memberId": str(member.id), "refundId": str(refund.id), "state": "refund_requested", "amount": refund.amount,
            "currency": refund.currency, "moneyReturned": False, "creditNotePosted": False}

    def _refund(self, site, value):
        refund = self.env["dojo.hub.refund"].sudo().search([("id", "=", record_id(value)), ("site_id", "=", site.id)], limit=1)
        if not refund:
            raise HubProblem("REFUND_UNAVAILABLE", 404)
        return refund

    def _approve_refund(self, site, grant, p):
        exact(p, "refundId expectedVersion reason")
        reason = text(p["reason"], 500)
        refund = self._refund(site, p["refundId"])
        self._lock(refund)
        self._require_version(refund, p["expectedVersion"])
        if refund.state != "awaiting_approval":
            raise HubProblem("REFUND_NOT_PENDING")
        invoice = refund.invoice_id.sudo()
        self._lock(invoice)
        if invoice.payment_state not in ("paid", "in_payment") or invoice.reversal_move_ids.filtered(lambda m: m.state != "cancel"):
            raise HubProblem("REFUND_NOT_SETTLED", 422)
        credit = invoice.with_company(site.company_id)._reverse_moves([{"ref": "Refund of %s" % invoice.name,
            "invoice_date": fields.Date.context_today(self), "date": fields.Date.context_today(self)}])
        credit.action_post()
        refund.write({"state": "credit_note_posted", "credit_note_id": credit.id, "approved_by": self.env.uid,
            "approved_at": fields.Datetime.now(), "note": "\n".join([refund.note or "", "%s %s approved: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)])})
        return {"memberId": str(refund.member_id.id), "refundId": str(refund.id), "state": "refund_approved", "creditNote": credit.name,
            "next": self._refund_state(refund), "moneyReturned": False, "creditNotePosted": True, "version": self._version(refund)}

    def _record_refund_payment(self, site, grant, p):
        exact(p, "refundId expectedVersion reason")
        reason = text(p["reason"], 500)
        refund = self._refund(site, p["refundId"])
        self._lock(refund)
        self._require_version(refund, p["expectedVersion"])
        if refund.state != "credit_note_posted" or refund.method != "manual":
            raise HubProblem("REFUND_PAYMENT_NOT_ALLOWED")
        credit = refund.credit_note_id.sudo()
        if credit.payment_state in ("paid", "in_payment") or credit.amount_residual <= 0:
            raise HubProblem("REFUND_PAYMENT_NOT_ALLOWED")
        original = self.env["account.payment"].sudo().browse([int(row["id"]) for row in self._payments(refund.invoice_id)])[:1]
        journal = original.journal_id if original and original.journal_id.type in ("cash", "bank") else self.env["account.journal"].sudo().search(
            [("type", "in", ["cash", "bank"]), ("company_id", "=", site.company_id.id)], order="type", limit=1)
        if not journal:
            raise HubProblem("REFUND_JOURNAL_REQUIRED", 422)
        wizard = self.env["account.payment.register"].sudo().with_company(site.company_id).with_context(
            active_model="account.move", active_ids=credit.ids).create({"journal_id": journal.id, "communication": "Refund %s" % credit.name})
        payment = wizard._create_payments()
        refund.write({"state": "refunded", "refund_payment_id": payment.id,
            "note": "\n".join([refund.note or "", "%s %s recorded refund payment: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)])})
        return {"memberId": str(refund.member_id.id), "refundId": str(refund.id), "state": "refund_payment_recorded",
            "payment": payment.name, "journal": journal.name, "moneyReturned": True, "evidence": "staff_recorded", "version": self._version(refund)}

    def _cancel_refund(self, site, grant, p):
        exact(p, "refundId expectedVersion reason")
        reason = text(p["reason"], 500)
        refund = self._refund(site, p["refundId"])
        self._lock(refund)
        self._require_version(refund, p["expectedVersion"])
        if refund.state != "awaiting_approval":
            raise HubProblem("REFUND_NOT_PENDING")
        refund.write({"state": "cancelled", "note": "\n".join([refund.note or "", "%s %s cancelled: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)])})
        return {"memberId": str(refund.member_id.id), "refundId": str(refund.id), "state": "refund_cancelled", "moneyReturned": False,
            "version": self._version(refund)}
