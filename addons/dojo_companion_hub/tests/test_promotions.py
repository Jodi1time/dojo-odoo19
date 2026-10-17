from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestBeltTesting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env["res.company"].create({"name": "Promotion school"})
        cls.other = cls.env["res.company"].create({"name": "Other promotion school"})
        cls.ranks = [cls.env["dojo.belt.rank"].create({"name": name, "sequence": seq, "company_id": cls.company.id,
            "attendance_threshold": 2, "max_stripes": 4}) for name, seq in (("White", 10), ("Yellow", 20), ("Orange", 30))]
        cls.program = cls.env["dojo.program"].create({"name": "Promotion program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Promotion class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        now = fields.Datetime.now()
        cls.members = {}
        for key in ("ready", "early", "second"):
            partner = cls.env["res.partner"].create({"name": "Promotion " + key, "company_id": cls.company.id,
                "email": "promotion-%s@example.invalid" % key})
            member = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": "active"})
            cls.env["dojo.member.rank"].create({"member_id": member.id, "rank_id": cls.ranks[0].id,
                "date_awarded": fields.Date.today() - timedelta(days=60)})
            cls.members[key] = member
        for key, count in (("ready", 3), ("second", 2), ("early", 1)):
            for i in range(count):
                session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
                    "start_datetime": now - timedelta(days=10 + i), "end_datetime": now - timedelta(days=10 + i) + timedelta(hours=1),
                    "state": "open", "capacity": 10})
                cls.env["dojo.attendance.log"].with_context(dojo_hub_staff_recorded=True).create({"session_id": session.id,
                    "member_id": cls.members[key].id, "status": "present", "checkin_datetime": session.start_datetime})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Promotion kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Promotion test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "16"})
        cls.users = {}
        for role in ("manager", "instructor"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Promotion " + role,
                "login": "promotion-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def act(self, op, payload, role="manager", key=None):
        if key is None:
            self.keys += 1
            key = "promotion-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def counts(self):
        models = ("mail.mail", "sms.sms", "mail.activity", "account.move")
        return {m: self.env[m].sudo().search_count([]) for m in models if m in self.env}

    def roster(self, keys=("ready", "second"), key=None):
        return self.act("save_testing_roster", {"testId": None, "expectedVersion": 0, "name": "Spring testing",
            "testDate": str(fields.Date.today()), "add": [{"memberId": str(self.members[k].id), "targetRankId": str(self.ranks[1].id)} for k in keys],
            "removeRegistrationIds": [], "reason": "Eligible students"}, key=key)

    def results(self, test_id, mapping):
        sheet = self.read("testing_roster", {"testId": test_id})
        rows = {r["memberId"]: r["registrationId"] for r in sheet["rows"]}
        return self.act("record_test_results", {"testId": test_id, "expectedVersion": sheet["test"]["version"],
            "results": [{"registrationId": rows[str(self.members[k].id)], "result": v} for k, v in mapping.items()],
            "reason": "Panel decision"})

    def test_eligibility_lists_threshold_progress_and_roster_saves_native_test(self):
        rows = {r["memberId"]: r for r in self.read("testing_eligibility", {"query": "Promotion"})["rows"]}
        self.assertEqual(rows[str(self.members["ready"].id)]["status"], "eligible")
        self.assertEqual(rows[str(self.members["early"].id)]["status"], "not_yet")
        self.assertEqual(rows[str(self.members["ready"].id)]["nextRank"]["name"], "Yellow")
        before = self.counts()
        saved = self.roster(key="promotion-roster-0001")
        self.assertEqual((saved["state"], saved["count"]), ("testing_roster_saved", 2), saved)
        test = self.env["dojo.belt.test"].browse(int(saved["testId"]))
        self.assertEqual(test.company_id, self.company)
        self.assertEqual(set(test.registration_ids.mapped("result")), {"pending"})
        self.assertTrue(self.members["ready"].test_invite_pending)
        self.assertTrue(self.roster(key="promotion-roster-0001")["replayed"])
        self.assertEqual(self.env["dojo.belt.test"].search_count([("company_id", "=", self.company.id)]), 1)
        self.assertEqual(self.counts(), before)
        sheet = self.read("testing_roster", {"testId": saved["testId"]})
        duplicate = self.act("save_testing_roster", {"testId": saved["testId"], "expectedVersion": sheet["test"]["version"], "name": "Spring testing",
            "testDate": str(fields.Date.today()), "add": [{"memberId": str(self.members["ready"].id), "targetRankId": str(self.ranks[1].id)}],
            "removeRegistrationIds": [], "reason": "x"})
        self.assertEqual(duplicate["error"]["code"], "ROSTER_DUPLICATE")
        lower = self.act("save_testing_roster", {"testId": saved["testId"], "expectedVersion": sheet["test"]["version"], "name": "Spring testing",
            "testDate": str(fields.Date.today()), "add": [{"memberId": str(self.members["early"].id), "targetRankId": str(self.ranks[0].id)}],
            "removeRegistrationIds": [], "reason": "x"})
        self.assertEqual(lower["error"]["code"], "TARGET_RANK_NOT_HIGHER")
        second = next(r for r in sheet["rows"] if r["memberId"] == str(self.members["second"].id))
        removed = self.act("save_testing_roster", {"testId": saved["testId"], "expectedVersion": sheet["test"]["version"], "name": "Spring testing",
            "testDate": str(fields.Date.today()), "add": [], "removeRegistrationIds": [second["registrationId"]], "reason": "Not ready"})
        self.assertEqual(removed["count"], 1, removed)
        self.assertFalse(self.members["second"].test_invite_pending)
        self.env.cr.execute("UPDATE dojo_belt_test SET write_date = write_date + interval '5 seconds' WHERE id = %s", [int(saved["testId"])])
        self.env["dojo.belt.test"].invalidate_model()
        stale = self.act("save_testing_roster", {"testId": saved["testId"], "expectedVersion": removed["version"], "name": "Renamed",
            "testDate": str(fields.Date.today()), "add": [], "removeRegistrationIds": [], "reason": "x"})
        self.assertEqual(stale["error"]["code"], "VERSION_CONFLICT")

    def test_results_and_bulk_promotion_are_reviewed_and_replay(self):
        test_id = self.roster()["testId"]
        before = self.counts()
        saved = self.results(test_id, {"ready": "pass", "second": "fail"})
        self.assertEqual(saved["state"], "test_results_saved", saved)
        self.assertFalse(self.members["ready"].test_invite_pending)
        sheet = self.read("testing_roster", {"testId": test_id})
        ids = [r["registrationId"] for r in sheet["rows"]]
        preview = self.read("promotion_preview", {"testId": test_id, "registrationIds": ids})
        self.assertFalse(preview["valid"])
        self.assertEqual([c["problem"] for c in preview["conflicts"]], ["Result is not pass"])
        passed = [r["registrationId"] for r in sheet["rows"] if r["result"] == "pass"]
        preview = self.read("promotion_preview", {"testId": test_id, "registrationIds": passed})
        self.assertTrue(preview["valid"])
        self.assertEqual([(p["from"], p["to"]) for p in preview["promotions"]], [("White", "Yellow")])
        payload = {"testId": test_id, "expectedVersion": preview["version"], "registrationIds": passed, "reason": "Passed panel",
            "reviewToken": preview["reviewToken"]}
        wrong = self.act("promote_test_results", {**payload, "reviewToken": "x" * 64})
        self.assertEqual(wrong["error"]["code"], "PROMOTION_REVIEW_CHANGED")
        done = self.act("promote_test_results", payload, key="promotion-award-0001")
        self.assertEqual(done["state"], "promotions_awarded", done)
        self.assertEqual(self.members["ready"].current_rank_id, self.ranks[1])
        self.assertEqual(self.members["ready"].attendance_since_last_rank, 0)
        self.assertEqual(self.members["second"].current_rank_id, self.ranks[0])
        self.assertTrue(self.act("promote_test_results", payload, key="promotion-award-0001")["replayed"])
        self.assertEqual(self.env["dojo.member.rank"].search_count([("member_id", "=", self.members["ready"].id)]), 2)
        self.assertEqual(self.env["dojo.belt.test"].browse(int(test_id)).state, "completed")
        again = self.read("promotion_preview", {"testId": test_id, "registrationIds": passed})
        self.assertEqual(again["conflicts"][0]["problem"], "Already promoted from this test")
        locked = self.results(test_id, {"ready": "fail"})
        self.assertEqual(locked["error"]["code"], "TEST_NOT_EDITABLE")
        self.assertEqual(self.counts(), before)

    def test_reversal_archives_only_the_latest_award_and_restores_previous_rank(self):
        test_id = self.roster(keys=("ready",))["testId"]
        self.results(test_id, {"ready": "pass"})
        sheet = self.read("testing_roster", {"testId": test_id})
        passed = [r["registrationId"] for r in sheet["rows"]]
        preview = self.read("promotion_preview", {"testId": test_id, "registrationIds": passed})
        self.act("promote_test_results", {"testId": test_id, "expectedVersion": preview["version"], "registrationIds": passed,
            "reason": "Passed", "reviewToken": preview["reviewToken"]})
        history = self.read("promotion_history", {"query": "Promotion ready"})["rows"]
        latest = next(r for r in history if r["latest"])
        older = next(r for r in history if not r["latest"])
        self.assertEqual(latest["rank"], "Yellow")
        self.assertEqual(self.act("reverse_promotion", {"rankHistoryId": older["id"], "expectedVersion": older["memberVersion"],
            "reason": "x"})["error"]["code"], "REVERSAL_NOT_LATEST")
        self.assertEqual(self.act("reverse_promotion", {"rankHistoryId": latest["id"], "expectedVersion": latest["memberVersion"],
            "reason": "x"}, role="instructor")["error"]["status"], 403)
        reversed_ = self.act("reverse_promotion", {"rankHistoryId": latest["id"], "expectedVersion": latest["memberVersion"],
            "reason": "Awarded to the wrong student"})
        self.assertEqual((reversed_["state"], reversed_["before"], reversed_["after"]), ("promotion_reversed", "Yellow", "White"), reversed_)
        self.assertEqual(self.members["ready"].current_rank_id, self.ranks[0])
        self.assertEqual(self.members["ready"].attendance_since_last_rank, 3)
        row = self.env["dojo.member.rank"].with_context(active_test=False).browse(int(latest["id"]))
        self.assertFalse(row.active)
        self.assertEqual(row.reversal_reason, "Awarded to the wrong student")
        rows = self.read("promotion_history", {"query": "Promotion ready"})["rows"]
        self.assertTrue(next(r for r in rows if r["id"] == latest["id"])["reversed"])
        details = self.read("member_details", {"memberId": str(self.members["ready"].id)})
        self.assertNotIn(latest["id"], [r["id"] for r in details["rankHistory"]])

    def test_testing_is_manager_only_and_school_scoped(self):
        self.assertEqual(self.read("testing_eligibility", {"query": ""}, role="instructor")["error"]["status"], 403)
        foreign = self.env["dojo.belt.test"].create({"name": "Foreign test", "company_id": self.other.id})
        self.assertEqual(self.read("testing_roster", {"testId": str(foreign.id)})["error"]["code"], "TEST_UNAVAILABLE")
        foreign_rank = self.env["dojo.belt.rank"].create({"name": "Foreign belt", "sequence": 99, "company_id": self.other.id})
        result = self.act("save_testing_roster", {"testId": None, "expectedVersion": 0, "name": "Bad", "testDate": str(fields.Date.today()),
            "add": [{"memberId": str(self.members["ready"].id), "targetRankId": str(foreign_rank.id)}], "removeRegistrationIds": [], "reason": "x"})
        self.assertEqual(result["error"]["code"], "INVALID_RANK")
        self.assertFalse(self.env["dojo.belt.test"].search([("company_id", "=", self.company.id)]))
