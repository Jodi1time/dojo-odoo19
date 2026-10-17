"""Explicit mappings to the privately installed EB Gym addon. No vendor code."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class GymLink(models.Model):
    _name = "dojo.gym.link"
    _description = "Dojang EB Gym Membership Mapping"
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict")
    membership_id = fields.Many2one("gym.membership", required=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", related="member_id.company_id", store=True)
    template_ids = fields.Many2many("dojo.class.template", string="Explicit covered classes")
    active = fields.Boolean(default=True)
    _unique_member = models.Constraint("unique(member_id)", "Select one explicit membership mapping per member.")
    _unique_membership = models.Constraint("unique(membership_id)", "Membership already mapped.")

    @api.constrains("member_id", "membership_id", "template_ids")
    def _check_mapping(self):
        for row in self:
            if row.member_id.partner_id != row.membership_id.partner_id or row.member_id.company_id != row.membership_id.company_id:
                raise ValidationError("Map the same partner and company; numeric IDs are not an identity mapping.")
            if any(t.company_id != row.company_id for t in row.template_ids):
                raise ValidationError("Mapped classes must belong to the same company.")

    def _eligible(self, session):
        self.ensure_one()
        membership = self.membership_id
        self.env["dojo.hub.service"]._lock(membership)
        today = fields.Date.to_date(session.start_datetime)
        if not self.active or not self.member_id.active or self.member_id.partner_id != membership.partner_id or self.company_id != membership.company_id or session.company_id != self.company_id:
            raise ValidationError("Review the EB Gym identity mapping.")
        if membership.state != "active" or not membership.start_date or not membership.end_date or not membership.start_date <= today <= membership.end_date:
            raise ValidationError("An active EB Gym membership covering the class date is required.")
        if session.template_id not in self.template_ids:
            raise ValidationError("This class is not covered by the mapped EB Gym membership.")


class GymKioskConfig(models.Model):
    _inherit = "dojo.kiosk.config"
    integration_eb_gym_required = fields.Boolean(default=False, groups="base.group_system")


class GymKioskService(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _v2_eligible(self, config, session, member, enrollment):
        super()._v2_eligible(config, session, member, enrollment)
        if config.integration_eb_gym_required:
            link = self.env["dojo.gym.link"].sudo().search([("member_id", "=", member.id), ("active", "=", True)], limit=1)
            if not link:
                raise ValidationError("An explicit EB Gym mapping is required.")
            link._eligible(session)


class GymHubService(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _gym_eligibility(self, member, session):
        super()._gym_eligibility(member, session)
        link = self.env["dojo.gym.link"].sudo().search([("member_id", "=", member.id), ("active", "=", True)], limit=1)
        if link:
            link._eligible(session)


class GymAttendanceBridge(models.Model):
    _inherit = "dojo.attendance.log"
    gym_attendance_id = fields.Many2one("gym.attendance", copy=False, readonly=True, ondelete="restrict")
    _unique_gym_attendance = models.Constraint("unique(gym_attendance_id)", "Gym attendance already mapped.")

    @api.model_create_multi
    def create(self, vals_list):
        rows = super().create(vals_list)
        for row in rows.filtered(lambda r: r.status in ("present", "late")):
            link = self.env["dojo.gym.link"].sudo().search([("member_id", "=", row.member_id.id), ("active", "=", True)], limit=1)
            if link:
                link._eligible(row.session_id)
                gym = self.env["gym.attendance"].sudo().with_context(tracking_disable=True, mail_create_nosubscribe=True).create({
                    "partner_id": row.member_id.partner_id.id, "membership_id": link.membership_id.id,
                    "check_in": row.checkin_datetime, "check_out": row.checkout_datetime})
                row.gym_attendance_id = gym
        return rows

    def write(self, vals):
        if set(vals) & {"member_id", "session_id", "status", "checkin_datetime"} and any(r.gym_attendance_id for r in self):
            raise ValidationError("Mapped attendance corrections need reconciliation with EB Gym.")
        result = super().write(vals)
        if "checkout_datetime" in vals:
            for row in self.filtered("gym_attendance_id"):
                if row.gym_attendance_id.check_out and row.gym_attendance_id.check_out != row.checkout_datetime:
                    raise ValidationError("Checkout already changed in EB Gym; review before overwriting.")
                row.gym_attendance_id.sudo().write({"check_out": row.checkout_datetime})
        return result
