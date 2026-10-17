"""Companion CRM: real leads and native activities, never a second CRM store."""
import re
from datetime import date

from odoo import fields, models
from markupsafe import escape as html_escape
from odoo.tools import html2plaintext
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id, session_version
from .service import HubProblem, exact, text


class CompanionLead(models.Model):
    _inherit = "crm.lead"

    def _notify_thread(self, message, **kwargs):
        # Internal task completion still writes the native chatter history, but
        # this explicit Companion action must not email followers/customers.
        if self.env.context.get("companion_internal_task"):
            return []
        return super()._notify_thread(message, **kwargs)


class CompanionMember(models.Model):
    _inherit = "dojo.member"

    def _notify_thread(self, message, **kwargs):
        if self.env.context.get("companion_internal_task"):
            return []
        return super()._notify_thread(message, **kwargs)


class HubCrm(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _lead(self, site, grant, value):
        self._role(grant, "owner manager")
        lead = self.env["crm.lead"].sudo().search([
            ("id", "=", record_id(value)), ("company_id", "=", site.company_id.id)], limit=1)
        if not lead:
            raise HubProblem("LEAD_UNAVAILABLE", 404)
        return lead

    def _lead_evidence(self, lead, tasks=None):
        """Explainable record completeness/engagement evidence, never propensity.

        Fixed v1 weights are exposed with each reason. Missing facts add nothing;
        notes, stage labels and probabilities are not proof of a trial attendance.
        """
        tasks = tasks if tasks is not None else self.env["mail.activity"].sudo().with_context(active_test=False).search([
            ("res_model", "=", "crm.lead"), ("res_id", "=", lead.id)], limit=200)
        today = fields.Date.context_today(self)
        active = tasks.filtered("active")
        completed = tasks.filtered(lambda t: not t.active and bool(t.feedback))
        trial = lead.trial_session_id if "trial_session_id" in lead._fields else self.env["dojo.class.session"]
        if trial and (trial.company_id != lead.company_id or trial.template_id.company_id != lead.company_id):
            trial = self.env["dojo.class.session"]
        member = lead.dojo_member_id if "dojo_member_id" in lead._fields else self.env["dojo.member"]
        if member and member.company_id != lead.company_id:
            member = self.env["dojo.member"]
        facts = [
            ("Contact name recorded", 10, bool(lead.contact_name)),
            ("Email or phone recorded (verification not implied)", 10, bool(lead.email_from or lead.phone)),
            ("Staff owner assigned", 10, bool(lead.user_id)),
            ("Acquisition source recorded", 10, bool(lead.source_id)),
            ("Interest tag recorded", 10, bool(lead.tag_ids)),
            ("Open next-action task recorded", 15, bool(active)),
            ("Completed task outcome recorded", 15, bool(completed)),
            ("Trial session linked (attendance not implied)", 10, bool(trial)),
            ("Member explicitly linked", 10, bool(member)),
        ]
        reasons = [{"label": label, "points": weight if present else 0, "available": weight, "recorded": present}
                   for label, weight, present in facts]
        stage = (lead.stage_id.name or "").strip().lower()
        # Only configured stage names establish these labels. No probability or
        # sentiment is used to turn a lead into a won/lost/customer record.
        stages = {"new": "New", "contacted": "Contacted", "qualified": "Contacted", "trial": "Trial",
            "trial booked": "Trial", "trial-in-progress": "Trial", "evaluation": "Evaluation",
            "offer": "Offer", "nurture": "Nurture", "lost": "Lost"}
        journey = "Won" if lead.stage_id.is_won else stages.get(stage, "Stage review")
        overdue = active.filtered(lambda t: t.date_deadline and t.date_deadline < today)
        due = active.filtered(lambda t: t.date_deadline == today)
        if journey in ("Won", "Lost"):
            priority, why = "Low", "The configured pipeline stage is terminal; review any outstanding work separately."
        elif overdue:
            priority, why = "Now", "%s open task(s) are overdue." % len(overdue)
        elif due:
            priority, why = "High", "%s open task(s) are due today." % len(due)
        elif not lead.user_id:
            priority, why = "High", "No staff owner is assigned."
        elif not active and journey != "Nurture":
            priority, why = "High", "No open next action is recorded."
        elif journey == "Nurture":
            priority, why = "Low", "The configured stage is Nurture and no task is due today."
        else:
            priority, why = "Normal", "Assigned work has no overdue or today-due task."
        action = "conversion" if journey in ("Offer", "Won") and not member else "reactivation" if journey in ("Nurture", "Lost") else "trial" if not trial and journey in ("New", "Contacted") else "follow_up"
        return {"score": sum(r["points"] for r in reasons), "scoreKind": "Recorded evidence, not conversion probability",
            "method": "v1-fixed-evidence", "reasons": reasons, "journey": journey, "priority": priority,
            "priorityReason": why, "nextAction": action, "risk": "Internal review only",
            "openTasks": len(active), "overdueTasks": len(overdue), "dueToday": len(due),
            "trialLinked": bool(trial), "memberLinked": bool(member)}

    def _crm_assignees(self, site):
        grants = self.env["dojo.hub.grant"].sudo().search([("site_id", "=", site.id), ("active", "=", True),
            ("role", "in", ["owner", "manager"]), ("user_id.active", "=", True)])
        return grants.user_id.filtered(lambda u: site.company_id in u.company_ids)

    def _lead_row(self, lead, tasks=None):
        return {"id": str(lead.id), "name": lead.name, "contact": lead.contact_name or "",
            "email": lead.email_from or "", "phone": lead.phone or "", "version": session_version(lead),
            "stageId": str(lead.stage_id.id) if lead.stage_id else None, "stage": lead.stage_id.name or "Unassigned",
            "ownerId": str(lead.user_id.id) if lead.user_id else None, "owner": lead.user_id.name or "Unassigned",
            "source": lead.source_id.name or "Not recorded", "tags": [{"id": str(t.id), "name": t.name} for t in lead.tag_ids],
            "canEditContact": not bool(lead.partner_id), "evidence": self._lead_evidence(lead, tasks)}

    def _task_record(self, site, grant, kind, value):
        self._role(grant, "owner manager")
        if kind == "lead":
            return self._lead(site, grant, value)
        if kind == "member":
            return self._member(site, grant, value)
        raise HubProblem("INVALID_COMMAND", 400)

    def _task(self, site, grant, value):
        self._role(grant, "owner manager")
        task = self.env["mail.activity"].sudo().with_context(active_test=False).search([
            ("id", "=", record_id(value)), ("res_model", "in", ["crm.lead", "dojo.member"])], limit=1)
        if not task:
            raise HubProblem("TASK_UNAVAILABLE", 404)
        kind = "lead" if task.res_model == "crm.lead" else "member"
        record = self._task_record(site, grant, kind, str(task.res_id))
        return task, record, kind

    def _task_completable(self, task):
        # Do not invoke campaign/automation activities through the manual UI.
        return (task.active and not task.automated and task.activity_type_id == self.env.ref("mail.mail_activity_data_todo")
            and not ("automation_record_step_id" in task._fields and task.automation_record_step_id))

    def _task_row(self, task, name=None):
        return {"id": str(task.id), "relatedType": "lead" if task.res_model == "crm.lead" else "member",
            "relatedId": str(task.res_id), "relatedName": name or task.res_name,
            "summary": task.summary or task.activity_type_id.name, "due": str(task.date_deadline),
            "owner": task.user_id.name or "Unassigned", "state": task.state, "version": session_version(task),
            "canComplete": self._task_completable(task), "feedback": task.feedback or ""}

    def _crm_context(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "query")
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        domain = [("company_id", "=", site.company_id.id)]
        if p["query"].strip():
            domain += ["|", ("name", "ilike", p["query"].strip()), ("contact_name", "ilike", p["query"].strip())]
        leads = self.env["crm.lead"].sudo().search(domain, order="write_date desc, id desc", limit=101)
        # Native activities are scoped by their actual linked record, not names.
        all_leads = self.env["crm.lead"].sudo().search([("company_id", "=", site.company_id.id)])
        members = self._members(site, grant)
        tasks = self.env["mail.activity"].sudo().search(["|",
            "&", ("res_model", "=", "crm.lead"), ("res_id", "in", all_leads.ids),
            "&", ("res_model", "=", "dojo.member"), ("res_id", "in", members.ids)], limit=101)
        evidence_tasks = self.env["mail.activity"].sudo().with_context(active_test=False).search([
            ("res_model", "=", "crm.lead"), ("res_id", "in", leads[:100].ids)])
        empty_tasks = self.env["mail.activity"].sudo().with_context(active_test=False)
        by_lead = {}
        for task in evidence_tasks:
            by_lead.setdefault(task.res_id, empty_tasks)
            by_lead[task.res_id] |= task
        return {"leads": [self._lead_row(l, by_lead.get(l.id, empty_tasks)) for l in leads[:100]], "tasks": [self._task_row(t) for t in tasks[:100]],
            "moreLeads": len(leads) > 100, "moreTasks": len(tasks) > 100,
            "limit": 100, "stages": [{"id": str(s.id), "name": s.name} for s in self.env["crm.stage"].sudo().search([
                "|", ("team_ids", "=", False), ("team_ids.company_id", "=", site.company_id.id)], order="sequence, id")],
            "members": [{"id": str(m.id), "name": m.name} for m in members],
            "assignees": [{"id": str(u.id), "name": u.name} for u in self._crm_assignees(site)]}

    def _lead_details(self, site, grant, p):
        exact(p, "leadId")
        lead = self._lead(site, grant, p["leadId"])
        tasks = self.env["mail.activity"].sudo().with_context(active_test=False).search([
            ("res_model", "=", "crm.lead"), ("res_id", "=", lead.id)], order="active desc, date_deadline, id", limit=50)
        audit = self.env["dojo.hub.receipt"].sudo().search([("site_id", "=", site.id), ("lead_id", "=", lead.id)], limit=30)
        member = lead.dojo_member_id if "dojo_member_id" in lead._fields else self.env["dojo.member"]
        if member and member not in self._members(site, grant):
            member = self.env["dojo.member"]
        trial = lead.trial_session_id if "trial_session_id" in lead._fields else self.env["dojo.class.session"]
        if trial and (trial.company_id != site.company_id or trial.template_id.company_id != site.company_id):
            trial = self.env["dojo.class.session"]
        notes = self.env["mail.message"].sudo().search([("model", "=", "crm.lead"), ("res_id", "=", lead.id),
            ("message_type", "=", "comment"), ("subtype_id", "=", self.env.ref("mail.mt_note").id)], order="id desc", limit=20)
        return {"lead": self._lead_row(lead), "tasks": [self._task_row(t, lead.name) for t in tasks],
            "stages": [{"id": str(s.id), "name": s.name} for s in self.env["crm.stage"].sudo().search([
                "|", ("team_ids", "=", False), ("team_ids", "in", lead.team_id.ids)], order="sequence, id")],
            "memberId": str(member.id) if member else None,
            "trial": {"title": trial.template_id.name, "startsAt": iso(trial.start_datetime)} if trial else None,
            # Never present a CRM stage/flag as factual attendance.
            "notes": [{"id": str(n.id), "text": html2plaintext(n.body or ""), "author": n.author_id.name or "Staff", "at": iso(n.date)} for n in notes],
            "timeline": [{"id": str(a.id), "action": a.operation, "actor": a.user_id.name, "at": iso(a.create_date)} for a in audit]}

    def _add_lead_note(self, site, grant, p):
        exact(p, "leadId expectedVersion note")
        lead = self._lead(site, grant, p["leadId"])
        self._lock(lead)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(lead):
            raise HubProblem("VERSION_CONFLICT")
        note = text(p["note"], 2000)
        message = lead.with_context(companion_internal_task=True, mail_post_autofollow=False).message_post(
            body=html_escape(note), message_type="comment", subtype_xmlid="mail.mt_note", author_id=self.env.user.partner_id.id)
        return {"leadId": str(lead.id), "noteId": str(message.id), "state": "saved", "messageSent": False}

    def _assign_lead(self, site, grant, p):
        exact(p, "leadId expectedVersion ownerId")
        lead = self._lead(site, grant, p["leadId"])
        self._lock(lead)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(lead):
            raise HubProblem("VERSION_CONFLICT")
        owner = self._crm_assignees(site).filtered(lambda u: u.id == record_id(p["ownerId"]))
        if not owner:
            raise HubProblem("FORBIDDEN", 403)
        lead.with_context(tracking_disable=True, mail_create_nosubscribe=True).write({"user_id": owner.id, "company_id": site.company_id.id})
        return {"leadId": str(lead.id), "ownerId": str(owner.id), "state": "assigned"}

    def _prepare_lead_action(self, site, grant, p):
        exact(p, "leadId expectedVersion kind reason due")
        lead = self._lead(site, grant, p["leadId"])
        self._lock(lead)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(lead):
            raise HubProblem("VERSION_CONFLICT")
        titles = {"follow_up": "Prepare follow-up", "trial": "Review trial options", "reactivation": "Review reactivation", "conversion": "Review member conversion"}
        if not isinstance(p["kind"], str) or p["kind"] not in titles:
            raise HubProblem("INVALID_COMMAND", 400)
        reason = text(p["reason"], 800)
        task = self._task_create_for(lead, titles[p["kind"]] + ": " + lead.name[:150], p["due"])
        # Notes belong to the real activity. They never activate campaigns,
        # book a trial, create a membership or change billing eligibility.
        task.write({"note": html_escape(reason)})
        return {"leadId": str(lead.id), "taskId": str(task.id), "kind": p["kind"], "state": "prepared",
            "messageSent": False, "bookingCreated": False, "membershipCreated": False}

    def _lead_values(self, changes):
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "contact", "email", "phone"}:
            raise HubProblem("INVALID_COMMAND", 400)
        values = {}
        for key, value in changes.items():
            if not isinstance(value, str) or len(value) > 200 or (key == "name" and not value.strip()):
                raise HubProblem("INVALID_COMMAND", 400)
            value = value.strip()
            if key == "email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise HubProblem("INVALID_COMMAND", 400)
            values[{"contact": "contact_name", "email": "email_from"}.get(key, key)] = value or False
        return values

    def _create_lead(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "name contact email phone")
        vals = self._lead_values(p)
        # Start in the configured global intake stage, never jump to a won stage.
        stage = self.env["crm.stage"].sudo().search([("team_ids", "=", False), ("is_won", "=", False)], order="sequence, id", limit=1)
        if not stage:
            raise HubProblem("CRM_SETUP_REQUIRED")
        lead = self.env["crm.lead"].sudo().with_context(tracking_disable=True, mail_create_nosubscribe=True).create({
            **vals, "company_id": site.company_id.id, "type": "lead", "user_id": False, "team_id": False, "stage_id": stage.id})
        return {"leadId": str(lead.id), "version": session_version(lead)}

    def _update_lead(self, site, grant, p):
        exact(p, "leadId expectedVersion changes")
        lead = self._lead(site, grant, p["leadId"])
        self._lock(lead)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(lead):
            raise HubProblem("VERSION_CONFLICT")
        values = self._lead_values(p["changes"])
        # Odoo inverses email/phone back onto partner; don't mutate shared identity.
        if lead.partner_id and set(values) & {"email_from", "phone", "contact_name"}:
            raise HubProblem("SHARED_CONTACT_REVIEW_REQUIRED")
        lead.with_context(tracking_disable=True).write(values)
        return {"leadId": str(lead.id), "version": session_version(lead)}

    def _task_create_for(self, record, summary, due):
        if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise HubProblem("INVALID_COMMAND", 400)
        try:
            date.fromisoformat(due)
        except ValueError:
            raise HubProblem("INVALID_COMMAND", 400)
        return self.env["mail.activity"].sudo().with_context(mail_activity_quick_update=True).create({
            "res_model_id": self.env["ir.model"]._get_id(record._name), "res_id": record.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": self.env.uid, "summary": text(summary, 200), "date_deadline": due})

    def _create_task(self, site, grant, p):
        exact(p, "relatedType relatedId summary due")
        record = self._task_record(site, grant, p["relatedType"], p["relatedId"])
        self._lock(record)
        task = self._task_create_for(record, p["summary"], p["due"])
        return {"taskId": str(task.id), "leadId" if p["relatedType"] == "lead" else "memberId": str(record.id)}

    def _complete_task(self, site, grant, p):
        exact(p, "taskId expectedVersion feedback")
        task, record, kind = self._task(site, grant, p["taskId"])
        self._lock(task)
        if not task.active:
            return {"taskId": str(task.id), "leadId" if kind == "lead" else "memberId": str(record.id), "state": "done"}
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(task):
            raise HubProblem("VERSION_CONFLICT")
        if not self._task_completable(task):
            raise HubProblem("AUTOMATED_TASK_REVIEW_REQUIRED")
        task.with_context(companion_internal_task=True).action_feedback(feedback=text(p["feedback"], 1000))
        return {"taskId": str(task.id), "leadId" if kind == "lead" else "memberId": str(record.id), "state": "done"}

    def _propose_stage(self, site, grant, p):
        exact(p, "leadId expectedVersion stageId reason due")
        lead = self._lead(site, grant, p["leadId"])
        self._lock(lead)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(lead):
            raise HubProblem("VERSION_CONFLICT")
        stage = self.env["crm.stage"].sudo().search([("id", "=", record_id(p["stageId"])),
            "|", ("team_ids", "=", False), ("team_ids", "in", lead.team_id.ids)], limit=1)
        if not stage or stage == lead.stage_id:
            raise HubProblem("INVALID_COMMAND", 400)
        reason = text(p["reason"], 120)
        task = self._task_create_for(lead, ("Review stage: %s → %s. %s" % (lead.stage_id.name, stage.name, reason))[:200], p["due"])
        return {"leadId": str(lead.id), "taskId": str(task.id), "state": "proposed", "stageChanged": False}
