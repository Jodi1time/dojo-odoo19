from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestTrialLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.program = cls.env["dojo.program"].create({"name": "Trial program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Trial class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        now = fields.Datetime.now()
        cls.session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now + timedelta(hours=48), "end_datetime": now + timedelta(hours=49), "state": "open", "capacity": 10})
        cls.second_session = cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
            "start_datetime": now + timedelta(hours=72), "end_datetime": now + timedelta(hours=73), "state": "open", "capacity": 1})
        cls.plan = cls.env["dojo.subscription.plan"].create({"name": "Trial plan", "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id, "price": 99, "plan_type": "program", "auto_send_invoice": False,
            "program_ids": [(6, 0, [cls.program.id])]})
        if not cls.env["sale.subscription.stage"].search_count([("type", "=", "in_progress")]):
            cls.env["sale.subscription.stage"].create({"name": "Trial active", "type": "in_progress", "in_progress": True})
        if not cls.env["product.pricelist"].search_count([("currency_id", "=", cls.company.currency_id.id)]):
            cls.env["product.pricelist"].create({"name": "Trial price", "currency_id": cls.company.currency_id.id})
        cls.members = {}
        for key, state in (("child", "lead"), ("other", "lead"), ("active", "active")):
            partner = cls.env["res.partner"].create({"name": "Trial " + key, "company_id": cls.company.id,
                "email": "trial-%s@example.invalid" % key})
            cls.members[key] = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": state})
        cls.leads = {key: cls.env["crm.lead"].create({"name": "Trial enquiry " + key, "company_id": cls.company.id,
            "contact_name": "Synthetic parent " + key, "email_from": "trial-parent-%s@example.invalid" % key,
            "type": "opportunity"}) for key in ("child", "other")}
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Trial kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Trial test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "15"})
        cls.users = {}
        for role in ("manager", "instructor"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Trial " + role,
                "login": "trial-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role,
                "session_ids": [(6, 0, [cls.session.id])]})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def act(self, op, payload, role="manager", key=None):
        if key is None:
            self.keys += 1
            key = "trial-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def counts(self):
        models = ("mail.mail", "sms.sms", "mail.activity", "sale.subscription", "res.users", "dojo.hub.guardian", "account.move")
        return {m: self.env[m].sudo().with_context(active_test=False).search_count([]) for m in models if m in self.env}

    def reserve(self, lead="child", member="child", session=None, key=None):
        session = session or self.session
        context = self.read("trial_context", {"leadId": str(self.leads[lead].id), "query": ""})
        version = next(s["version"] for s in context["sessions"] if s["id"] == str(session.id))
        return self.act("reserve_trial", {"leadId": str(self.leads[lead].id), "expectedLeadVersion": context["lead"]["version"],
            "memberId": str(self.members[member].id), "sessionId": str(session.id), "expectedSessionVersion": version,
            "reason": "Parent asked for a first class"}, key=key)

    def trial_row(self, lead="child"):
        return self.read("trial_context", {"leadId": str(self.leads[lead].id), "query": ""})["trials"][0]

    def start_class(self, session=None):
        session = session or self.session
        session.flush_recordset()
        self.env.cr.execute("UPDATE dojo_class_session SET start_datetime = now() at time zone 'UTC' - interval '2 hours', "
            "end_datetime = now() at time zone 'UTC' - interval '1 hour' WHERE id = %s", [session.id])
        session.invalidate_recordset()

    def attend(self, lead="child"):
        self.start_class()
        row = self.trial_row(lead)
        return self.act("record_trial_outcome", {"trialId": row["id"], "expectedVersion": row["version"], "outcome": "attended",
            "reason": "Took part in the full class"})

    def test_context_exposes_sessions_plans_students_and_stage_automations(self):
        context = self.read("trial_context", {"leadId": str(self.leads["child"].id), "query": "Trial ch"})
        self.assertIn(str(self.session.id), [s["id"] for s in context["sessions"]])
        self.assertIn(str(self.plan.id), [p["id"] for p in context["plans"]])
        self.assertEqual([s["id"] for s in context["students"]], [str(self.members["child"].id)])
        self.assertIsInstance(context["stageAutomations"], list)
        self.assertEqual(self.read("trial_context", {"leadId": str(self.leads["child"].id), "query": ""}, role="instructor")["error"]["status"], 403)

    def test_reservation_is_a_native_enrollment_without_stage_change_or_messages(self):
        lead, member = self.leads["child"], self.members["child"]
        stage, before = lead.stage_id, self.counts()
        result = self.reserve(key="trial-reserve-0001")
        self.assertEqual(result["state"], "trial_reserved", result)
        self.assertEqual((result["stageChanged"], result["messageSent"], result["membershipCreated"]), (False, False, False))
        enrollment = self.env["dojo.class.enrollment"].browse(int(result["enrollmentId"]))
        self.assertEqual((enrollment.member_id, enrollment.session_id, enrollment.status), (member, self.session, "registered"))
        self.assertEqual(member.membership_state, "trial")
        self.assertEqual(lead.trial_session_id, self.session)
        self.assertEqual(lead.stage_id, stage)
        self.assertFalse(lead.dojo_member_id)
        self.assertEqual(self.counts(), before)
        self.assertTrue(self.reserve(key="trial-reserve-0001")["replayed"])
        receipt = self.env["dojo.hub.receipt"].search([("request_key", "=", "trial-reserve-0001")])
        self.assertEqual((receipt.lead_id, receipt.member_id, receipt.session_id), (lead, member, self.session))

    def test_reservation_rules_are_enforced_before_writing(self):
        self.assertEqual(self.reserve(member="active")["error"]["code"], "TRIAL_STUDENT_REVIEW_REQUIRED")
        self.reserve()
        self.assertEqual(self.reserve(member="other")["error"]["code"], "TRIAL_ALREADY_OPEN")
        self.assertEqual(self.reserve(lead="other")["error"]["code"], "TRIAL_ALREADY_OPEN")
        filler = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Trial filler",
            "company_id": self.company.id}).id, "company_id": self.company.id})
        self.env["dojo.class.enrollment"].with_context(skip_subscription_check=True).create({"session_id": self.second_session.id, "member_id": filler.id})
        before = self.env["dojo.hub.trial"].search_count([])
        self.assertEqual(self.reserve(lead="other", member="other", session=self.second_session)["error"]["code"], "BUSINESS_RULE_REVIEW_REQUIRED")
        self.assertEqual(self.env["dojo.hub.trial"].search_count([]), before)
        context = self.read("trial_context", {"leadId": str(self.leads["other"].id), "query": ""})
        stale = self.act("reserve_trial", {"leadId": str(self.leads["other"].id), "expectedLeadVersion": context["lead"]["version"] - 1,
            "memberId": str(self.members["other"].id), "sessionId": str(self.session.id), "expectedSessionVersion": 0, "reason": "x"})
        self.assertEqual(stale["error"]["code"], "VERSION_CONFLICT")
        other_company = self.env["res.company"].create({"name": "Foreign trial school"})
        foreign = self.env["dojo.member"].create({"partner_id": self.env["res.partner"].create({"name": "Foreign trial",
            "company_id": other_company.id}).id, "company_id": other_company.id, "membership_state": "lead"})
        self.assertEqual(self.act("reserve_trial", {"leadId": str(self.leads["other"].id), "expectedLeadVersion": 0,
            "memberId": str(foreign.id), "sessionId": str(self.session.id), "expectedSessionVersion": 0, "reason": "x"})["error"]["code"], "MEMBER_UNAVAILABLE")

    def test_cancel_before_class_restores_previous_state(self):
        result = self.reserve()
        row = self.trial_row()
        cancelled = self.act("cancel_trial", {"trialId": row["id"], "expectedVersion": row["version"], "reason": "Family rescheduled"})
        self.assertEqual(cancelled["state"], "trial_cancelled", cancelled)
        self.assertEqual(self.env["dojo.class.enrollment"].browse(int(result["enrollmentId"])).status, "cancelled")
        self.assertEqual(self.members["child"].membership_state, "lead")
        self.assertFalse(self.leads["child"].trial_session_id)
        again = self.reserve()
        self.assertEqual(again["state"], "trial_reserved", again)
        self.assertEqual(again["enrollmentId"], result["enrollmentId"])
        self.assertEqual(self.env["dojo.class.enrollment"].browse(int(result["enrollmentId"])).status, "registered")

    def test_outcome_records_native_attendance_without_no_show_automation(self):
        early = self.reserve()
        row = self.trial_row()
        self.assertEqual(self.act("record_trial_outcome", {"trialId": row["id"], "expectedVersion": row["version"],
            "outcome": "attended", "reason": "x"})["error"]["code"], "ATTENDANCE_WINDOW_CLOSED")
        stage, before = self.leads["child"].stage_id, self.counts()
        attended = self.attend()
        self.assertEqual(attended["state"], "trial_attended", attended)
        log = self.env["dojo.attendance.log"].browse(int(attended["attendanceId"]))
        self.assertEqual((log.status, log.member_id), ("present", self.members["child"]))
        self.assertFalse(log.notification_sent)
        self.assertEqual(self.env["dojo.class.enrollment"].browse(int(early["enrollmentId"])).attendance_state, "present")
        self.assertTrue(self.leads["child"].trial_attended)
        self.assertEqual(self.leads["child"].stage_id, stage)
        self.assertEqual(self.counts(), before)
        self.reserve(lead="other", member="other", session=self.second_session)
        self.start_class(self.second_session)
        row = self.trial_row("other")
        no_show = self.act("record_trial_outcome", {"trialId": row["id"], "expectedVersion": row["version"], "outcome": "no_show",
            "reason": "Did not arrive"})
        self.assertEqual(no_show["state"], "trial_no_show", no_show)
        self.assertFalse(self.leads["other"].no_show)
        self.assertFalse(self.leads["other"].trial_attended)

    def test_conversion_is_previewed_then_creates_only_the_reviewed_membership(self):
        self.reserve()
        trial = self.trial_row()
        self.assertEqual(self.read("conversion_preview", {"trialId": trial["id"], "planId": str(self.plan.id),
            "startDate": str(fields.Date.today())})["error"]["code"], "TRIAL_NOT_ATTENDED")
        self.attend()
        trial = self.trial_row()
        today = str(fields.Date.today())
        self.assertEqual(self.read("conversion_preview", {"trialId": trial["id"], "planId": str(self.plan.id),
            "startDate": "2020-01-01"})["error"]["code"], "INVALID_START_DATE")
        preview = self.read("conversion_preview", {"trialId": trial["id"], "planId": str(self.plan.id), "startDate": today})
        self.assertTrue(preview["coversTrialClass"], preview)
        self.assertEqual(preview["payer"], "Trial child")
        self.assertIn("Send a message", preview["doesNot"])
        payload = {"trialId": trial["id"], "expectedVersion": preview["version"], "planId": str(self.plan.id), "startDate": today,
            "reason": "Parent chose the monthly plan", "reviewToken": preview["reviewToken"]}
        self.assertEqual(self.act("convert_trial", {**payload, "reviewToken": "stale"})["error"]["code"], "CONVERSION_REVIEW_CHANGED")
        before = self.counts()
        converted = self.act("convert_trial", payload, key="trial-convert-0001")
        self.assertEqual(converted["state"], "trial_converted", converted)
        subscription = self.env["sale.subscription"].browse(int(converted["subscriptionId"]))
        self.assertEqual((subscription.member_id, subscription.plan_id, subscription.state), (self.members["child"], self.plan, "active"))
        self.assertEqual(self.members["child"].membership_state, "active")
        self.assertEqual(self.leads["child"].dojo_member_id, self.members["child"])
        after = self.counts()
        self.assertEqual(after["sale.subscription"], before["sale.subscription"] + 1)
        for model in ("mail.mail", "sms.sms", "res.users", "account.move"):
            if model in before:
                self.assertEqual(after[model], before[model], model)
        self.assertTrue(self.act("convert_trial", payload, key="trial-convert-0001")["replayed"])
        self.assertEqual(self.act("convert_trial", payload)["error"]["code"], "TRIAL_NOT_ATTENDED")
        self.env["dojo.kiosk.service"]._v2_eligible(self.kiosk, self.second_session, self.members["child"],
            self.env["dojo.class.enrollment"].with_context(skip_subscription_check=True).create(
                {"session_id": self.second_session.id, "member_id": self.members["child"].id}))

    def test_future_start_conversion_waits_for_the_native_start_job(self):
        self.reserve()
        self.attend()
        trial = self.trial_row()
        start = str(fields.Date.today() + timedelta(days=7))
        preview = self.read("conversion_preview", {"trialId": trial["id"], "planId": str(self.plan.id), "startDate": start})
        self.assertIn(start, preview["billing"])
        converted = self.act("convert_trial", {"trialId": trial["id"], "expectedVersion": preview["version"], "planId": str(self.plan.id),
            "startDate": start, "reason": "Starts next week", "reviewToken": preview["reviewToken"]})
        self.assertEqual(converted["membershipState"], "pending", converted)
        subscription = self.env["sale.subscription"].browse(int(converted["subscriptionId"]))
        self.assertEqual((subscription.stage_id.type, str(subscription.date_start)), ("pre", start))
        self.assertEqual(self.members["child"].membership_state, "active")

