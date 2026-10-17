from odoo import fields, models


class DojoSchoolPromotionBatch(models.Model):
    _name = "dojo.school.promotion.batch"
    _description = "School App Promotion Batch"
    _order = "promoted_at desc, id desc"

    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="restrict")
    from_belt = fields.Char(required=True)
    to_rank_id = fields.Many2one("dojo.belt.rank", required=True, ondelete="restrict")
    rank_ids = fields.Many2many("dojo.member.rank", "dojo_school_promotion_batch_rank_rel", "batch_id", "rank_id")
    student_names = fields.Text()
    promoted_at = fields.Datetime(required=True, default=fields.Datetime.now)
    undone_at = fields.Datetime()
    undone_by = fields.Many2one("res.users", ondelete="restrict")


class DojoSchoolTestRoster(models.Model):
    _name = "dojo.school.test.roster"
    _description = "School App Belt Test Roster"
    _order = "create_date desc, id desc"

    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="restrict")
    roster_date = fields.Date(required=True)
    belt_filter = fields.Char()
    class_filter = fields.Char()
    member_ids = fields.Many2many("dojo.member", "dojo_school_test_roster_member_rel", "roster_id", "member_id")
    student_names = fields.Text()


class DojoSchoolEmailLog(models.Model):
    _name = "dojo.school.email.log"
    _description = "School App Email History"
    _order = "sent_at desc, id desc"

    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    user_id = fields.Many2one("res.users", ondelete="restrict")
    kind = fields.Selection([("mass", "Mass"), ("individual", "Individual"), ("followup", "Follow-up"),
                             ("automation", "Automation"), ("test", "Test")], required=True)
    subject = fields.Char(required=True)
    body = fields.Text()
    member_ids = fields.Many2many("dojo.member", "dojo_school_email_log_member_rel", "log_id", "member_id")
    mail_ids = fields.Many2many("mail.mail", "dojo_school_email_log_mail_rel", "log_id", "mail_id")
    recipient_name = fields.Char()
    recipient_count = fields.Integer()
    queued = fields.Integer()
    failed = fields.Integer()
    sent_at = fields.Datetime(required=True, default=fields.Datetime.now)


class DojoSchoolAutomationLog(models.Model):
    _name = "dojo.school.automation.log"
    _description = "School App Automation Send"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="cascade", index=True)
    kind = fields.Char(required=True)
    key = fields.Char(required=True)
    _unique_send = models.Constraint("unique(member_id, kind, key)", "This automation was already sent.")
