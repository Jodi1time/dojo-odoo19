"""Native intake transactions: real records; no provider or production account."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import session_version


@tagged("post_install", "-at_install")
class TestStudentIntake(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Intake kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Intake test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "12"})
        cls.users, cls.grants = {}, {}
        for role in ("manager", "instructor", "guardian", "unassigned"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Intake " + role,
                "login": "intake-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            if role != "unassigned":
                cls.grants[role] = cls.env["dojo.hub.grant"].create({"site_id": cls.site.id,
                    "user_id": cls.users[role].id, "role": role})

    def act(self, op, payload, key="student-intake-request-0001", role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def intake(self, child=False):
        return {"studentType": "child" if child else "adult", "student": {"name": "Synthetic New Student",
            "dateOfBirth": "2018-01-02" if child else "1995-01-02", "gender": "", "email": "" if child else "intake-student@example.invalid", "phone": ""},
            "guardian": {"kind": "new", "name": "Synthetic New Guardian", "email": "intake-parent@example.invalid", "phone": ""} if child else None,
            "reviewRef": "synthetic-completed-identity-and-authority-check"}

    def command(self, intake=None):
        value = self.act("intake_preview", {"intake": intake or self.intake()})
        self.assertNotIn("error", value)
        self.assertTrue(value["valid"], value)
        return {"intake": value["intake"], "reviewToken": value["reviewToken"]}

    def counts(self):
        return {model: self.env[model].sudo().with_context(active_test=False).search_count([]) for model in
                ("dojo.member", "res.partner", "sale.subscription", "dojo.class.enrollment", "res.users", "dojo.hub.guardian", "dojo.hub.delivery", "mail.mail")}

    def test_preview_collects_missing_fields_without_writes(self):
        p = self.intake(child=True)
        p["student"].update(name="", dateOfBirth="")
        p["guardian"].update(name="", email="")
        p["reviewRef"] = ""
        before = self.counts()
        result = self.act("intake_preview", {"intake": p})
        self.assertFalse(result["valid"])
        self.assertGreaterEqual(len(result["issues"]), 5)
        self.assertEqual(self.counts(), before)

    def test_create_adult_native_profile_once_and_no_implicit_side_effects(self):
        before = self.counts()
        p = self.command()
        self.assertEqual(self.counts(), before)
        first = self.act("create_student", p)
        self.assertEqual(first["state"], "student_created", first)
        member = self.env["dojo.member"].browse(int(first["memberId"]))
        self.assertEqual(member.company_id, self.company)
        self.assertEqual(member.partner_id.company_id, self.company)
        self.assertEqual(member.membership_state, "lead")
        self.assertFalse(member.partner_id.parent_id)
        self.assertEqual(str(member.date_of_birth), "1995-01-02")
        self.assertTrue(self.act("create_student", p)["replayed"])
        after = self.counts()
        self.assertEqual(after["dojo.member"], before["dojo.member"] + 1)
        self.assertEqual(after["res.partner"], before["res.partner"] + 1)
        for model in before.keys() - {"dojo.member", "res.partner"}:
            self.assertEqual(after[model], before[model], model)
        recent = self.act("intake_context", {"query": ""})["recent"]
        self.assertEqual(recent[0]["memberId"], first["memberId"])
        self.assertEqual(self.act("member_details", {"memberId": first["memberId"]})["member"]["id"], first["memberId"])

    def test_create_child_has_exact_native_household_and_no_channel_binding(self):
        before = self.counts()
        result = self.act("create_student", self.command(self.intake(child=True)))
        member = self.env["dojo.member"].browse(int(result["memberId"]))
        household = member.partner_id.parent_id
        self.assertEqual(str(household.id), result["householdId"])
        self.assertTrue(household.is_household)
        self.assertTrue(member.partner_id.is_minor)
        self.assertEqual(str(household.primary_guardian_id.id), result["guardianId"])
        self.assertEqual(household.primary_guardian_id.parent_id, household)
        self.assertEqual(household.company_id, self.company)
        self.assertEqual(self.counts()["dojo.hub.guardian"], before["dojo.hub.guardian"])
        self.assertEqual(self.counts()["res.users"], before["res.users"])

    def test_existing_guardian_links_only_selected_household(self):
        first = self.act("create_student", self.command(self.intake(child=True)))
        row = self.act("intake_context", {"query": "Synthetic New Guardian"})["guardians"][0]
        p = self.intake(child=True)
        p["student"]["name"] = "Synthetic Second Child"
        p["guardian"] = {"kind": "existing", "partnerId": row["id"], "householdId": row["householdId"],
            "expectedVersion": row["version"], "expectedHouseholdVersion": row["householdVersion"]}
        before = self.counts()
        result = self.act("create_student", self.command(p), key="student-intake-request-0002")
        self.assertEqual(result["householdId"], first["householdId"])
        self.assertEqual(result["guardianId"], first["guardianId"])
        self.assertEqual(self.counts()["res.partner"], before["res.partner"] + 1)

    def test_new_key_duplicate_and_archived_duplicate_are_rejected(self):
        p = self.command()
        first = self.act("create_student", p)
        result = self.act("create_student", p, key="student-intake-request-0002")
        self.assertEqual(result["error"]["code"], "DUPLICATE_STUDENT_REVIEW_REQUIRED")
        self.env["dojo.member"].browse(int(first["memberId"])).active = False
        self.assertEqual(self.act("intake_preview", {"intake": p["intake"]})["error"]["code"], "DUPLICATE_STUDENT_REVIEW_REQUIRED")
        self.assertTrue(self.act("create_student", p)["replayed"])

    def test_changed_intake_requires_new_review_and_same_key_is_not_repurposed(self):
        p = self.command()
        p["intake"]["student"]["name"] = "Changed after review"
        before = self.counts()
        self.assertEqual(self.act("create_student", p)["error"]["code"], "INTAKE_REVIEW_CHANGED")
        self.assertEqual(self.counts(), before)
        p = self.command()
        self.act("create_student", p)
        p["intake"]["reviewRef"] = "Changed reference"
        self.assertEqual(self.act("create_student", p)["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_roles_revoked_access_and_company_boundaries(self):
        p = self.command()
        self.act("create_student", p)
        for role in ("instructor", "guardian", "unassigned"):
            for op, payload in (("intake_preview", {"intake": self.intake()}), ("intake_context", {"query": ""}), ("create_student", p)):
                self.assertEqual(self.act(op, payload, role=role)["error"]["status"], 403)
        self.grants["manager"].active = False
        self.assertEqual(self.act("create_student", p)["error"]["status"], 403)

    def test_foreign_guardian_is_never_disclosed_or_linked(self):
        other = self.env["res.company"].create({"name": "Foreign intake school"})
        house = self.env["res.partner"].create({"name": "Private household", "is_household": True, "company_id": other.id})
        guardian = self.env["res.partner"].create({"name": "Private guardian", "is_guardian": True, "parent_id": house.id, "company_id": other.id})
        self.assertFalse(self.act("intake_context", {"query": "Private"})["guardians"])
        p = self.intake(child=True)
        p["guardian"] = {"kind": "existing", "partnerId": str(guardian.id), "householdId": str(house.id),
            "expectedVersion": session_version(guardian), "expectedHouseholdVersion": session_version(house)}
        self.assertEqual(self.act("intake_preview", {"intake": p})["error"]["code"], "GUARDIAN_UNAVAILABLE")

    def test_existing_guardian_change_invalidates_review(self):
        self.act("create_student", self.command(self.intake(child=True)))
        row = self.act("intake_context", {"query": ""})["guardians"][0]
        p = self.intake(child=True)
        p["student"]["name"] = "Another child"
        p["guardian"] = {"kind": "existing", "partnerId": row["id"], "householdId": row["householdId"],
            "expectedVersion": row["version"], "expectedHouseholdVersion": row["householdVersion"]}
        command = self.command(p)
        self.env["res.partner"].browse(int(row["id"])).write({"write_date": fields.Datetime.now() + timedelta(seconds=5)})
        self.assertEqual(self.act("create_student", command, key="student-intake-request-0002")["error"]["code"], "VERSION_CONFLICT")

    def test_duplicate_guardian_requires_explicit_selection(self):
        self.env["res.partner"].create({"name": "Known guardian", "email": "INTAKE-PARENT@example.invalid", "company_id": self.company.id})
        self.assertEqual(self.act("intake_preview", {"intake": self.intake(child=True)})["error"]["code"], "GUARDIAN_DUPLICATE_REVIEW_REQUIRED")

    def test_receipt_failure_rolls_back_student_guardian_and_household(self):
        from odoo.exceptions import ValidationError
        p = self.command(self.intake(child=True))
        before = self.counts()
        with patch.object(type(self.env["dojo.hub.receipt"]), "create", side_effect=ValidationError("synthetic receipt failure")):
            result = self.act("create_student", p)
        self.assertEqual(result["error"]["code"], "BUSINESS_RULE_REVIEW_REQUIRED")
        self.assertEqual(self.counts(), before)

    def test_unapproved_fields_and_future_birth_do_not_write(self):
        for field in ("companyId", "membership_state", "portalAccessGranted"):
            p = self.intake()
            p[field] = "not permitted"
            self.assertFalse(self.act("intake_preview", {"intake": p})["valid"])
        p = self.intake()
        p["student"]["dateOfBirth"] = str(fields.Date.today() + timedelta(days=1))
        self.assertFalse(self.act("intake_preview", {"intake": p})["valid"])
