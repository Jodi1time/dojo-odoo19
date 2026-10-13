from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRefundControls(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Refund program", "company_id": cls.company.id})
        plan = cls.env["dojo.subscription.plan"].create({"name": "Refund plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Refund active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].create({"name": "Refund price", "currency_id": cls.company.currency_id.id})
        cls.partner = cls.env["res.partner"].create({"name": "Refund payer", "company_id": cls.company.id})
        cls.member = cls.env["dojo.member"].create({"partner_id": cls.partner.id, "company_id": cls.company.id, "membership_state": "active"})
        cls.subscription = cls.env["sale.subscription"].create({"partner_id": cls.partner.id, "company_id": cls.company.id,
            "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": cls.member.id, "plan_id": plan.id})
        cls.income = cls.env["account.account"].create({"name": "Refund income", "code": "RFI1", "account_type": "income", "company_ids": [(6, 0, [cls.company.id])]})
        receivable = cls.env["account.account"].create({"name": "Refund recv", "code": "RFR1", "account_type": "asset_receivable", "reconcile": True, "company_ids": [(6, 0, [cls.company.id])]})
        cls.sales = cls.env["account.journal"].create({"name": "Refund sales", "code": "RFS1", "type": "sale", "company_id": cls.company.id, "default_account_id": cls.income.id})
        cls.cash = cls.env["account.journal"].search([("type", "=", "cash"), ("company_id", "=", cls.company.id)], limit=1) or cls.env["account.journal"].create(
            {"name": "Refund cash", "code": "RFC1", "type": "cash", "company_id": cls.company.id})
        cls.partner.with_company(cls.company).property_account_receivable_id = receivable
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Refund kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Refund test", "company_id": cls.company.id, "kiosk_config_id": cls.kiosk.id, "workspace_ref": "19"})
        cls.users = {}
        for role in ("owner", "manager"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Refund " + role,
                "login": "refund-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def invoice(self, paid=True):
        move = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": self.partner.id, "company_id": self.company.id,
            "journal_id": self.sales.id, "invoice_date": fields.Date.today(), "subscription_id": self.subscription.id,
            "invoice_line_ids": [(0, 0, {"name": "Tuition", "quantity": 1, "price_unit": 120, "account_id": self.income.id, "tax_ids": [(5, 0, 0)]})]})
        move.action_post()
        if paid:
            self.env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids).create({"journal_id": self.cash.id})._create_payments()
        return move

    def act(self, op, payload, role="manager", key=None):
        if key is None:
            self.keys += 1
            key = "refund-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def request(self, invoice, key=None):
        preview = self.read("refund_preview", {"memberId": str(self.member.id), "invoiceId": str(invoice.id)})
        return preview, self.act("request_refund", {"memberId": str(self.member.id), "invoiceId": str(invoice.id), "reason": "Moved away",
            "reviewToken": preview["reviewToken"]}, key=key)

    def test_ledger_shows_settlement_evidence_and_unpaid_cannot_be_refunded(self):
        paid, unpaid = self.invoice(), self.invoice(paid=False)
        ledger = {row["number"]: row for row in self.read("billing_ledger", {"memberId": str(self.member.id)})["invoices"]}
        self.assertEqual(ledger[paid.name]["settlementEvidence"], ["staff_recorded"])
        self.assertTrue(ledger[paid.name]["refundable"])
        self.assertFalse(ledger[unpaid.name]["settled"])
        self.assertEqual(self.read("refund_preview", {"memberId": str(self.member.id), "invoiceId": str(unpaid.id)})["error"]["code"], "REFUND_NOT_SETTLED")

    def test_refund_needs_owner_approval_posts_credit_note_and_records_desk_refund(self):
        invoice = self.invoice()
        preview, requested = self.request(invoice, key="refund-request-key-0001")
        self.assertEqual((preview["method"], preview["amount"]), ("manual", 120))
        self.assertEqual((requested["state"], requested["moneyReturned"], requested["creditNotePosted"]), ("refund_requested", False, False), requested)
        self.assertFalse(invoice.reversal_move_ids)
        replay = self.act("request_refund", {"memberId": str(self.member.id), "invoiceId": str(invoice.id), "reason": "Moved away",
            "reviewToken": preview["reviewToken"]}, key="refund-request-key-0001")
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.read("refund_preview", {"memberId": str(self.member.id), "invoiceId": str(invoice.id)})["error"]["code"], "REFUND_ALREADY_OPEN")
        row = self.read("billing_ledger", {"memberId": str(self.member.id)})["invoices"][0]["refunds"][0]
        approve = {"refundId": requested["refundId"], "expectedVersion": row["version"], "reason": "Owner checked"}
        self.assertEqual(self.act("approve_refund", approve)["error"]["status"], 403)
        approved = self.act("approve_refund", approve, role="owner")
        self.assertEqual((approved["state"], approved["next"], approved["moneyReturned"]), ("refund_approved", "awaiting_refund_payment", False), approved)
        credit = invoice.reversal_move_ids
        self.assertEqual((credit.state, credit.amount_total, credit.payment_state), ("posted", 120, "not_paid"))
        recorded = self.act("record_refund_payment", {"refundId": requested["refundId"], "expectedVersion": approved["version"], "reason": "Cash returned"})
        self.assertEqual((recorded["state"], recorded["moneyReturned"], recorded["evidence"]), ("refund_payment_recorded", True, "staff_recorded"), recorded)
        self.assertIn(credit.payment_state, ("paid", "in_payment"))
        refunds = self.read("billing_ledger", {"memberId": str(self.member.id)})["invoices"][0]
        self.assertEqual(refunds["refunds"][0]["state"], "refunded")
        self.assertFalse(refunds["refundable"])

    def test_cancelled_request_creates_no_accounting(self):
        invoice = self.invoice()
        _, requested = self.request(invoice)
        row = self.read("billing_ledger", {"memberId": str(self.member.id)})["invoices"][0]["refunds"][0]
        cancelled = self.act("cancel_refund", {"refundId": requested["refundId"], "expectedVersion": row["version"], "reason": "Customer stayed"})
        self.assertEqual(cancelled["state"], "refund_cancelled", cancelled)
        self.assertFalse(invoice.reversal_move_ids)
        self.assertEqual(self.act("approve_refund", {"refundId": requested["refundId"], "expectedVersion": cancelled["version"], "reason": "x"},
            role="owner")["error"]["code"], "REFUND_NOT_PENDING")
        self.assertTrue(self.request(invoice)[1]["refundId"])
