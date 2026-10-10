import logging
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DojoMember(models.Model):
    _name = "dojo.member"
    _description = "Dojang Member"
    _inherits = {"res.partner": "partner_id"}
    _inherit = ["mail.thread", "mail.activity.mixin"]

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    date_of_birth = fields.Date()
    gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")],
        string="Gender",
    )
    emergency_note = fields.Text()

    # ── Medical Information ───────────────────────────────────────────────
    blood_type = fields.Selection(
        [
            ("A+", "A+"), ("A-", "A-"),
            ("B+", "B+"), ("B-", "B-"),
            ("AB+", "AB+"), ("AB-", "AB-"),
            ("O+", "O+"), ("O-", "O-"),
        ],
        string="Blood Type",
    )
    allergies = fields.Text(string="Allergies")
    medical_notes = fields.Text(string="Medical Notes")

    user_ids = fields.One2many(
        "res.users", "partner_id", string="Linked Users",
        related="partner_id.user_ids",
    )
    has_portal_login = fields.Boolean(compute="_compute_has_portal_login", store=True)
    membership_state = fields.Selection(
        [
            ("lead", "Lead"),
            ("trial", "Trial"),
            ("active", "Active"),
            ("paused", "Paused"),
            ("cancelled", "Cancelled"),
        ],
        default="lead",
        required=True,
        tracking=True,
        string="Membership State",
    )

    # ── Member Number (from dojo_members) ─────────────────────────────────
    member_number = fields.Char(
        string="Member Number",
        copy=False,
        readonly=True,
        index=True,
        help="Auto-generated unique member identifier (e.g. DJ-00001). Used for barcode/kiosk check-in.",
    )

    # ── Emergency Contacts (from dojo_members) ────────────────────────────
    emergency_contact_ids = fields.One2many(
        "dojo.emergency.contact", "member_id", string="Emergency Contacts"
    )

    # ── Belt Progression (from dojo_belt_progression) ─────────────────────
    rank_history_ids = fields.One2many(
        "dojo.member.rank", "member_id", string="Belt History"
    )
    current_rank_id = fields.Many2one(
        "dojo.belt.rank",
        compute="_compute_current_rank",
        store=True,
        string="Current Belt",
    )
    attendance_log_ids = fields.One2many(
        "dojo.attendance.log",
        "member_id",
        string="Attendance Logs",
    )
    attendance_since_last_rank = fields.Integer(
        string="Attendances Since Last Rank",
        compute="_compute_attendance_since_last_rank",
        store=True,
        help="Count of present/late sessions attended since the member's last rank was awarded.",
    )
    total_sessions = fields.Integer(
        string="Total Sessions",
        compute="_compute_performance_stats",
        store=True,
        help="Total number of sessions this member has been recorded in.",
    )
    attendance_rate = fields.Float(
        string="Attendance Rate (%)",
        compute="_compute_performance_stats",
        store=True,
        help="Percentage of sessions the member attended (present or late).",
    )
    test_invite_pending = fields.Boolean(
        string="Belt Test Invite Pending",
        default=False,
        copy=False,
        help=(
            "Set automatically when the threshold is reached and a test event is created.  "
            "Reset when the test registration reaches a terminal result (pass/fail/withdrew) "
            "so the automation can re-evaluate on the next cycle."
        ),
    )
    current_stripe_count = fields.Integer(
        string="Current Stripes",
        compute="_compute_current_stripe_count",
        store=True,
        help="Number of stripes on the member's current belt rank.",
    )

    # ── Constraints ───────────────────────────────────────────────────────
    _dojo_member_number_unique = models.Constraint(
        "unique(member_number)",
        "Member Number must be unique.",
    )

    # ── Membership State Actions ──────────────────────────────────────────

    def action_set_trial(self):
        self.membership_state = "trial"

    def action_set_active(self):
        self.membership_state = "active"

    def action_set_paused(self):
        self.membership_state = "paused"

    def action_set_cancelled(self):
        self.membership_state = "cancelled"
        # Cancel any active class enrollments so the member no longer holds spots
        if 'dojo.class.enrollment' in self.env:
            enrollments = self.env['dojo.class.enrollment'].sudo().search([
                ('member_id', 'in', self.ids),
                ('status', 'in', ['registered', 'waitlist']),
            ])
            enrollments.write({'status': 'cancelled'})

    # ── ORM Overrides ─────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("partner_id"):
                partner_vals = {}
                for field_name in ("name", "email", "phone", "mobile", "company_id"):
                    if field_name in vals:
                        partner_vals[field_name] = vals.pop(field_name)
                if not partner_vals.get("name"):
                    partner_vals["name"] = "New Member"
                partner_vals["is_student"] = True
                partner = self.env["res.partner"].sudo().create(partner_vals)
                vals["partner_id"] = partner.id
            else:
                partner = self.env["res.partner"].browse(vals["partner_id"])
                if not partner.is_student and not partner.is_guardian:
                    partner.sudo().write({"is_student": True})
        records = super().create(vals_list)
        # Auto-generate member numbers
        for record in records:
            if not record.member_number:
                record.member_number = self.env["ir.sequence"].next_by_code("dojo.member") or "/"
        return records

    # ── Computed Fields ───────────────────────────────────────────────────

    @api.depends("partner_id.user_ids")
    def _compute_has_portal_login(self):
        for member in self:
            member.has_portal_login = any(
                user.share for user in member.partner_id.user_ids
            )

    @api.depends("rank_history_ids.date_awarded", "rank_history_ids.rank_id")
    def _compute_current_rank(self):
        for member in self:
            latest = member.rank_history_ids.sorted(lambda r: (r.date_awarded, r.id), reverse=True)[:1]
            member.current_rank_id = latest.rank_id if latest else False

    @api.depends("rank_history_ids.date_awarded", "rank_history_ids.stripe_count")
    def _compute_current_stripe_count(self):
        for member in self:
            latest = member.rank_history_ids.sorted(lambda r: (r.date_awarded, r.id), reverse=True)[:1]
            member.current_stripe_count = latest.stripe_count if latest else 0

    @api.depends(
        "attendance_log_ids.status",
        "attendance_log_ids.checkin_datetime",
        "rank_history_ids.date_awarded",
    )
    def _compute_attendance_since_last_rank(self):
        for member in self:
            last_rank = member.rank_history_ids.sorted("date_awarded", reverse=True)[:1]
            threshold_date = last_rank.date_awarded if last_rank else False
            logs = member.attendance_log_ids.filtered(
                lambda l: l.status in ("present", "late")
                and (
                    not threshold_date
                    or (l.checkin_datetime and l.checkin_datetime.date() >= threshold_date)
                )
            )
            member.attendance_since_last_rank = len(logs)

    @api.depends("attendance_log_ids.status")
    def _compute_performance_stats(self):
        for member in self:
            logs = member.attendance_log_ids
            total = len(logs)
            attended = len(logs.filtered(lambda l: l.status in ("present", "late")))
            member.total_sessions = total
            member.attendance_rate = (attended / total * 100) if total else 0.0

    # ── Belt Actions ──────────────────────────────────────────────────────

    def action_reset_test_invite(self):
        """Manually clear the belt-test invite pending flag (admin use)."""
        self.test_invite_pending = False

    # ── Household Helpers ─────────────────────────────────────────────────

    def _get_household(self):
        """Return the household res.partner for this member, or empty recordset."""
        self.ensure_one()
        return self.partner_id.parent_id.filtered("is_household")

    def action_create_household(self):
        """Create a household for this solo member if they don't already have one."""
        self.ensure_one()
        household = self._get_household()
        if household:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': _('%s is already in household "%s".') % (self.name, household.name),
                    'type': 'warning',
                    'sticky': False,
                },
            }
        household = self.env['res.partner'].sudo().create({
            'name': _("%s's Household") % self.name,
            'is_household': True,
            'is_company': True,
            'company_id': self.company_id.id,
            'primary_guardian_id': self.partner_id.id,
        })
        self.partner_id.parent_id = household
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Household "%s" created and linked.') % household.name,
                'type': 'success',
                'sticky': False,
            },
        }

    # ── Portal Access ─────────────────────────────────────────────────────

    def _grant_portal_access_credentials(self):
        """Grant portal access and return credentials dict for new users, or None
        if the user already existed and just needed a group added."""
        self.ensure_one()
        return self.partner_id._grant_portal_access_credentials()

    def action_grant_portal_access(self):
        """Create or update the member's user account and ensure it belongs to
        the dojo_core.group_dojo_parent_student group, which implies portal access
        and grants the correct dojo-specific ACLs and record-rules."""
        self.ensure_one()
        creds = self._grant_portal_access_credentials()
        if creds:
            message = _(
                "%(name)s now has portal access.\n"
                "Username: %(login)s\n"
                "Temp Password: %(pw)s",
                name=creds["name"],
                login=creds["login"],
                pw=creds["temp_password"],
            )
            sticky = True
        else:
            message = _("%s already has portal access.") % self.partner_id.name
            sticky = False
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "title": _("Portal Access"),
                "type": "success",
                "sticky": sticky,
            },
        }

    # ── Deletion ──────────────────────────────────────────────────────────

    def unlink(self):
        """Delete associated user accounts and res.partner contacts when a
        member is removed.

        Deletion order matters:
        1. res.users   — has a RESTRICT FK on partner_id; must go first.
        2. dojo.member — super().unlink() removes the member row.
        3. res.partner — _inherits does NOT auto-delete the parent; we do it
                         explicitly after the member row is gone.

        Household handling:
        - If the member is the only person in a household, the household
          partner is archived as well.
        - If the household has other members and this member is the primary
          guardian, deletion is blocked; staff must assign a new guardian first.
        """
        households_to_archive = self.env['res.partner']
        for member in self:
            household = member.sudo().partner_id.parent_id.filtered('is_household')
            if not household:
                continue
            other_members = self.sudo().search([
                ('partner_id.parent_id', '=', household.id),
                ('id', 'not in', self.ids),
            ])
            if other_members:
                if household.primary_guardian_id == member.partner_id:
                    raise UserError(_(
                        'Cannot delete %s: they are the primary guardian of '
                        '"%s" which still has other members. Please assign a '
                        'new primary guardian to the household before deleting '
                        'this member.',
                        member.name, household.name,
                    ))
            else:
                households_to_archive |= household

        partners = self.sudo().mapped("partner_id")

        users = partners.mapped("user_ids")
        if users:
            users.sudo().unlink()

        if 'payment.token' in self.env:
            tokens = self.env['payment.token'].sudo().search(
                [('partner_id', 'in', partners.ids)]
            )
            if tokens:
                tokens.unlink()

        res = super().unlink()

        if partners:
            partners.sudo().write({'active': False})
        if households_to_archive:
            households_to_archive.sudo().write({'active': False})
        return res
