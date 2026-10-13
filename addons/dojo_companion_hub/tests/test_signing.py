import base64
import io

from PIL import Image, ImageDraw
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


def signature(blank=False, size=(600, 180)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    if not blank:
        ImageDraw.Draw(image).line([(20, 120), (200, 40), (380, 140), (560, 60)], fill=(0, 0, 0, 255), width=4)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


@tagged("post_install", "-at_install")
class TestWaiverSigning(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        household = cls.env["res.partner"].create({"name": "Family Sign", "is_household": True, "company_id": cls.company.id})
        cls.guardian = cls.env["res.partner"].create({"name": "Sign Parent", "parent_id": household.id, "is_guardian": True, "company_id": cls.company.id})
        household.primary_guardian_id = cls.guardian
        cls.child = cls.env["dojo.member"].create({"partner_id": cls.env["res.partner"].create({"name": "Sign Child", "parent_id": household.id,
            "is_minor": True, "company_id": cls.company.id}).id, "company_id": cls.company.id, "membership_state": "active"})
        cls.adult = cls.env["dojo.member"].create({"partner_id": cls.env["res.partner"].create({"name": "Sign Adult", "company_id": cls.company.id}).id,
            "company_id": cls.company.id, "membership_state": "active"})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Sign kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Sign test", "company_id": cls.company.id, "kiosk_config_id": cls.kiosk.id, "workspace_ref": "20"})
        cls.users = {}
        for role in ("manager", "instructor"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Sign " + role,
                "login": "sign-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def call(self, op, payload, role="manager", key=None, write=True):
        if write and key is None:
            self.keys += 1
            key = "sign-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key if write else None)

    def sign(self, member, signer, image=None, key=None, **overrides):
        context = self.call("waiver_context", {"memberId": str(member.id)}, write=False)
        payload = {"memberId": str(member.id), "expectedVersion": context["version"], "signerPartnerId": str(signer.id), "signedByName": signer.name,
            "contentVersion": context["contentVersion"], "agreed": True, "signature": image or signature()}
        payload.update(overrides)
        return self.call("sign_waiver", payload, key=key)

    def test_minor_waiver_is_signed_by_household_guardian_with_evidence(self):
        context = self.call("waiver_context", {"memberId": str(self.child.id)}, write=False)
        self.assertTrue(context["minor"])
        self.assertEqual([s["name"] for s in context["signers"]], ["Sign Parent"])
        self.assertTrue(context["text"])
        self.assertEqual(self.sign(self.child, self.child.partner_id)["error"]["code"], "SIGNER_NOT_AUTHORIZED")
        self.assertEqual(self.sign(self.child, self.guardian, image=signature(blank=True))["error"]["code"], "SIGNATURE_EMPTY")
        self.assertEqual(self.sign(self.child, self.guardian, image="data:image/png;base64,AAAA")["error"]["code"], "SIGNATURE_INVALID")
        self.assertEqual(self.sign(self.child, self.guardian, agreed=False)["error"]["code"], "SIGNATURE_AGREEMENT_REQUIRED")
        self.assertEqual(self.sign(self.child, self.guardian, contentVersion="0" * 64)["error"]["code"], "WAIVER_TEXT_CHANGED")
        before = (self.env["mail.mail"].sudo().search_count([]), self.env["sms.sms"].sudo().search_count([]))
        signed = self.sign(self.child, self.guardian, key="sign-request-key-0001")
        self.assertEqual((signed["state"], signed["relationship"], signed["messageSent"]), ("waiver_signed", "guardian", False), signed)
        self.assertEqual(self.child.waiver_state, "signed")
        self.assertEqual(self.child.waiver_signed_by, "Sign Parent")
        self.assertTrue(self.child.waiver_signature)
        row = self.env["dojo.hub.signature"].sudo().search([("member_id", "=", self.child.id)])
        self.assertEqual((row.witnessed_by, row.signer_partner_id, len(row.content_sha256)), (self.users["manager"], self.guardian, 64))
        receipt = self.env["dojo.hub.receipt"].sudo().search([("request_key", "=", "sign-request-key-0001")])
        self.assertNotIn("base64", receipt.response_json)
        self.assertEqual((self.env["mail.mail"].sudo().search_count([]), self.env["sms.sms"].sudo().search_count([])), before)
        self.assertEqual(self.sign(self.child, self.guardian)["error"]["code"], "WAIVER_ALREADY_SIGNED")

    def test_adult_signs_own_waiver_and_instructors_cannot(self):
        self.assertEqual(self.call("waiver_context", {"memberId": str(self.adult.id)}, role="instructor", write=False)["error"]["status"], 403)
        context = self.call("waiver_context", {"memberId": str(self.adult.id)}, write=False)
        self.assertEqual([s["relationship"] for s in context["signers"]], ["self"])
        self.assertEqual(self.sign(self.adult, self.adult.partner_id)["state"], "waiver_signed")
