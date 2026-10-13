import base64
import io
from datetime import timedelta

from PIL import Image
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


def picture(size=(128, 128), kind="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, (180, 40, 40)).save(stream, kind)
    return base64.b64encode(stream.getvalue()).decode()


@tagged("post_install", "-at_install")
class TestMemberAdministration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Admin program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Admin course", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": True})
        now = fields.Datetime.now()
        cls.sessions = cls.env["dojo.class.session"]
        for offset in (24, 72):
            cls.sessions |= cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
                "start_datetime": now + timedelta(hours=offset), "end_datetime": now + timedelta(hours=offset + 1),
                "state": "open", "capacity": 10})
        cls.past = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now - timedelta(days=3), "end_datetime": now - timedelta(days=3, hours=-1), "state": "open", "capacity": 10})
        cls.members = {}
        for key in ("archive", "subscribed", "second", "third"):
            partner = cls.env["res.partner"].create({"name": "Admin " + key, "company_id": cls.company.id})
            cls.members[key] = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id,
                "membership_state": "active"})
        cls.booking = cls.env["dojo.class.enrollment"].with_context(skip_subscription_check=True).create({"session_id": cls.sessions[0].id, "member_id": cls.members["archive"].id})
        cls.history = cls.env["dojo.attendance.log"].create({"session_id": cls.past.id, "member_id": cls.members["archive"].id, "status": "present"})
        plan = cls.env["dojo.subscription.plan"].create({"name": "Admin plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        stage = cls.env["sale.subscription.stage"].create({"name": "Admin active", "type": "in_progress", "in_progress": True})
        pricelist = cls.env["product.pricelist"].create({"name": "Admin price", "currency_id": cls.company.currency_id.id})
        cls.env["sale.subscription"].create({"partner_id": cls.members["subscribed"].partner_id.id, "company_id": cls.company.id,
            "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id,
            "member_id": cls.members["subscribed"].id, "plan_id": plan.id})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Admin kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Admin test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "13"})
        cls.users, cls.grants = {}, {}
        for role in ("manager", "owner", "instructor", "guardian"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Admin " + role,
                "login": "admin-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.grants[role] = cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id,
                "role": role, "session_ids": [(6, 0, [cls.sessions[0].id])], "member_ids": [(6, 0, [cls.members["archive"].id])]})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def act(self, op, payload, key=None, role="manager"):
        if key is None:
            self.keys += 1
            key = "member-admin-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def bump(self, record):
        record.flush_recordset()
        self.env.cr.execute('UPDATE "%s" SET write_date = write_date + interval \'5 seconds\' WHERE id = %%s' % record._table, [record.id])
        record.invalidate_recordset(["write_date"])

    def member_version(self, key):
        return self.read("member_details", {"memberId": str(self.members[key].id)})["member"]["version"]

    def test_archive_review_lists_bookings_courses_and_blockers(self):
        self.template.with_context(skip_course_membership_check=True).course_member_ids = [(4, self.members["archive"].id)]
        review = self.read("archive_review", {"memberId": str(self.members["archive"].id)})
        self.assertEqual(review["futureBookingCount"], 2)
        self.assertEqual(review["courses"], [{"id": str(self.template.id), "name": "Admin course"}])
        self.assertEqual(review["blockers"], [])
        blocked = self.read("archive_review", {"memberId": str(self.members["subscribed"].id)})
        self.assertEqual(blocked["blockers"], ["MEMBERSHIP_REVIEW_REQUIRED"])
        result = self.act("archive_member", {"memberId": str(self.members["subscribed"].id),
            "expectedVersion": blocked["version"], "reason": "Synthetic request"})
        self.assertEqual(result["error"]["code"], "MEMBERSHIP_REVIEW_REQUIRED")
        self.assertTrue(self.members["subscribed"].active)

    def test_archive_cancels_future_bookings_keeps_history_and_restores_without_rebooking(self):
        member = self.members["archive"]
        payload = {"memberId": str(member.id), "expectedVersion": self.member_version("archive"), "reason": "Moved away"}
        result = self.act("archive_member", payload, key="member-admin-archive-0001")
        self.assertEqual(result["state"], "archived", result)
        self.assertEqual(result["cancelledBookingIds"], [str(self.booking.id)])
        self.assertFalse(member.active)
        self.assertEqual(self.booking.status, "cancelled")
        self.assertTrue(self.history.exists())
        self.assertNotIn(str(member.id), [m["id"] for m in self.read("context", {})["members"]])
        self.assertEqual(self.read("member_details", {"memberId": str(member.id)})["error"]["code"], "MEMBER_UNAVAILABLE")
        replay = self.act("archive_member", payload, key="member-admin-archive-0001")
        self.assertTrue(replay["replayed"])
        archived = self.read("archived_members", {"query": "Admin arch"})["members"]
        self.assertEqual([(a["id"], a["reason"], a["archivedBy"]) for a in archived], [(str(member.id), "Moved away", "Admin manager")])
        restored = self.act("restore_member", {"memberId": str(member.id), "expectedVersion": archived[0]["version"], "reason": "Returned"})
        self.assertEqual((restored["state"], restored["bookingsRestored"]), ("restored", 0))
        self.assertTrue(member.active)
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual(self.read("archived_members", {"query": ""})["members"], [])

    def test_archive_requires_current_version_role_and_company(self):
        member = self.members["archive"]
        stale = self.member_version("archive")
        self.bump(member)
        self.assertEqual(self.act("archive_member", {"memberId": str(member.id), "expectedVersion": stale,
            "reason": "Stale"})["error"]["code"], "VERSION_CONFLICT")
        for role in ("instructor", "guardian"):
            self.assertEqual(self.act("archive_member", {"memberId": str(member.id), "expectedVersion": stale,
                "reason": "No"}, role=role)["error"]["status"], 403)
            self.assertEqual(self.read("archived_members", {"query": ""}, role=role)["error"]["status"], 403)
        foreign_company = self.env["res.company"].create({"name": "Foreign admin school"})
        foreign = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Foreign admin",
            "company_id": foreign_company.id}).id, "company_id": foreign_company.id, "active": False})
        self.assertEqual(self.act("restore_member", {"memberId": str(foreign.id), "expectedVersion": 0,
            "reason": "No"})["error"]["code"], "MEMBER_UNAVAILABLE")
        self.assertEqual(self.act("restore_member", {"memberId": str(member.id), "expectedVersion": 0,
            "reason": "Active already"})["error"]["code"], "MEMBER_UNAVAILABLE")
        self.assertTrue(member.active)

    def test_primary_guardian_and_portal_login_block_archive(self):
        member = self.members["archive"]
        household = self.env["res.partner"].create({"name": "Admin household", "is_household": True,
            "company_id": self.company.id, "primary_guardian_id": member.partner_id.id})
        member.partner_id.parent_id = household
        self.members["second"].partner_id.parent_id = household
        self.assertIn("PRIMARY_GUARDIAN_REVIEW_REQUIRED", self.read("archive_review", {"memberId": str(member.id)})["blockers"])
        self.members["third"].partner_id.write({"email": "third-admin@example.invalid"})
        self.env["res.users"].with_context(no_reset_password=True).create({"name": "Admin third", "login": "third-admin@example.invalid",
            "partner_id": self.members["third"].partner_id.id, "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        review = self.read("archive_review", {"memberId": str(self.members["third"].id)})
        self.assertEqual(review["blockers"], ["PORTAL_ACCESS_REVIEW_REQUIRED"])
        self.assertEqual(self.act("archive_member", {"memberId": str(self.members["third"].id), "expectedVersion": review["version"],
            "reason": "No"})["error"]["code"], "PORTAL_ACCESS_REVIEW_REQUIRED")

    def test_photo_is_validated_scoped_and_read_back(self):
        member = self.members["archive"]
        version = self.member_version("archive")
        result = self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": version, "image": picture()})
        self.assertEqual(result["state"], "photo_updated", result)
        self.assertTrue(self.read("member_details", {"memberId": str(member.id)})["member"]["photo"].startswith("data:image/"))
        version = self.member_version("archive")
        for image, code in ((picture(kind="GIF"), "PHOTO_TYPE_UNSUPPORTED"), (picture((32, 32)), "PHOTO_DIMENSIONS"),
                            ("not base64!", "PHOTO_INVALID"), ("A" * 420000, "PHOTO_TOO_LARGE"),
                            (base64.b64encode(b"\xff\xd8\xff" + b"0" * 64).decode(), "PHOTO_INVALID")):
            self.assertEqual(self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": version,
                "image": image})["error"]["code"], code)
        instructor = self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": version,
            "image": picture(kind="JPEG")}, role="instructor")
        self.assertEqual(instructor["state"], "photo_updated", instructor)
        self.assertEqual(self.act("update_member_photo", {"memberId": str(self.members["second"].id),
            "expectedVersion": 0, "image": picture()}, role="instructor")["error"]["code"], "MEMBER_UNAVAILABLE")
        self.assertEqual(self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": version,
            "image": picture()}, role="guardian")["error"]["status"], 403)
        stale = self.member_version("archive")
        self.bump(member)
        self.assertEqual(self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": stale,
            "image": None})["error"]["code"], "VERSION_CONFLICT")
        removed = self.act("update_member_photo", {"memberId": str(member.id), "expectedVersion": self.member_version("archive"), "image": None})
        self.assertEqual(removed["state"], "photo_removed")
        self.assertIsNone(self.read("member_details", {"memberId": str(member.id)})["member"]["photo"])

    def test_bulk_course_assignment_reviews_then_applies_native_enrollments(self):
        roster = self.read("course_roster", {"templateId": str(self.template.id)})
        self.assertEqual(roster["course"]["upcomingSessions"], 2)
        ids = [str(self.members["second"].id), str(self.members["third"].id)]
        preview = self.read("course_assignment_preview", {"templateId": str(self.template.id), "addMemberIds": ids, "removeMemberIds": []})
        self.assertTrue(preview["valid"], preview)
        self.assertEqual([a["futureBookings"] for a in preview["add"]], [2, 2])
        payload = {"templateId": str(self.template.id), "expectedVersion": roster["course"]["version"], "addMemberIds": ids,
            "removeMemberIds": [], "reason": "Spring cohort", "reviewToken": preview["reviewToken"]}
        result = self.act("assign_course_members", payload, key="member-admin-course-0001")
        self.assertEqual((result["state"], result["bookingsCreated"], result["bookingsCancelled"]), ("course_roster_updated", 4, 0), result)
        self.assertEqual(set(self.template.course_member_ids.ids), {self.members["second"].id, self.members["third"].id})
        self.assertTrue(self.act("assign_course_members", payload, key="member-admin-course-0001")["replayed"])
        receipt = self.env["dojo.hub.receipt"].search([("request_key", "=", "member-admin-course-0001")])
        self.assertEqual(set(receipt.scope_member_ids.ids), {self.members["second"].id, self.members["third"].id})
        details = self.read("member_details", {"memberId": ids[0]})
        self.assertEqual(details["courses"], [{"id": str(self.template.id), "name": "Admin course"}])
        self.assertEqual(len(details["bookings"]), 2)
        roster = self.read("course_roster", {"templateId": str(self.template.id)})
        preview = self.read("course_assignment_preview", {"templateId": str(self.template.id), "addMemberIds": [], "removeMemberIds": ids[:1]})
        self.assertEqual(preview["remove"][0]["cancelledBookings"], 2)
        removed = self.act("assign_course_members", {**payload, "expectedVersion": roster["course"]["version"], "addMemberIds": [],
            "removeMemberIds": ids[:1], "reviewToken": preview["reviewToken"]})
        self.assertEqual((removed["bookingsCreated"], removed["bookingsCancelled"]), (0, 2), removed)
        self.assertEqual(self.template.course_member_ids, self.members["third"])

    def test_bulk_assignment_rejects_stale_review_capacity_and_unbounded_input(self):
        ids = [str(self.members["second"].id)]
        roster = self.read("course_roster", {"templateId": str(self.template.id)})
        preview = self.read("course_assignment_preview", {"templateId": str(self.template.id), "addMemberIds": ids, "removeMemberIds": []})
        payload = {"templateId": str(self.template.id), "expectedVersion": roster["course"]["version"], "addMemberIds": ids,
            "removeMemberIds": [], "reason": "Cohort", "reviewToken": preview["reviewToken"]}
        self.env["dojo.class.enrollment"].with_context(skip_subscription_check=True).create({"session_id": self.sessions[1].id, "member_id": self.members["second"].id})
        self.assertEqual(self.act("assign_course_members", payload)["error"]["code"], "ASSIGNMENT_REVIEW_CHANGED")
        self.bump(self.template)
        self.assertEqual(self.act("assign_course_members", payload)["error"]["code"], "VERSION_CONFLICT")
        self.sessions[0].capacity = 1
        full = self.read("course_assignment_preview", {"templateId": str(self.template.id),
            "addMemberIds": ids + [str(self.members["third"].id)], "removeMemberIds": []})
        self.assertFalse(full["valid"])
        self.assertIsNone(full["reviewToken"])
        self.assertEqual(full["capacityConflicts"][0]["sessionId"], str(self.sessions[0].id))
        version = self.read("course_roster", {"templateId": str(self.template.id)})["course"]["version"]
        before = self.env["dojo.class.enrollment"].search_count([])
        self.assertEqual(self.act("assign_course_members", {**payload, "expectedVersion": version,
            "addMemberIds": ids + [str(self.members["third"].id)], "reviewToken": "x"})["error"]["code"], "CAPACITY_REVIEW_REQUIRED")
        self.assertEqual(self.env["dojo.class.enrollment"].search_count([]), before)
        self.assertFalse(self.template.course_member_ids)
        foreign_company = self.env["res.company"].create({"name": "Foreign course school"})
        foreign = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Foreign course",
            "company_id": foreign_company.id}).id, "company_id": foreign_company.id})
        base = {"templateId": str(self.template.id), "removeMemberIds": []}
        self.assertEqual(self.read("course_assignment_preview", {**base, "addMemberIds": [str(foreign.id)]})["error"]["code"], "MEMBER_UNAVAILABLE")
        self.assertEqual(self.read("course_assignment_preview", {**base, "addMemberIds": ids * 2})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.read("course_assignment_preview", {**base, "addMemberIds": [str(i) for i in range(1, 52)]})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.read("course_assignment_preview", {"templateId": str(self.template.id), "addMemberIds": ids,
            "removeMemberIds": ids})["error"]["code"], "INVALID_COMMAND")
        self.assertEqual(self.read("course_roster", {"templateId": str(self.template.id)}, role="instructor")["error"]["status"], 403)
