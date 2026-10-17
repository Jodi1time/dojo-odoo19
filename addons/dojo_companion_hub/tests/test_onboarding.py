from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestOnboardingChecklists(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        household = cls.env["res.partner"].create({"name": "Family Onboard", "is_household": True, "company_id": cls.company.id})
        cls.guardian = cls.env["res.partner"].create({"name": "Onboard Parent", "parent_id": household.id, "is_guardian": True,
            "company_id": cls.company.id, "phone": "+1 512 555 0190"})
        household.primary_guardian_id = cls.guardian
        cls.child = cls.env["dojo.member"].create({"partner_id": cls.env["res.partner"].create({"name": "Onboard Child", "parent_id": household.id,
            "is_minor": True, "company_id": cls.company.id}).id, "company_id": cls.company.id, "membership_state": "lead"})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Onboard kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Onboard test", "company_id": cls.company.id, "kiosk_config_id": cls.kiosk.id, "workspace_ref": "21"})
        cls.users = {}
        for role in ("manager", "instructor"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Onboard " + role,
                "login": "onboard-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def test_setup_checklist_reports_real_configuration(self):
        checklist = self.read("setup_checklist", {})
        items = {i["key"]: i for i in checklist["items"]}
        self.assertTrue(items["staff"]["done"])
        self.assertFalse(items["messaging"]["done"])
        self.assertFalse(items["kiosk"]["done"])
        self.assertEqual(checklist["total"], len(items))
        self.assertEqual(self.read("setup_checklist", {}, role="instructor")["error"]["status"], 403)

    def test_member_checklist_follows_native_records(self):
        items = {i["key"]: i for i in self.read("member_onboarding", {"memberId": str(self.child.id)})["items"]}
        self.assertTrue(items["guardian"]["done"])
        self.assertTrue(items["contact"]["done"])
        self.assertFalse(items["waiver"]["done"])
        self.assertFalse(items["membership"]["done"])
        self.assertFalse(items["kiosk"]["done"])
        self.env["dojo.kiosk.guardian.pin"]._set_pin(self.company, self.guardian, "482913")
        items = {i["key"]: i for i in self.read("member_onboarding", {"memberId": str(self.child.id)})["items"]}
        self.assertTrue(items["kiosk"]["done"])
