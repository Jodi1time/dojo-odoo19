"""Create synthetic check-in, recovery and parent-report students in a NEW test database."""
import hashlib
import json
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from odoo import fields

if "env" not in globals() or not env.cr.dbname.startswith("dojang_demo_"):
    raise RuntimeError("Only a new synthetic dojang_demo_* database may be seeded.")
output = Path(os.environ["DOJANG_ENV_OUTPUT"])
fixture_output = Path(os.environ["DOJANG_FIXTURE_OUTPUT"])
if output.exists() or fixture_output.exists():
    raise RuntimeError("Refusing to overwrite existing credentials or fixtures.")
if env["dojo.kiosk.config"].sudo().search_count([("integration_tenant_ref", "=", "dojang-demo-school")]):
    raise RuntimeError("Demo already exists. Start a separate disposable database.")
base = env(context=dict(env.context, tracking_disable=True, mail_create_nosubscribe=True))
company = base.company
# Synthetic rehearsal explicitly exercises reviewed, not auto-approved HR shifts.
company.attendance_validation = "manual_validation"
keys = {name: secrets.token_urlsafe(48) for name in ("DOJANG_COOKIE_SECRET", "DOJANG_KIOSK_GATEWAY_KEY", "DOJANG_STAFF_GATEWAY_KEY", "DOJANG_KIOSK_PAIR_KEY", "DOJANG_STAFF_PAIR_KEY")}
program = base["dojo.program"].create({"name": "Demo program", "company_id": company.id})
members = base["dojo.member"]
for name in ("Demo Student", "Recovery Student", "Followup Student"):
    partner = base["res.partner"].create({"name": name, "email": name.split()[0].lower()+"@example.invalid"})
    members |= base["dojo.member"].create({"partner_id": partner.id, "company_id": company.id, "membership_state": "active"})
template = base["dojo.class.template"].create({"name": "Demo session", "company_id": company.id, "program_id": program.id, "auto_enroll_members": False, "course_member_ids": [(6,0,members.ids)]})
now = fields.Datetime.now()
session = base["dojo.class.session"].create({"template_id": template.id, "company_id": company.id, "start_datetime": now, "end_datetime": now + timedelta(hours=2), "state": "open", "capacity": 3})
plan = base["dojo.subscription.plan"].create({"name": "Demo plan", "company_id": company.id, "currency_id": company.currency_id.id, "price": 0, "plan_type": "program", "auto_send_invoice": False, "program_ids": [(6,0,[program.id])]})
stage = base["sale.subscription.stage"].create({"name": "Demo active", "type": "in_progress", "in_progress": True})
pricelist = base["product.pricelist"].create({"name": "Demo pricelist", "currency_id": company.currency_id.id})
for member in members:
    base["sale.subscription"].create({"partner_id": member.partner_id.id, "company_id": company.id, "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": member.id, "plan_id": plan.id})
    base["dojo.class.enrollment"].create({"session_id": session.id, "member_id": member.id, "status": "registered", "attendance_state": "pending"})
instructor_pin = str(secrets.randbelow(900000)+100000)
kiosk = base["dojo.kiosk.config"].create({"name": "Demo kiosk", "pin_code": instructor_pin, "company_id": company.id, "integration_enabled": True, "integration_companion_followup_enabled": True, "integration_tenant_ref": "dojang-demo-school", "integration_key_hash": hashlib.sha256(keys["DOJANG_KIOSK_GATEWAY_KEY"].encode()).hexdigest(), "integration_staff_key_hash": hashlib.sha256(keys["DOJANG_STAFF_GATEWAY_KEY"].encode()).hexdigest(), "integration_member_ids": [(6,0,members.ids)], "integration_session_ids": [(6,0,[session.id])]})
values = {"DOJANG_INTEGRATION_MODE": "odoo-test", "NEXT_PUBLIC_DEMO_MODE": "false", "DOJANG_PUBLIC_ORIGIN": "http://localhost:3000", "DOJANG_ODOO_URL": "http://127.0.0.1:8069", "DOJANG_KIOSK_TOKEN": kiosk.kiosk_token, **keys}
hub_fixture = {}
if "dojo.hub.site" in base:
    makeup = base["dojo.class.session"].create({"template_id": template.id, "company_id": company.id,
        "start_datetime": now + timedelta(days=2), "end_datetime": now + timedelta(days=2, hours=1), "state": "open", "capacity": 3})
    site = base["dojo.hub.site"].create({"name": "Demo companion hub", "company_id": company.id, "kiosk_config_id": kiosk.id,
        "workspace_ref": "11", "webhook_secret_env": "DOJANG_HUB_DEMO_WEBHOOK_SECRET"})
    password = secrets.token_urlsafe(48)
    user = base["res.users"].with_context(no_reset_password=True).create({"name": "Demo Hub Manager", "login": "hub-manager@example.invalid", "password": password,
        "company_id": company.id, "company_ids": [(6, 0, [company.id])], "group_ids": [(6, 0, [base.ref("base.group_portal").id])]})
    base["dojo.hub.grant"].create({"site_id": site.id, "user_id": user.id, "role": "manager"})
    instructor = base["dojo.instructor.profile"].create({"name": "Demo Instructor", "user_id": user.id,
        "partner_id": user.partner_id.id, "company_id": company.id})
    session.instructor_profile_id = instructor
    prior_shift = base["hr.attendance"].create({"employee_id": instructor.employee_id.id,
        "check_in": now-timedelta(hours=5), "check_out": now-timedelta(hours=3), "out_mode": "auto_check_out"})
    guardian = base["res.partner"].create({"name": "Synthetic Verified Guardian", "company_id": company.id})
    base["dojo.hub.guardian"].create({"site_id": site.id, "partner_id": guardian.id, "member_ids": [(6, 0, [members[2].id])],
        "channel": "sms", "contact_ref": "42", "verification_ref": "synthetic-fixture-only", "allow_reply": True})
    course = base["dojo.class.template"].create({"name": "Rehearsal course", "company_id": company.id, "program_id": program.id,
        "auto_enroll_members": True})
    course_sessions = base["dojo.class.session"]
    for days in (3, 5):
        course_sessions |= base["dojo.class.session"].create({"template_id": course.id, "company_id": company.id,
            "start_datetime": now + timedelta(days=days), "end_datetime": now + timedelta(days=days, hours=1), "state": "open", "capacity": 10})
    admin_members = base["dojo.member"]
    for name in ("Course Student A", "Course Student B", "Archive Student"):
        admin_members |= base["dojo.member"].create({"partner_id": base["res.partner"].create({"name": name, "company_id": company.id}).id,
            "company_id": company.id, "membership_state": "active"})
    base["dojo.class.enrollment"].with_context(skip_subscription_check=True).create({"session_id": course_sessions[0].id,
        "member_id": admin_members[2].id, "status": "registered", "attendance_state": "pending"})
    review_class = base["dojo.class.template"].create({"name": "Attendance review class", "company_id": company.id,
        "program_id": program.id, "auto_enroll_members": False, "course_member_ids": [(6, 0, members.ids)]})
    review_session = base["dojo.class.session"].create({"template_id": review_class.id, "company_id": company.id,
        "start_datetime": now - timedelta(hours=26), "end_datetime": now - timedelta(hours=25), "state": "open", "capacity": 10})
    for member in members:
        base["dojo.class.enrollment"].create({"session_id": review_session.id, "member_id": member.id, "status": "registered", "attendance_state": "pending"})
    trial_template = base["dojo.class.template"].create({"name": "Trial class", "company_id": company.id,
        "program_id": program.id, "auto_enroll_members": False})
    trial_session = base["dojo.class.session"].create({"template_id": trial_template.id, "company_id": company.id,
        "start_datetime": now + timedelta(minutes=14), "end_datetime": now + timedelta(minutes=74), "state": "open", "capacity": 10})
    trial_member = base["dojo.member"].create({"partner_id": base["res.partner"].create({"name": "Trial Rehearsal Student",
        "company_id": company.id, "email": "trial-rehearsal-student@example.invalid"}).id, "company_id": company.id, "membership_state": "lead"})
    trial_lead = base["crm.lead"].create({"name": "Trial Rehearsal Enquiry", "company_id": company.id, "type": "opportunity",
        "contact_name": "Trial Rehearsal Parent", "email_from": "trial-rehearsal-parent@example.invalid"})
    belts = [base["dojo.belt.rank"].create({"name": name, "sequence": sequence, "company_id": company.id, "attendance_threshold": 2,
        "max_stripes": 4}) for name, sequence in (("White Belt", 10), ("Yellow Belt", 20))]
    practice = base["dojo.class.template"].create({"name": "Testing practice class", "company_id": company.id,
        "program_id": program.id, "auto_enroll_members": False})
    testing_members = base["dojo.member"]
    for name in ("Testing Student A", "Testing Student B"):
        testing_members |= base["dojo.member"].create({"partner_id": base["res.partner"].create({"name": name, "company_id": company.id}).id,
            "company_id": company.id, "membership_state": "active"})
    for member in testing_members:
        base["dojo.member.rank"].create({"member_id": member.id, "rank_id": belts[0].id, "date_awarded": fields.Date.today() - timedelta(days=60)})
    for days in (10, 11):
        practice_session = base["dojo.class.session"].create({"template_id": practice.id, "company_id": company.id,
            "start_datetime": now - timedelta(days=days), "end_datetime": now - timedelta(days=days, hours=-1), "state": "open", "capacity": 10})
        for member in testing_members:
            base["dojo.attendance.log"].with_context(dojo_hub_staff_recorded=True).create({"session_id": practice_session.id,
                "member_id": member.id, "status": "present", "checkin_datetime": practice_session.start_datetime})
    guardian_template = base["dojo.class.template"].create({"name": "Guardian class", "company_id": company.id,
        "program_id": program.id, "auto_enroll_members": False})
    guardian_session = base["dojo.class.session"].create({"template_id": guardian_template.id, "company_id": company.id,
        "start_datetime": now + timedelta(minutes=20), "end_datetime": now + timedelta(minutes=80), "state": "open", "capacity": 10})
    family = base["res.partner"].create({"name": "Family Rehearsal", "is_household": True, "company_id": company.id})
    guardian_parent = base["res.partner"].create({"name": "Guardian Rehearsal Parent", "parent_id": family.id, "is_guardian": True,
        "company_id": company.id, "phone": "512-555-0177"})
    family.primary_guardian_id = guardian_parent
    guardian_child = base["dojo.member"].create({"partner_id": base["res.partner"].create({"name": "Mia Rehearsal", "parent_id": family.id,
        "is_minor": True, "company_id": company.id}).id, "company_id": company.id, "membership_state": "active"})
    base["sale.subscription"].create({"partner_id": guardian_parent.id, "company_id": company.id, "template_id": plan.template_id.id,
        "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": guardian_child.id, "plan_id": plan.id})
    base["dojo.class.enrollment"].create({"session_id": guardian_session.id, "member_id": guardian_child.id, "status": "registered", "attendance_state": "pending"})
    kiosk.write({"integration_session_ids": [(4, guardian_session.id)], "integration_member_ids": [(4, guardian_child.id)]})
    values["NEXT_PUBLIC_DOJANG_GUARDIAN_CHECKIN"] = "true"
    twilio_test = os.environ.get("DOJANG_TWILIO_TEST") == "1"
    if twilio_test:
        site.write({"twilio_account_sid_env": "TWILIO_TEST_ACCOUNT_SID", "twilio_auth_token_env": "TWILIO_TEST_AUTH_TOKEN",
            "twilio_from_number": "+15005550006"})
    owner_password = secrets.token_urlsafe(48)
    owner = base["res.users"].with_context(no_reset_password=True).create({"name": "Demo Hub Owner", "login": "hub-owner@example.invalid",
        "password": owner_password, "company_id": company.id, "company_ids": [(6, 0, [company.id])],
        "group_ids": [(6, 0, [base.ref("base.group_portal").id])]})
    base["dojo.hub.grant"].create({"site_id": site.id, "user_id": owner.id, "role": "owner"})
    refund_partner = base["res.partner"].create({"name": "Refund Student", "company_id": company.id})
    refund_member = base["dojo.member"].create({"partner_id": refund_partner.id, "company_id": company.id, "membership_state": "active"})
    refund_subscription = base["sale.subscription"].create({"partner_id": refund_partner.id, "company_id": company.id, "template_id": plan.template_id.id,
        "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": refund_member.id, "plan_id": plan.id})
    sales = base["account.journal"].search([("type", "=", "sale"), ("company_id", "=", company.id)], limit=1)
    cash = base["account.journal"].search([("type", "in", ["cash", "bank"]), ("company_id", "=", company.id)], order="type", limit=1)
    refund_invoice = base["account.move"].create({"move_type": "out_invoice", "partner_id": refund_partner.id, "company_id": company.id,
        "journal_id": sales.id, "invoice_date": fields.Date.today(), "subscription_id": refund_subscription.id,
        "invoice_line_ids": [(0, 0, {"name": "Synthetic tuition", "quantity": 1, "price_unit": 120, "tax_ids": [(5, 0, 0)]})]})
    refund_invoice.action_post()
    base["account.payment.register"].with_context(active_model="account.move", active_ids=refund_invoice.ids).create({"journal_id": cash.id})._create_payments()
    values.update({"DOJANG_HUB_ENABLED": "true", "DOJANG_HUB_SITE_ID": str(site.id), "DOJANG_ODOO_DATABASE": env.cr.dbname,
        "DOJANG_HUB_DEMO_WEBHOOK_SECRET": secrets.token_urlsafe(48)})
    hub_fixture = {"hubSiteId": str(site.id), "hubLogin": user.login, "hubPassword": password, "makeupSessionId": str(makeup.id),
        "instructorPin": instructor_pin, "priorShiftId": str(prior_shift.id), "courseTemplateId": str(course.id),
        "courseMemberIds": [str(m.id) for m in admin_members[:2]], "archiveMemberId": str(admin_members[2].id),
        "attendanceSessionId": str(review_session.id), "trialLeadId": str(trial_lead.id), "trialMemberId": str(trial_member.id),
        "trialSessionId": str(trial_session.id), "planId": str(plan.id), "testingMemberIds": [str(m.id) for m in testing_members],
        "guardianChildId": str(guardian_child.id), "guardianId": str(guardian_parent.id), "guardianSessionId": str(guardian_session.id),
        "hubOwnerLogin": owner.login, "hubOwnerPassword": owner_password, "refundMemberId": str(refund_member.id), "refundInvoice": refund_invoice.name, "twilioTest": twilio_test}
for value in values.values():
    if not re.fullmatch(r"[A-Za-z0-9_:/.-]+", value): raise RuntimeError("Invalid environment value")
fd = os.open(str(output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
try:
    with os.fdopen(fd, "w") as stream:
        for key, value in values.items(): stream.write(f"{key}={value}\n")
    fixture_fd = os.open(str(fixture_output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fixture_fd, "w") as fixture_stream:
        json.dump({"memberId": str(members[0].id), "offlineMemberId": str(members[1].id), "followupMemberId": str(members[2].id), "sessionId": str(session.id), "student": "Demo S.", "sessionTitle": "Demo session", **hub_fixture}, fixture_stream)
    env.cr.commit()
except Exception:
    output.unlink(missing_ok=True)
    fixture_output.unlink(missing_ok=True)
    raise
print("Synthetic fixture created. Credentials remain in a mode-0600 file and are not printed.")
