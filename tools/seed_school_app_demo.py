import os
import random
import secrets
from datetime import date, datetime, time, timedelta

import pytz
from odoo import fields

if os.environ.get("DOJANG_DEMO_SEED") != "1":
    raise SystemExit("Refusing to add demo data. Set DOJANG_DEMO_SEED=1 on a demo database only.")

env = env(context=dict(env.context, tracking_disable=True, mail_create_nosubscribe=True, mail_notrack=True,
                       dojo_hub_staff_recorded=True, no_reset_password=True))
ICP = env["ir.config_parameter"].sudo()
if ICP.get_str("dojo_school_app.demo_seeded"):
    raise SystemExit("Demo data is already in this database.")

rng = random.Random(20261015)
company = env.company
company.partner_id.tz = company.partner_id.tz or os.environ.get("DOJANG_DEMO_TZ", "America/New_York")
if company.name == "My Company":
    company.name = os.environ.get("DOJANG_DEMO_SCHOOL", "Dojang Demo School")
tz = pytz.timezone(company.partner_id.tz)
today = datetime.now(tz).date()
password = os.environ.get("DOJANG_DEMO_PASSWORD") or secrets.token_urlsafe(12)

BELTS = [("White", "#f4f4f5", 12), ("Yellow", "#facc15", 16), ("Orange", "#fb923c", 18), ("Green", "#22c55e", 20),
         ("Blue", "#3b82f6", 24), ("Purple", "#a855f7", 28), ("Red", "#ef4444", 32), ("Brown", "#92400e", 36),
         ("Black", "#18181b", 48)]
ranks = env["dojo.belt.rank"].search([("company_id", "in", [False, company.id]), ("active", "=", True)], order="sequence")
if not ranks:
    for sequence, (name, color, threshold) in enumerate(BELTS, start=1):
        ranks |= env["dojo.belt.rank"].create({"name": name, "color": color, "sequence": sequence * 10,
                                               "attendance_threshold": threshold, "company_id": company.id})

programs = {name: env["dojo.program"].create({"name": name, "company_id": company.id})
            for name in ("Little Dragons", "Kids Taekwondo", "Teen & Adult Taekwondo")}
plans = {name: env["dojo.subscription.plan"].create({
    "name": name + " Unlimited", "company_id": company.id, "currency_id": company.currency_id.id,
    "price": 119 if name == "Little Dragons" else 149, "plan_type": "program", "auto_send_invoice": False,
    "program_ids": [(6, 0, [program.id])]}) for name, program in programs.items()}
stage = env["sale.subscription.stage"].search([("type", "=", "in_progress")], limit=1) or env["sale.subscription.stage"].create(
    {"name": "Active", "type": "in_progress", "in_progress": True})
pricelist = env["product.pricelist"].search([("currency_id", "=", company.currency_id.id)], limit=1) or env["product.pricelist"].create(
    {"name": "Standard", "currency_id": company.currency_id.id})


def user(name, login, groups):
    return env["res.users"].create({"name": name, "login": login, "password": password, "email": login,
                                    "company_id": company.id, "company_ids": [(6, 0, [company.id])],
                                    "group_ids": [(6, 0, [env.ref(g).id for g in groups])]})


owner = user("Paul Owner", "owner@dojang.demo", ["base.group_user", "dojo_core.group_dojo_admin"])
desk = user("Ava Front Desk", "frontdesk@dojang.demo", ["base.group_user", "dojo_school_app.group_dojo_front_desk"])
instructors = []
for name, login, pin in (("Master Kim", "kim@dojang.demo", "246810"), ("Sarah Park", "park@dojang.demo", "135790")):
    account = user(name, login, ["base.group_user", "dojo_core.group_dojo_instructor"])
    profile = env["dojo.instructor.profile"].create({"name": name, "user_id": account.id, "partner_id": account.partner_id.id,
                                                     "company_id": company.id})
    profile.employee_id.sudo().pin = pin
    instructors.append(profile)

SCHEDULE = [
    ("Little Dragons", "Little Dragons", (0, 2), time(16, 30), 45, 0),
    ("Kids Beginner", "Kids Taekwondo", (0, 2), time(17, 30), 60, 0),
    ("Kids Advanced", "Kids Taekwondo", (1, 3), time(17, 0), 60, 1),
    ("Teen & Adult", "Teen & Adult Taekwondo", (0, 2), time(18, 45), 75, 1),
    ("Sparring", "Teen & Adult Taekwondo", (1, 3), time(18, 15), 60, 0),
    ("Family Class", "Kids Taekwondo", (5,), time(10, 0), 60, 1),
]
templates = {}
for title, program, days, start, minutes, teacher in SCHEDULE:
    templates[title] = (env["dojo.class.template"].create({
        "name": title, "company_id": company.id, "program_id": programs[program].id, "duration_minutes": minutes,
        "max_capacity": 20, "instructor_profile_ids": [(6, 0, [instructors[teacher].id])]}), days, start, minutes, teacher)

FAMILIES = [
    ("Lee", "Grace Lee", [("Jordan Lee", 9, 2), ("Mia Lee", 6, 0)]),
    ("Chen", "David Chen", [("Maya Chen", 11, 4)]),
    ("Rivera", "Elena Rivera", [("Mateo Rivera", 8, 1), ("Sofia Rivera", 12, 3)]),
    ("Okafor", "Chidi Okafor", [("Ada Okafor", 10, 3), ("Tobi Okafor", 7, 1)]),
    ("Patel", "Priya Patel", [("Arjun Patel", 13, 5)]),
    ("Nguyen", "Linh Nguyen", [("Kevin Nguyen", 9, 2)]),
    ("Johnson", "Marcus Johnson", [("Devon Johnson", 12, 4), ("Kayla Johnson", 5, 0)]),
    ("Haddad", "Rami Haddad", [("Layla Haddad", 10, 2)]),
    ("Brooks", "Tanya Brooks", [("Eli Brooks", 8, 1)]),
]
ADULTS = [("Sarah Kim", 34, 3), ("James Walker", 41, 6), ("Nina Alvarez", 27, 2), ("Omar Farouk", 22, 5),
          ("Hannah Cole", 19, 7), ("Ben Ortiz", 30, 1), ("Rachel Green", 45, 0), ("Victor Hugo", 38, 8)]
parent_user = None
students = []


def birthday(age, index):
    month = today.month if index % 6 == 0 else rng.randint(1, 12)
    day = min(today.day + 3, 28) if index % 6 == 0 else rng.randint(1, 28)
    return date(today.year - age, month, day)


def enroll(name, age, belt, parent=None, index=0):
    partner = env["res.partner"].create({"name": name, "company_id": company.id, "parent_id": parent.id if parent else False,
                                         "email": False if parent else name.lower().replace(" ", ".") + "@example.com",
                                         "phone": False if parent else "555-01%02d" % index})
    state = "paused" if index == 7 else "trial" if index == 11 else "active"
    member = env["dojo.member"].create({"partner_id": partner.id, "company_id": company.id, "membership_state": state,
                                        "date_of_birth": birthday(age, index)})
    env.cr.execute("UPDATE dojo_member SET create_date = %s WHERE id = %s",
                   [datetime.combine(today - timedelta(days=60 + index * 21), time(12)), member.id])
    member.invalidate_recordset(["create_date"])
    awarded = today - timedelta(days=30 * (belt + 1) + 20)
    for step in range(min(belt, len(ranks) - 1) + 1):
        env["dojo.member.rank"].create({"member_id": member.id, "rank_id": ranks[step].id, "company_id": company.id,
                                        "date_awarded": awarded + timedelta(days=30 * step)})
    program = "Little Dragons" if age <= 6 else "Kids Taekwondo" if age <= 12 else "Teen & Adult Taekwondo"
    plan = plans[program]
    env["sale.subscription"].create({
        "partner_id": (parent.primary_guardian_id if parent else partner).id, "company_id": company.id,
        "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": member.id,
        "plan_id": plan.id, "date_start": today - timedelta(days=90),
        "date": today + timedelta(days=12) if index in (3, 14) else False})
    students.append((member, program))
    return member


index = 0
for family, guardian_name, kids in FAMILIES:
    guardian = env["res.partner"].create({"name": guardian_name, "company_id": company.id,
                                          "email": guardian_name.lower().replace(" ", ".") + "@example.com",
                                          "phone": "555-02%02d" % len(students)})
    household = env["res.partner"].create({"name": family + " Household", "is_household": True,
                                           "primary_guardian_id": guardian.id, "company_id": company.id})
    guardian.parent_id = household
    if family == "Lee":
        parent_user = env["res.users"].create({"name": guardian_name, "login": "parent@dojang.demo", "password": password,
                                               "partner_id": guardian.id, "company_id": company.id,
                                               "company_ids": [(6, 0, [company.id])],
                                               "group_ids": [(6, 0, [env.ref("base.group_portal").id,
                                                                     env.ref("dojo_core.group_dojo_parent_student").id])]})
    for name, age, belt in kids:
        index += 1
        enroll(name, age, belt, household, index)
for name, age, belt in ADULTS:
    index += 1
    enroll(name, age, belt, None, index)

kiosk = env["dojo.kiosk.config"].search([("company_id", "=", company.id), ("active", "=", True)], limit=1) or env["dojo.kiosk.config"].create(
    {"name": "Front Desk iPad", "pin_code": "321654", "company_id": company.id})

now = datetime.utcnow()
Session = env["dojo.class.session"]
Enrollment = env["dojo.class.enrollment"]
Log = env["dojo.attendance.log"]
for offset in range(-21, 15):
    day = today + timedelta(days=offset)
    for title, (template, days, start, minutes, teacher) in templates.items():
        if day.weekday() not in days:
            continue
        begin = tz.localize(datetime.combine(day, start)).astimezone(pytz.utc).replace(tzinfo=None)
        finish = begin + timedelta(minutes=minutes)
        session = Session.create({"template_id": template.id, "company_id": company.id, "start_datetime": begin,
                                  "end_datetime": finish, "capacity": 20, "state": "open",
                                  "instructor_profile_id": instructors[teacher].id})
        roster = [m for m, p in students if p == template.program_id.name and m.membership_state in ("active", "trial")]
        for member in roster:
            Enrollment.create({"session_id": session.id, "member_id": member.id})
        if finish < now:
            for member in roster:
                if rng.random() < 0.72:
                    Log.create({"session_id": session.id, "member_id": member.id,
                                "status": "late" if rng.random() < 0.1 else "present",
                                "checkin_datetime": begin + timedelta(minutes=rng.randint(-10, 8))})
            session.state = "done"

ICP.set_str("dojo_school_app.demo_seeded", fields.Datetime.to_string(fields.Datetime.now()))
env.cr.commit()
print("Demo school ready: %d students, %d classes, kiosk '%s'." % (len(students), Session.search_count([("company_id", "=", company.id)]), kiosk.name))
print("Kiosk link: /dojang/kiosk/%s" % kiosk.kiosk_token)
print("Sign-ins (password %s): owner@dojang.demo, frontdesk@dojang.demo, kim@dojang.demo, park@dojang.demo, parent@dojang.demo" % password)
print("Instructor PINs: Master Kim 246810, Sarah Park 135790")
