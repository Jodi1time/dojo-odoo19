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
kiosk = base["dojo.kiosk.config"].create({"name": "Demo kiosk", "pin_code": str(secrets.randbelow(900000)+100000), "company_id": company.id, "integration_enabled": True, "integration_companion_followup_enabled": True, "integration_tenant_ref": "dojang-demo-school", "integration_key_hash": hashlib.sha256(keys["DOJANG_KIOSK_GATEWAY_KEY"].encode()).hexdigest(), "integration_staff_key_hash": hashlib.sha256(keys["DOJANG_STAFF_GATEWAY_KEY"].encode()).hexdigest(), "integration_member_ids": [(6,0,members.ids)], "integration_session_ids": [(6,0,[session.id])]})
values = {"DOJANG_INTEGRATION_MODE": "odoo-test", "NEXT_PUBLIC_DEMO_MODE": "false", "DOJANG_PUBLIC_ORIGIN": "http://localhost:3000", "DOJANG_ODOO_URL": "http://127.0.0.1:8069", "DOJANG_KIOSK_TOKEN": kiosk.kiosk_token, **keys}
hub_fixture = {}
if "dojo.hub.site" in base:
    site = base["dojo.hub.site"].create({"name": "Demo companion hub", "company_id": company.id, "kiosk_config_id": kiosk.id,
        "workspace_ref": "11", "webhook_secret_env": "DOJANG_HUB_DEMO_WEBHOOK_SECRET"})
    password = secrets.token_urlsafe(48)
    user = base["res.users"].with_context(no_reset_password=True).create({"name": "Demo Hub Manager", "login": "hub-manager@example.invalid", "password": password,
        "company_id": company.id, "company_ids": [(6, 0, [company.id])], "group_ids": [(6, 0, [base.ref("base.group_portal").id])]})
    base["dojo.hub.grant"].create({"site_id": site.id, "user_id": user.id, "role": "manager"})
    guardian = base["res.partner"].create({"name": "Synthetic Verified Guardian", "company_id": company.id})
    base["dojo.hub.guardian"].create({"site_id": site.id, "partner_id": guardian.id, "member_ids": [(6, 0, [members[2].id])],
        "channel": "sms", "contact_ref": "42", "verification_ref": "synthetic-fixture-only", "allow_reply": True})
    values.update({"DOJANG_HUB_ENABLED": "true", "DOJANG_HUB_SITE_ID": str(site.id), "DOJANG_ODOO_DATABASE": env.cr.dbname,
        "DOJANG_HUB_DEMO_WEBHOOK_SECRET": secrets.token_urlsafe(48)})
    hub_fixture = {"hubSiteId": str(site.id), "hubLogin": user.login, "hubPassword": password}
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
