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
    company.name = os.environ.get("DOJANG_DEMO_SCHOOL", "United Family Taekwondo")
tz = pytz.timezone(company.partner_id.tz)
today = datetime.now(tz).date()
password = os.environ.get("DOJANG_DEMO_PASSWORD") or secrets.token_urlsafe(12)

BELTS = [("White", "#f4f4f5"), ("Yellow", "#facc15"), ("Advanced Yellow", "#fde047"), ("Orange", "#fb923c"),
         ("Green", "#22c55e"), ("Purple", "#a855f7"), ("Blue", "#3b82f6"), ("Brown", "#92400e"), ("Red", "#ef4444"),
         ("Advanced Red", "#dc2626"), ("Deputy Black", "#27272a"), ("Senior Deputy Black", "#18181b"),
         ("Black 1st Dan", "#09090b"), ("Black 2nd Dan", "#09090b"), ("Black 3rd Dan", "#09090b"), ("Black 4th Dan", "#09090b")]
ranks = env["dojo.belt.rank"].search([("company_id", "in", [False, company.id]), ("active", "=", True)], order="sequence")
if not ranks:
    for index, (name, color) in enumerate(BELTS, start=1):
        ranks |= env["dojo.belt.rank"].create({"name": name, "color": color, "sequence": index * 10,
                                               "attendance_threshold": 12 + index * 2, "company_id": company.id})

PROGRAM_OF = {"PEE WEE": "Kids", "CHILDREN BEGINNER": "Kids", "CHILDREN INTERMEDIATE": "Kids", "CHILDREN ADVANCED": "Kids",
              "TEEN/ADULT BEGINNER": "Teen & Adult", "TEEN/ADULT INTERMEDIATE & ADVANCED": "Teen & Adult",
              "BLACK BELT": "Black Belt", "BBC": "Black Belt", "TOURNAMENT SPARRING TEAM": "Black Belt",
              "TOURNAMENT FORMS TEAM": "Black Belt", "LEADERSHIP TRAINING": "Black Belt"}
programs = {name: env["dojo.program"].create({"name": name, "company_id": company.id})
            for name in dict.fromkeys(PROGRAM_OF.values())}
plans = {name: env["dojo.subscription.plan"].create({
    "name": name + " Unlimited", "company_id": company.id, "currency_id": company.currency_id.id,
    "price": 149, "plan_type": "program", "auto_send_invoice": False, "program_ids": [(6, 0, [program.id])]})
    for name, program in programs.items()}
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

SCHEDULE = {
    "PEE WEE": [(0, "16:15", "16:45"), (2, "16:15", "16:45"), (4, "16:15", "16:45")],
    "CHILDREN BEGINNER": [(0, "17:00", "17:40"), (2, "17:00", "17:40"), (4, "17:45", "18:25")],
    "CHILDREN INTERMEDIATE": [(1, "16:15", "16:55"), (3, "16:15", "16:55"), (4, "17:00", "17:40")],
    "CHILDREN ADVANCED": [(0, "17:45", "18:25"), (1, "17:00", "17:40"), (3, "17:00", "17:40")],
    "TEEN/ADULT INTERMEDIATE & ADVANCED": [(0, "18:30", "19:10"), (2, "18:30", "19:10"), (4, "18:30", "19:10")],
    "TEEN/ADULT BEGINNER": [(1, "18:30", "19:10"), (3, "18:30", "19:10")],
    "BLACK BELT": [(1, "17:45", "18:25"), (3, "17:45", "18:25"), (5, "11:00", "11:40")],
    "BBC": [(2, "17:45", "18:25")],
    "TOURNAMENT SPARRING TEAM": [(4, "19:15", "20:30")],
    "LEADERSHIP TRAINING": [(5, "09:45", "10:45")],
    "TOURNAMENT FORMS TEAM": [(5, "11:45", "13:00")],
}
templates = {}
for index, (title, slots) in enumerate(SCHEDULE.items()):
    templates[title] = env["dojo.class.template"].create({
        "name": title, "company_id": company.id, "program_id": programs[PROGRAM_OF[title]].id, "max_capacity": 24,
        "instructor_profile_ids": [(6, 0, [instructors[index % 2].id])]})

FAMILIES = [
    ("Lee", "Grace Lee", [("Jordan Lee", 9, 3, ["CHILDREN INTERMEDIATE"]), ("Mia Lee", 5, 0, ["PEE WEE"])]),
    ("Chen", "David Chen", [("Maya Chen", 11, 6, ["CHILDREN ADVANCED"])]),
    ("Rivera", "Elena Rivera", [("Mateo Rivera", 8, 1, ["CHILDREN BEGINNER"]), ("Sofia Rivera", 12, 5, ["CHILDREN ADVANCED"])]),
    ("Okafor", "Chidi Okafor", [("Ada Okafor", 10, 4, ["CHILDREN INTERMEDIATE"]), ("Tobi Okafor", 6, 1, ["PEE WEE"])]),
    ("Patel", "Priya Patel", [("Arjun Patel", 13, 9, ["TEEN/ADULT INTERMEDIATE & ADVANCED", "TOURNAMENT SPARRING TEAM"])]),
    ("Nguyen", "Linh Nguyen", [("Kevin Nguyen", 9, 2, ["CHILDREN BEGINNER"])]),
    ("Johnson", "Marcus Johnson", [("Devon Johnson", 12, 7, ["CHILDREN ADVANCED", "TOURNAMENT FORMS TEAM"]),
                                   ("Kayla Johnson", 5, 0, ["PEE WEE"])]),
    ("Haddad", "Rami Haddad", [("Layla Haddad", 10, 3, ["CHILDREN INTERMEDIATE"])]),
    ("Brooks", "Tanya Brooks", [("Eli Brooks", 7, 1, ["CHILDREN BEGINNER"])]),
]
ADULTS = [("Sarah Kim", 34, 6, ["TEEN/ADULT INTERMEDIATE & ADVANCED"]), ("James Walker", 41, 12, ["BLACK BELT", "BBC", "LEADERSHIP TRAINING"]),
          ("Nina Alvarez", 27, 2, ["TEEN/ADULT BEGINNER"]), ("Omar Farouk", 22, 10, ["BLACK BELT", "BBC", "TOURNAMENT SPARRING TEAM"]),
          ("Hannah Cole", 19, 13, ["BLACK BELT", "LEADERSHIP TRAINING", "TOURNAMENT FORMS TEAM"]),
          ("Ben Ortiz", 30, 1, ["TEEN/ADULT BEGINNER"]), ("Rachel Green", 45, 0, ["TEEN/ADULT BEGINNER"]),
          ("Victor Hugo", 38, 8, ["TEEN/ADULT INTERMEDIATE & ADVANCED"])]
students = []


def birthday(age, index):
    if index % 6 == 0:
        return date(today.year - age, today.month, min(today.day + 3, 28))
    return date(today.year - age, rng.randint(1, 12), rng.randint(1, 28))


def enroll(name, age, belt, classes, parent=None, index=0):
    partner = env["res.partner"].create({"name": name, "company_id": company.id, "parent_id": parent.id if parent else False,
                                         "is_student": True,
                                         "email": False if parent else name.lower().replace(" ", ".") + "@example.com",
                                         "phone": False if parent else "555-01%02d" % index})
    state = "paused" if index == 7 else "trial" if index == 11 else "active"
    member = env["dojo.member"].create({"partner_id": partner.id, "company_id": company.id, "membership_state": state,
                                        "date_of_birth": birthday(age, index)})
    env.cr.execute("UPDATE dojo_member SET create_date = %s WHERE id = %s",
                   [datetime.combine(today - timedelta(days=60 + index * 21), time(12)), member.id])
    member.invalidate_recordset(["create_date"])
    awarded = today - timedelta(days=45 * (belt + 1))
    for step in range(min(belt, len(ranks) - 1) + 1):
        env["dojo.member.rank"].create({"member_id": member.id, "rank_id": ranks[step].id, "company_id": company.id,
                                        "date_awarded": awarded + timedelta(days=45 * step)})
    for program in dict.fromkeys(PROGRAM_OF[title] for title in classes):
        plan = plans[program]
        env["sale.subscription"].create({
            "partner_id": (parent.primary_guardian_id if parent else partner).id, "company_id": company.id,
            "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "stage_id": stage.id, "member_id": member.id,
            "plan_id": plan.id, "date_start": today - timedelta(days=90),
            "date": today + timedelta(days=12) if index in (3, 14) else False})
    for title in classes:
        templates[title].write({"course_member_ids": [(4, member.id)]})
    students.append((member, classes))
    return member


index = 0
for family, guardian_name, kids in FAMILIES:
    guardian = env["res.partner"].create({"name": guardian_name, "company_id": company.id, "is_guardian": True,
                                          "email": guardian_name.lower().replace(" ", ".") + "@example.com",
                                          "phone": "555-02%02d" % len(students)})
    household = env["res.partner"].create({"name": family + " Household", "is_household": True,
                                           "primary_guardian_id": guardian.id, "company_id": company.id})
    guardian.parent_id = household
    if family == "Lee":
        env["res.users"].create({"name": guardian_name, "login": "parent@dojang.demo", "password": password,
                                 "partner_id": guardian.id, "company_id": company.id, "company_ids": [(6, 0, [company.id])],
                                 "group_ids": [(6, 0, [env.ref("base.group_portal").id,
                                                       env.ref("dojo_core.group_dojo_parent_student").id])]})
    for name, age, belt, classes in kids:
        index += 1
        enroll(name, age, belt, classes, household, index)
for name, age, belt, classes in ADULTS:
    index += 1
    enroll(name, age, belt, classes, None, index)

kiosk = env["dojo.kiosk.config"].search([("company_id", "=", company.id), ("active", "=", True)], limit=1) or env["dojo.kiosk.config"].create(
    {"name": "Front Desk iPad", "pin_code": "321654", "company_id": company.id})

now = datetime.utcnow()
Session = env["dojo.class.session"]
Enrollment = env["dojo.class.enrollment"]
Log = env["dojo.attendance.log"]
for offset in range(-21, 15):
    day = today + timedelta(days=offset)
    for index, (title, slots) in enumerate(SCHEDULE.items()):
        for weekday, start, end in slots:
            if day.weekday() != weekday:
                continue
            begin = tz.localize(datetime.combine(day, time.fromisoformat(start))).astimezone(pytz.utc).replace(tzinfo=None)
            finish = tz.localize(datetime.combine(day, time.fromisoformat(end))).astimezone(pytz.utc).replace(tzinfo=None)
            session = Session.create({"template_id": templates[title].id, "company_id": company.id, "start_datetime": begin,
                                      "end_datetime": finish, "capacity": 24, "state": "open",
                                      "instructor_profile_id": instructors[index % 2].id})
            roster = [member for member, classes in students if title in classes and member.membership_state in ("active", "trial")]
            for member in roster:
                if not Enrollment.search_count([("session_id", "=", session.id), ("member_id", "=", member.id)]):
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
