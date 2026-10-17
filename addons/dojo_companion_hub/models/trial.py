from datetime import date, timedelta

from odoo import fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, fingerprint, text

QUIET = dict(tracking_disable=True, mail_create_nosubscribe=True, mail_create_nolog=True, mail_notrack=True)
OPEN_TRIAL = ("reserved", "attended")
OPEN_MEMBERSHIP = ("active", "paused", "pending")


class HubTrial(models.Model):
    _name = "dojo.hub.trial"
    _description = "Reviewed Companion trial"
    _order = "id desc"

    site_id = fields.Many2one("dojo.hub.site", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)
    lead_id = fields.Many2one("crm.lead", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict", index=True)
    session_id = fields.Many2one("dojo.class.session", required=True, ondelete="restrict")
    enrollment_id = fields.Many2one("dojo.class.enrollment", ondelete="set null")
    attendance_id = fields.Many2one("dojo.attendance.log", ondelete="set null")
    subscription_id = fields.Many2one("sale.subscription", ondelete="restrict")
    state = fields.Selection([("reserved", "Reserved"), ("attended", "Attended"), ("no_show", "No-show"),
        ("cancelled", "Cancelled"), ("converted", "Converted")], required=True, default="reserved")
    reserved_by = fields.Many2one("res.users", ondelete="restrict")
    outcome_by = fields.Many2one("res.users", ondelete="restrict")
    converted_by = fields.Many2one("res.users", ondelete="restrict")
    previous_membership_state = fields.Char()
    note = fields.Text()
    _one_open_trial_per_lead = models.UniqueIndex("(lead_id) WHERE state IN ('reserved', 'attended')",
        "This lead already has an open trial.")
    _one_open_trial_per_member = models.UniqueIndex("(member_id) WHERE state IN ('reserved', 'attended')",
        "This student already has an open trial.")


class HubTrialActions(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "trial_context": {"handler": self._trial_context, "roles": "owner manager", "write": False},
            "conversion_preview": {"handler": self._conversion_preview, "roles": "owner manager", "write": False},
            "reserve_trial": {"handler": self._reserve_trial, "roles": "owner manager", "write": True, "replay": self._replay_trial},
            "cancel_trial": {"handler": self._cancel_trial, "roles": "owner manager", "write": True, "replay": self._replay_trial},
            "record_trial_outcome": {"handler": self._record_trial_outcome, "roles": "owner manager", "write": True, "replay": self._replay_trial},
            "convert_trial": {"handler": self._convert_trial, "roles": "owner manager", "write": True, "replay": self._replay_trial},
        })
        return operations

    def _replay_trial(self, site, grant, receipt, saved):
        self._lead(site, grant, saved["leadId"])
        self._trial(site, saved["trialId"])

    def _trial(self, site, value):
        trial = self.env["dojo.hub.trial"].sudo().search([("id", "=", record_id(value)), ("site_id", "=", site.id)], limit=1)
        if not trial or trial.lead_id.company_id != site.company_id or trial.member_id.company_id != site.company_id:
            raise HubProblem("TRIAL_UNAVAILABLE", 404)
        return trial

    def _trial_row(self, trial):
        return {"id": str(trial.id), "state": trial.state, "version": self._version(trial),
            "member": {"id": str(trial.member_id.id), "name": trial.member_id.name},
            "session": {"id": str(trial.session_id.id), "title": trial.session_id.template_id.name, "startsAt": iso(trial.session_id.start_datetime),
                "canRecordOutcome": trial.session_id.start_datetime <= fields.Datetime.now() + timedelta(minutes=15),
                "canCancel": trial.session_id.start_datetime > fields.Datetime.now()},
            "reservedBy": trial.reserved_by.name or None, "outcomeBy": trial.outcome_by.name or None,
            "subscriptionId": str(trial.subscription_id.id) if trial.subscription_id else None}

    def _trial_sessions(self, site):
        now = fields.Datetime.now()
        sessions = self.env["dojo.class.session"].sudo().search([("company_id", "=", site.company_id.id),
            ("template_id.company_id", "=", site.company_id.id), ("template_id.active", "=", True), ("state", "=", "open"),
            ("start_datetime", ">", now), ("start_datetime", "<=", now + timedelta(days=60))], order="start_datetime, id", limit=100)
        Enrollment = self.env["dojo.class.enrollment"].sudo()
        return [{"id": str(s.id), "title": s.template_id.name, "startsAt": iso(s.start_datetime), "version": self._version(s),
            "registered": Enrollment.search_count([("session_id", "=", s.id), ("status", "=", "registered")]),
            "capacity": s.capacity or 0, "courseRoster": bool(s.template_id.course_member_ids)} for s in sessions]

    def _trial_context(self, site, grant, p):
        exact(p, "leadId query")
        lead = self._lead(site, grant, p["leadId"])
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        students = self.env["dojo.member"]
        if p["query"].strip():
            students = self.env["dojo.member"].sudo().search([("company_id", "=", site.company_id.id), ("active", "=", True),
                ("name", "ilike", p["query"].strip())], order="name, id", limit=20)
        trials = self.env["dojo.hub.trial"].sudo().search([("site_id", "=", site.id), ("lead_id", "=", lead.id)], limit=20)
        plans = self.env["dojo.subscription.plan"].sudo().search([("company_id", "=", site.company_id.id), ("active", "=", True)], order="name, id", limit=50)
        automations = []
        if "base.automation" in self.env:
            automations = self.env["base.automation"].sudo().search([("model_id.model", "=", "crm.lead"), ("active", "=", True)], limit=50).mapped("name")
        return {"lead": {"id": str(lead.id), "name": lead.name, "version": self._version(lead), "stage": lead.stage_id.name or None,
                "contact": lead.contact_name or lead.partner_name or None},
            "trials": [self._trial_row(t) for t in trials],
            "students": [{"id": str(m.id), "name": m.name, "memberNumber": m.member_number or "", "state": m.membership_state} for m in students],
            "sessions": self._trial_sessions(site),
            "plans": [{"id": str(pl.id), "name": pl.name, "price": pl.price, "currency": pl.currency_id.name,
                "billingPeriod": pl.billing_period, "initialFee": pl.initial_fee or 0, "autoSendInvoice": bool(pl.auto_send_invoice)} for pl in plans],
            "stageAutomations": automations}

    def _reserve_trial(self, site, grant, p):
        exact(p, "leadId expectedLeadVersion memberId sessionId expectedSessionVersion reason")
        reason = text(p["reason"], 500)
        lead = self._lead(site, grant, p["leadId"])
        member = self.env["dojo.member"].sudo().search([("id", "=", record_id(p["memberId"])),
            ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not member:
            raise HubProblem("MEMBER_UNAVAILABLE", 404)
        session = self._session(site, grant, p["sessionId"])
        for row in (lead, member, session):
            self._lock(row)
        self._require_version(lead, p["expectedLeadVersion"])
        self._require_version(session, p["expectedSessionVersion"])
        if session.state != "open" or session.start_datetime <= fields.Datetime.now():
            raise HubProblem("SESSION_NOT_OPEN")
        if member.membership_state not in ("lead", "trial"):
            raise HubProblem("TRIAL_STUDENT_REVIEW_REQUIRED")
        Trial = self.env["dojo.hub.trial"].sudo()
        if Trial.search_count([("lead_id", "=", lead.id), ("state", "in", list(OPEN_TRIAL))]):
            raise HubProblem("TRIAL_ALREADY_OPEN")
        if Trial.search_count([("member_id", "=", member.id), ("state", "in", list(OPEN_TRIAL))]):
            raise HubProblem("TRIAL_ALREADY_OPEN")
        Enrollment = self.env["dojo.class.enrollment"].sudo().with_context(skip_subscription_check=True)
        enrollment = Enrollment.search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if enrollment and (enrollment.status != "cancelled" or enrollment.attendance_state != "pending"):
            raise HubProblem("ENROLLMENT_REVIEW_REQUIRED")
        if enrollment:
            self._lock(enrollment)
            enrollment.write({"status": "registered"})
        else:
            enrollment = Enrollment.create({"session_id": session.id, "member_id": member.id, "status": "registered"})
        previous = member.membership_state
        if previous == "lead":
            member.with_context(**QUIET).write({"membership_state": "trial"})
        lead.with_context(**QUIET).write({"trial_session_id": session.id})
        trial = Trial.create({"site_id": site.id, "lead_id": lead.id, "member_id": member.id, "session_id": session.id,
            "enrollment_id": enrollment.id, "reserved_by": self.env.uid, "previous_membership_state": previous, "note": reason})
        return {"trialId": str(trial.id), "leadId": str(lead.id), "memberId": str(member.id), "sessionId": str(session.id),
            "enrollmentId": str(enrollment.id), "state": "trial_reserved", "reason": reason,
            "stageChanged": False, "messageSent": False, "membershipCreated": False}

    def _cancel_trial(self, site, grant, p):
        exact(p, "trialId expectedVersion reason")
        reason = text(p["reason"], 500)
        trial = self._trial(site, p["trialId"])
        self._lead(site, grant, str(trial.lead_id.id))
        self._lock(trial)
        self._require_version(trial, p["expectedVersion"])
        if trial.state != "reserved" or trial.session_id.start_datetime <= fields.Datetime.now():
            raise HubProblem("TRIAL_REVIEW_REQUIRED")
        if trial.enrollment_id and trial.enrollment_id.status == "registered":
            self._lock(trial.enrollment_id)
            trial.enrollment_id.write({"status": "cancelled"})
        if trial.member_id.membership_state == "trial" and trial.previous_membership_state == "lead":
            trial.member_id.with_context(**QUIET).write({"membership_state": "lead"})
        if trial.lead_id.trial_session_id == trial.session_id:
            trial.lead_id.with_context(**QUIET).write({"trial_session_id": False})
        trial.write({"state": "cancelled", "outcome_by": self.env.uid, "note": "\n".join(filter(None, [trial.note, reason]))})
        return {"trialId": str(trial.id), "leadId": str(trial.lead_id.id), "memberId": str(trial.member_id.id),
            "state": "trial_cancelled", "reason": reason, "messageSent": False}

    def _record_trial_outcome(self, site, grant, p):
        exact(p, "trialId expectedVersion outcome reason")
        if p["outcome"] not in ("attended", "no_show"):
            raise HubProblem("INVALID_COMMAND", 400)
        reason = text(p["reason"], 500)
        trial = self._trial(site, p["trialId"])
        self._lead(site, grant, str(trial.lead_id.id))
        self._lock(trial)
        self._require_version(trial, p["expectedVersion"])
        session, enrollment, now = trial.session_id, trial.enrollment_id, fields.Datetime.now()
        if trial.state != "reserved" or not enrollment or enrollment.status != "registered":
            raise HubProblem("TRIAL_REVIEW_REQUIRED")
        if session.start_datetime > now + timedelta(minutes=15) or session.end_datetime < now - timedelta(days=31):
            raise HubProblem("ATTENDANCE_WINDOW_CLOSED")
        self._lock(enrollment)
        Log = self.env["dojo.attendance.log"].sudo()
        if Log.search_count([("session_id", "=", session.id), ("member_id", "=", trial.member_id.id)]):
            raise HubProblem("ATTENDANCE_REVIEW_REQUIRED")
        attended = p["outcome"] == "attended"
        log = Log.with_context(dojo_hub_staff_recorded=True).create({"session_id": session.id, "member_id": trial.member_id.id, "enrollment_id": enrollment.id,
            "status": "present" if attended else "absent", "checkin_datetime": min(now, session.start_datetime),
            "note": "%s %s: trial %s. %s" % (iso(now), self.env.user.name, p["outcome"], reason)})
        enrollment.write({"attendance_state": "present" if attended else "absent"})
        if attended:
            trial.lead_id.with_context(**QUIET).write({"trial_attended": True})
        trial.write({"state": p["outcome"], "attendance_id": log.id, "outcome_by": self.env.uid})
        return {"trialId": str(trial.id), "leadId": str(trial.lead_id.id), "memberId": str(trial.member_id.id),
            "sessionId": str(session.id), "attendanceId": str(log.id), "state": "trial_" + p["outcome"], "reason": reason,
            "stageChanged": False, "messageSent": False, "noShowFollowUpScheduled": False}

    def _conversion_plan(self, site, grant, p):
        trial = self._trial(site, p["trialId"])
        self._lead(site, grant, str(trial.lead_id.id))
        if trial.state != "attended":
            raise HubProblem("TRIAL_NOT_ATTENDED")
        plan = self.env["dojo.subscription.plan"].sudo().search([("id", "=", record_id(p["planId"])),
            ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not plan or not plan.template_id:
            raise HubProblem("PLAN_UNAVAILABLE", 404)
        try:
            start = date.fromisoformat(p["startDate"]) if isinstance(p["startDate"], str) and len(p["startDate"]) == 10 else None
        except ValueError:
            start = None
        today = fields.Date.context_today(self.with_context(tz=site.company_id.partner_id.tz or "UTC"))
        if not start or start < today or start > today + timedelta(days=60):
            raise HubProblem("INVALID_START_DATE", 422)
        member = trial.member_id
        if self.env["sale.subscription"].sudo().search([("member_id", "=", member.id), ("company_id", "=", site.company_id.id)],
                limit=100).filtered(lambda s: s.state in OPEN_MEMBERSHIP):
            raise HubProblem("MEMBERSHIP_ALREADY_OPEN")
        household = member.partner_id.parent_id
        payer = household.primary_guardian_id if household.is_household and household.primary_guardian_id else member.partner_id
        stage = self.env["sale.subscription.stage"].sudo().search([("type", "=", "in_progress" if start == today else "pre")], order="sequence, id", limit=1)
        pricelist = payer.property_product_pricelist if payer.property_product_pricelist.currency_id == plan.currency_id else \
            self.env["product.pricelist"].sudo().search([("currency_id", "=", plan.currency_id.id),
                ("company_id", "in", [False, site.company_id.id])], order="id", limit=1)
        if not stage or not pricelist:
            raise HubProblem("CONVERSION_SETUP_REQUIRED")
        covers = (plan.plan_type == "program" and trial.session_id.template_id.program_id in plan.program_ids) or \
            (plan.plan_type == "course" and (not plan.allowed_template_ids or trial.session_id.template_id in plan.allowed_template_ids))
        summary = {"trialId": str(trial.id), "leadId": str(trial.lead_id.id), "memberId": str(member.id), "member": member.name,
            "plan": {"id": str(plan.id), "name": plan.name, "price": plan.price, "currency": plan.currency_id.name,
                "billingPeriod": plan.billing_period, "initialFee": plan.initial_fee or 0, "autoSendInvoice": bool(plan.auto_send_invoice)},
            "startDate": str(start), "payer": payer.name, "coversTrialClass": bool(covers),
            "creates": ["An Odoo membership subscription started today" if start == today else "An Odoo membership subscription ready to start on %s" % start,
                "Native program enrollment and plan credits when the membership starts, if the plan grants them",
                "Student status changes from trial to active", "The lead is linked to this student as converted"],
            "doesNot": ["Collect a payment or store a payment method", "Invite a portal account", "Send a message",
                "Change the CRM stage (stage automations stay untouched)"],
            "billing": ("Odoo's native start action runs today; its billing schedule then invoices from the next invoice date it sets."
                if start == today else "Odoo's scheduled job starts the membership on %s and creates the first invoice." % start)
                + (" The plan emails invoices automatically." if plan.auto_send_invoice else " The plan does not email invoices automatically.")}
        token = fingerprint({"summary": summary, "trial": self._version(trial), "member": self._version(member),
            "lead": self._version(trial.lead_id), "siteId": site.id, "userId": self.env.uid})
        return trial, plan, start, payer, stage, pricelist, summary, token

    def _conversion_preview(self, site, grant, p):
        exact(p, "trialId planId startDate")
        trial, _, _, _, _, _, summary, token = self._conversion_plan(site, grant, p)
        return {**summary, "version": self._version(trial), "reviewToken": token}

    def _convert_trial(self, site, grant, p):
        exact(p, "trialId expectedVersion planId startDate reason reviewToken")
        reason = text(p["reason"], 500)
        trial = self._trial(site, p["trialId"])
        for row in (trial, trial.member_id, trial.lead_id):
            self._lock(row)
        self._require_version(trial, p["expectedVersion"])
        trial, plan, start, payer, stage, pricelist, summary, token = self._conversion_plan(site, grant, p)
        if not isinstance(p["reviewToken"], str) or p["reviewToken"] != token:
            raise HubProblem("CONVERSION_REVIEW_CHANGED")
        member, lead = trial.member_id, trial.lead_id
        Subscription = self.env["sale.subscription"].sudo().with_company(site.company_id).with_context(**QUIET)
        values = {"member_id": member.id, "partner_id": payer.id, "company_id": site.company_id.id, "plan_id": plan.id,
            "template_id": plan.template_id.id, "pricelist_id": pricelist.id, "date_start": start}
        if stage.type == "in_progress":
            subscription = Subscription.create(values)
            subscription.action_start_subscription()
        else:
            subscription = Subscription.create({**values, "stage_id": stage.id})
        member.with_context(**QUIET).write({"membership_state": "active"})
        lead.with_context(**QUIET).write({"dojo_member_id": member.id})
        trial.write({"state": "converted", "subscription_id": subscription.id, "converted_by": self.env.uid,
            "note": "\n".join(filter(None, [trial.note, reason]))})
        return {"trialId": str(trial.id), "leadId": str(lead.id), "memberId": str(member.id), "subscriptionId": str(subscription.id),
            "state": "trial_converted", "reason": reason, "plan": summary["plan"]["name"], "startDate": str(start),
            "membershipState": subscription.state, "nextInvoiceDate": str(subscription.recurring_next_date) if subscription.recurring_next_date else None,
            "paymentCollected": False, "portalAccessGranted": False, "messageSent": False, "stageChanged": False}
