from datetime import date, timedelta

from odoo import fields


class SchoolFixture:

    @classmethod
    def build_school(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.company.partner_id.tz = "UTC"
        cls.program = cls.env["dojo.program"].create({"name": "Kids Taekwondo", "company_id": cls.company.id})
        cls.white = cls.env["dojo.belt.rank"].create({"name": "White", "sequence": 1, "color": "#ffffff", "company_id": cls.company.id})
        cls.yellow = cls.env["dojo.belt.rank"].create({"name": "Yellow", "sequence": 2, "color": "#facc15", "company_id": cls.company.id})
        cls.plan = cls.env["dojo.subscription.plan"].create({
            "name": "Kids Unlimited", "company_id": cls.company.id, "currency_id": cls.company.currency_id.id, "price": 0,
            "plan_type": "program", "auto_send_invoice": False, "program_ids": [(6, 0, [cls.program.id])]})
        cls.stage = cls.env["sale.subscription.stage"].create({"name": "School active", "type": "in_progress", "in_progress": True})
        cls.pricelist = cls.env["product.pricelist"].create({"name": "School price", "currency_id": cls.company.currency_id.id})
        cls.parent_user = cls.make_user("parent", ["base.group_portal", "dojo_core.group_dojo_parent_student"])
        household = cls.env["res.partner"].create({"name": "Lee Household", "is_household": True,
                                                    "primary_guardian_id": cls.parent_user.partner_id.id,
                                                    "company_id": cls.company.id})
        cls.jordan = cls.make_member("Jordan Lee", "active", parent=household, born=date(2015, 3, 4), rank=cls.white,
                                subscribed=True, email="jordan@example.invalid")
        cls.maya = cls.make_member("Maya Chen", "active", born=date(2014, 7, 9), rank=cls.yellow, subscribed=True)
        cls.devon = cls.make_member("Devon Reed", "paused", rank=cls.white, subscribed=True)
        cls.jordan.write({"allergies": "Synthetic peanut note", "medical_notes": "Synthetic medical note"})
        cls.template = cls.env["dojo.class.template"].create({"name": "Kids Taekwondo", "company_id": cls.company.id,
                                                               "program_id": cls.program.id})
        now = fields.Datetime.now()
        cls.today_session = cls.make_session(now, now + timedelta(hours=1))
        cls.later_session = cls.make_session(now + timedelta(days=2), now + timedelta(days=2, hours=1))
        for member in (cls.jordan, cls.maya, cls.devon):
            cls.env["dojo.class.enrollment"].create({"session_id": cls.today_session.id, "member_id": member.id})
        cls.kiosk = cls.env["dojo.kiosk.config"].create({"name": "Lobby iPad", "pin_code": "321654", "company_id": cls.company.id})
        cls.admin = cls.make_user("admin", ["base.group_user", "dojo_core.group_dojo_admin"])
        cls.front_desk = cls.make_user("frontdesk", ["base.group_user", "dojo_school_app.group_dojo_front_desk"])
        cls.instructor = cls.make_user("instructor", ["base.group_user", "dojo_core.group_dojo_instructor"])
        cls.nobody = cls.make_user("nobody", ["base.group_portal"])
        cls.profile = cls.env["dojo.instructor.profile"].create({"name": "Master Kim", "user_id": cls.instructor.id,
                                                                  "partner_id": cls.instructor.partner_id.id,
                                                                  "company_id": cls.company.id})
        cls.profile.employee_id.sudo().pin = "246810"
        cls.foreign = cls.env["res.company"].create({"name": "Other School"})
        cls.foreign_member = cls.env["dojo.member"].create({
            "partner_id": cls.env["res.partner"].create({"name": "Private Foreign", "company_id": cls.foreign.id}).id,
            "company_id": cls.foreign.id, "membership_state": "active"})
        foreign_template = cls.env["dojo.class.template"].create({"name": "Foreign class", "company_id": cls.foreign.id})
        cls.foreign_session = cls.env["dojo.class.session"].create({
            "template_id": foreign_template.id, "company_id": cls.foreign.id, "state": "open",
            "start_datetime": now, "end_datetime": now + timedelta(hours=1)})

    @classmethod
    def make_user(cls, login, groups):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Synthetic " + login, "login": "school-" + login + "@example.invalid", "password": "Synthetic-" + login + "-pass1",
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
            "group_ids": [(6, 0, [cls.env.ref(xmlid).id for xmlid in groups])]})

    @classmethod
    def make_member(cls, name, state, parent=None, born=None, rank=None, subscribed=False, email=False):
        partner = cls.env["res.partner"].create({"name": name, "company_id": cls.company.id, "email": email,
                                                  "parent_id": parent.id if parent else False})
        member = cls.env["dojo.member"].create({"partner_id": partner.id, "company_id": cls.company.id,
                                                "membership_state": state, "date_of_birth": born})
        if rank:
            cls.env["dojo.member.rank"].create({"member_id": member.id, "rank_id": rank.id, "company_id": cls.company.id})
        if subscribed:
            cls.env["sale.subscription"].create({
                "partner_id": partner.id, "company_id": cls.company.id, "template_id": cls.plan.template_id.id,
                "pricelist_id": cls.pricelist.id, "stage_id": cls.stage.id, "member_id": member.id, "plan_id": cls.plan.id})
        return member

    @classmethod
    def make_session(cls, start, end):
        return cls.env["dojo.class.session"].create({"template_id": cls.template.id, "company_id": cls.company.id,
                                                     "start_datetime": start, "end_datetime": end, "state": "open",
                                                     "capacity": 20})
