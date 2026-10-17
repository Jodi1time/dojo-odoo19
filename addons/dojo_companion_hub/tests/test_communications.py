from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCommunicationsControls(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.us")
        cls.program = cls.env["dojo.program"].create({"name": "Comms program", "company_id": cls.company.id})
        cls.template = cls.env["dojo.class.template"].create({"name": "Comms class", "company_id": cls.company.id,
            "program_id": cls.program.id, "auto_enroll_members": False})
        household = cls.env["res.partner"].create({"name": "Family Comms", "is_household": True, "company_id": cls.company.id})
        cls.guardian = cls.env["res.partner"].create({"name": "Comms Parent", "parent_id": household.id, "is_guardian": True,
            "company_id": cls.company.id, "phone": "+1 512 555 0142", "email": "comms-parent@example.invalid"})
        household.primary_guardian_id = cls.guardian
        cls.members = {}
        for key, values in (("child", {"parent_id": household.id, "is_minor": True}), ("sibling", {"parent_id": household.id, "is_minor": True}),
                            ("adult", {"phone": "+1 512 555 0143", "email": "comms-adult@example.invalid"}), ("unreachable", {})):
            partner = cls.env["res.partner"].create({"name": "Comms " + key, "company_id": cls.company.id, **values})
            cls.members[key] = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id, "membership_state": "active"})
        cls.template.course_member_ids = [(6, 0, [m.id for m in cls.members.values()])]
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Comms kiosk", "pin_code": "321654", "company_id": cls.company.id})
        cls.site = cls.env["dojo.hub.site"].create({"name": "Comms test", "company_id": cls.company.id,
            "kiosk_config_id": cls.kiosk.id, "workspace_ref": "18"})
        cls.users = {}
        for role in ("owner", "manager"):
            cls.users[role] = cls.env["res.users"].with_context(no_reset_password=True).create({"name": "Comms " + role,
                "login": "comms-" + role + "@example.invalid", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])], "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
            cls.env["dojo.hub.grant"].create({"site_id": cls.site.id, "user_id": cls.users[role].id, "role": role})

    def setUp(self):
        super().setUp()
        self.keys = 0

    def act(self, op, payload, role="manager", key=None):
        if key is None:
            self.keys += 1
            key = "comms-request-%04d" % self.keys
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, key)

    def read(self, op, payload, role="manager"):
        return self.env["dojo.hub.service"].with_user(self.users[role])._dispatch(str(self.site.id), op, payload, None)

    def outbound(self):
        return (self.env["mail.mail"].sudo().search_count([]), self.env["sms.sms"].sudo().search_count([]))

    def consent(self, channel="sms", purpose="marketing", state="granted", partner=None, member="child"):
        return self.act("record_consent", {"memberId": str(self.members[member].id), "partnerId": str((partner or self.guardian).id),
            "channel": channel, "purpose": purpose, "state": state, "source": "paper_form", "evidence": "Signed enrolment form"})

    def payload(self, purpose="marketing", channel="sms"):
        return {"channel": channel, "purpose": purpose, "audience": "template:%s" % self.template.id, "body": "Belt testing is on Saturday."}

    def test_consent_is_recorded_with_history_and_updates_native_opt_out(self):
        before = self.outbound()
        overview = self.read("consent_overview", {"memberId": str(self.members["child"].id)})
        self.assertEqual({c["name"] for c in overview["contacts"]}, {"Comms child", "Comms Parent"})
        granted = self.consent()
        self.assertEqual((granted["state"], granted["messageSent"]), ("consent_recorded", False), granted)
        self.assertEqual(self.consent()["error"]["code"], "CONSENT_UNCHANGED")
        self.assertIsNone(self.consent(purpose="service", state="withdrawn")["nativeOptOutUpdated"])
        withdrawn = self.consent(state="withdrawn")
        self.assertEqual(withdrawn["nativeOptOutUpdated"], "phone_opt_out", withdrawn)
        self.assertTrue(self.env["phone.blacklist"].sudo().search_count([("number", "=", self.guardian.phone_sanitized)]))
        sms = next(c for c in self.read("consent_overview", {"memberId": str(self.members["child"].id)})["contacts"] if c["name"] == "Comms Parent")
        row = next(ch for ch in sms["channels"] if ch["channel"] == "sms")
        self.assertTrue(row["nativeOptOut"])
        self.assertEqual(row["address"], "•••0142")
        self.assertEqual(len(sms["history"]), 3)
        self.assertEqual(self.consent(partner=self.members["adult"].partner_id)["error"]["code"], "CONTACT_UNAVAILABLE")
        self.assertEqual(self.consent(member="unreachable", partner=self.members["unreachable"].partner_id)["error"]["code"], "CONTACT_ADDRESS_REQUIRED")
        self.assertEqual(self.outbound(), before)

    def test_broadcast_freezes_consented_audience_and_queues_without_sending(self):
        before = self.outbound()
        preview = self.read("broadcast_preview", self.payload())
        self.assertEqual(preview["recipientCount"], 0)
        self.assertEqual(preview["excludedCounts"], {"no_marketing_consent": 3, "no_contact": 1})
        self.consent()
        self.consent(partner=self.members["adult"].partner_id, member="adult")
        preview = self.read("broadcast_preview", self.payload())
        self.assertEqual(preview["recipientCount"], 2, preview)
        parent = next(s for s in preview["sample"] if s["name"] == "Comms Parent")
        self.assertEqual(parent["students"], 2)
        wrong = self.act("create_broadcast", {**self.payload(), "name": "Testing day", "reason": "Event notice", "reviewToken": "x" * 64})
        self.assertEqual(wrong["error"]["code"], "BROADCAST_REVIEW_CHANGED")
        created = self.act("create_broadcast", {**self.payload(), "name": "Testing day", "reason": "Event notice", "reviewToken": preview["reviewToken"]},
            key="comms-broadcast-0001")
        self.assertEqual((created["state"], created["recipients"], created["messageSent"]), ("broadcast_awaiting_approval", 2, False), created)
        self.assertTrue(self.act("create_broadcast", {**self.payload(), "name": "Testing day", "reason": "Event notice",
            "reviewToken": preview["reviewToken"]}, key="comms-broadcast-0001")["replayed"])
        self.consent(partner=self.members["adult"].partner_id, member="adult", state="withdrawn")
        approved = self.act("approve_broadcast", {"broadcastId": created["broadcastId"], "expectedVersion": created["version"], "reason": "Checked wording"}, role="owner")
        self.assertEqual((approved["state"], approved["queued"], approved["excludedAtApproval"], approved["messageSent"]),
            ("broadcast_approved", 1, 1, False), approved)
        self.assertEqual(approved["provider"], "not_configured")
        row = self.env["dojo.hub.broadcast"].sudo().browse(int(created["broadcastId"]))
        self.assertEqual(sorted(row.recipient_ids.mapped("state")), ["excluded", "excluded", "queued"])
        self.assertEqual(self.outbound(), before)
        context = self.read("broadcast_context", {})
        self.assertEqual(context["broadcasts"][0]["counts"], {"queued": 1, "excluded": 2})
        self.assertEqual(self.act("approve_broadcast", {"broadcastId": created["broadcastId"], "expectedVersion": approved["version"],
            "reason": "x"})["error"]["code"], "BROADCAST_NOT_PENDING")
        cancelled = self.act("cancel_broadcast", {"broadcastId": created["broadcastId"], "expectedVersion": approved["version"], "reason": "Date moved"})
        self.assertEqual(cancelled["state"], "broadcast_cancelled", cancelled)
        self.assertEqual(set(row.recipient_ids.mapped("state")), {"excluded"})

    def test_service_notices_respect_opt_out_and_large_audiences_stop(self):
        preview = self.read("broadcast_preview", self.payload(purpose="service"))
        self.assertEqual(preview["recipientCount"], 2)
        self.consent(purpose="service", state="withdrawn")
        preview = self.read("broadcast_preview", self.payload(purpose="service"))
        self.assertEqual(preview["recipientCount"], 1)
        self.assertEqual(preview["excludedCounts"]["opted_out"], 2)
        self.assertEqual(self.read("broadcast_preview", {**self.payload(), "audience": "state:everyone"})["error"]["code"], "INVALID_COMMAND")

    def test_automations_are_listed_and_only_owners_pause_them(self):
        overview = self.read("automation_overview", {})
        self.assertFalse(overview["canManage"])
        rules = [a for a in overview["automations"] if a["module"] == "dojo_crm" and a["kind"] == "Rule"]
        self.assertTrue(rules)
        target = rules[0]
        payload = {"automationId": target["id"], "active": False, "expectedVersion": target["version"], "reason": "Pause during migration"}
        self.assertEqual(self.act("set_automation_active", payload)["error"]["status"], 403)
        paused = self.act("set_automation_active", payload, role="owner")
        self.assertEqual(paused["state"], "automation_paused", paused)
        record = self.env["base.automation"].with_context(active_test=False).browse(int(target["id"].split(":")[1]))
        self.assertFalse(record.active)
        self.assertEqual(self.act("set_automation_active", {**payload, "expectedVersion": paused["version"]}, role="owner")["error"]["code"], "AUTOMATION_UNCHANGED")
        foreign = self.env["base.automation"].sudo().search([], limit=1).filtered(lambda a: a.id not in [int(r["id"].split(":")[1]) for r in rules])
        if foreign:
            self.assertEqual(self.act("set_automation_active", {**payload, "automationId": "base.automation:%s" % foreign.id}, role="owner")["error"]["code"],
                "AUTOMATION_UNAVAILABLE")
        resumed = self.act("set_automation_active", {**payload, "active": True, "expectedVersion": paused["version"]}, role="owner")
        self.assertEqual(resumed["state"], "automation_resumed")
