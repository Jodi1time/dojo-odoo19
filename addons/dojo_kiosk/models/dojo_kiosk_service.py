"""
Kiosk service methods -- all business logic for the kiosk SPA lives here.
Methods are designed to be called from the kiosk HTTP controller via sudo().
"""
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import html2plaintext

_MAX_PIN_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


class DojoKioskService(models.AbstractModel):
    _name = "dojo.kiosk.service"
    _description = "Dojang Kiosk Service"

    # -------------------------------------------------------------------------
    # Token + bootstrap
    # -------------------------------------------------------------------------

    @api.model
    def validate_token(self, token):
        """Return the dojo.kiosk.config for a given token, or raise AccessError."""
        if not token:
            raise AccessError("Missing kiosk token.")
        config = self.env["dojo.kiosk.config"].search(
            [("kiosk_token", "=", token), ("active", "=", True)], limit=1
        )
        if not config:
            raise AccessError("Invalid or inactive kiosk token.")
        return config

    @api.model
    def get_config_bootstrap(self, token):
        """Return device config and today's sessions for the initial app load."""
        config = self.validate_token(token)
        sessions = self.get_todays_sessions()
        announcements = [
            {"id": a.id, "title": a.title or "", "body": html2plaintext(a.body or "").strip()}
            for a in config.announcement_ids.filtered("active")
        ]
        return {
            "config_id": config.id,
            "name": config.name,
            "theme_mode": config.theme_mode or "dark",
            "view_mode": config.view_mode or "search_only",
            "show_title": config.show_title,
            "announcements": announcements,
            "sessions": sessions,
        }

    @api.model
    def get_enrolled_sessions_today(self, member_id, date=None):
        """Return today's open sessions where the member has a registered enrollment."""
        tz_name = (
            self.env.context.get("tz")
            or self.env.user.tz
            or self.env.company.partner_id.tz
            or "UTC"
        )
        tz = pytz.timezone(tz_name)
        if date:
            try:
                from datetime import datetime as _dt
                local_target = _dt.strptime(date, "%Y-%m-%d")
            except (ValueError, TypeError):
                local_target = datetime.now(tz).replace(tzinfo=None)
        else:
            local_target = datetime.now(tz).replace(tzinfo=None)

        today_start_local = local_target.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_local = local_target.replace(hour=23, minute=59, second=59, microsecond=999999)
        today_start = tz.localize(today_start_local).astimezone(pytz.utc).replace(tzinfo=None)
        today_end = tz.localize(today_end_local).astimezone(pytz.utc).replace(tzinfo=None)

        enrollments = self.env["dojo.class.enrollment"].search([
            ("member_id", "=", member_id),
            ("status", "=", "registered"),
            ("session_id.state", "=", "open"),
            ("session_id.start_datetime", ">=", fields.Datetime.to_string(today_start)),
            ("session_id.start_datetime", "<=", fields.Datetime.to_string(today_end)),
        ])

        # Deduplicate by session
        seen = set()
        result = []
        for enr in enrollments.sorted(key=lambda e: e.session_id.start_datetime):
            s = enr.session_id
            if s.id in seen:
                continue
            seen.add(s.id)
            result.append({
                "id": s.id,
                "name": s.name,
                "template_name": s.template_id.name if s.template_id else "",
                "program_name": s.template_id.program_id.name if (s.template_id and s.template_id.program_id) else "",
                "program_color": s.template_id.program_id.color if (s.template_id and s.template_id.program_id) else "",
                "start": fields.Datetime.to_string(s.start_datetime),
                "end": fields.Datetime.to_string(s.end_datetime),
                "instructor": s.instructor_profile_id.name if s.instructor_profile_id else "",
                "attendance_state": enr.attendance_state,
            })
        return result

    @api.model
    def bulk_roster_add(
        self, session_id, member_ids,
        override_capacity=False, override_settings=False, enroll_type="single",
        date_from=None, date_to=None,
        pref_mon=False, pref_tue=False, pref_wed=False, pref_thu=False,
        pref_fri=False, pref_sat=False, pref_sun=False,
    ):
        """Add multiple members to a session roster at once.

        enroll_type:
          'single'    — one-time session enrollment only.
          'multiday'  — session enrollment + multiday auto-enroll pref
                        (covers sessions within the specified date_from/date_to range).
          'permanent' — session enrollment + permanent auto-enroll pref
                        (enrolled into every future session for this template, never removed).

        override_settings:
          When True, the course-membership constraint is bypassed and (for multiday /
          permanent) the member is added to the template's course_member_ids so
          future cron enrollments also succeed.

        override_capacity:
          When True, ignore the per-session capacity limit.
        """
        from datetime import date as _date

        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return {"success": False, "error": "Session not found."}

        template = session.template_id

        # Context used when creating enrollments
        enroll_ctx = dict(self.env.context)
        if override_settings:
            enroll_ctx["skip_course_membership_check"] = True
        EnrollModel = self.env["dojo.class.enrollment"].with_context(**enroll_ctx)
        AutoEnroll = self.env["dojo.course.auto.enroll"]

        added = []
        skipped = []
        for member_id in (member_ids or []):
            member = self.env["dojo.member"].browse(member_id)
            if not member.exists():
                skipped.append(member_id)
                continue

            # ── 1. Pre-checks (course membership + weekly limit) ──────────────
            if not override_settings:
                # Course membership
                if template and template.course_member_ids and member not in template.course_member_ids:
                    course_name = template.name or "this course"
                    skipped.append({
                        "member_id": member_id,
                        "reason": (
                            f"{member.name} is not enrolled in {course_name}."
                        ),
                    })
                    continue

            # Add to course_member_ids when overriding so the ORM constraint and
            # future cron enrollments both succeed.
            if override_settings and template and template.course_member_ids:
                if member not in template.course_member_ids:
                    template.course_member_ids = [(4, member.id)]

            # ── 2. Session enrollment ───────────────────────────────────────────
            existing = EnrollModel.search([
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
            ], limit=1)
            if existing:
                if existing.status != "registered":
                    try:
                        existing.status = "registered"
                    except Exception as e:
                        skipped.append({"member_id": member_id, "reason": str(e)})
                        continue
                    added.append(member_id)
                else:
                    # Already registered — still apply auto-enroll pref below
                    added.append(member_id)
            else:
                if not override_capacity and session.capacity > 0 and session.seats_taken >= session.capacity:
                    skipped.append({"member_id": member_id, "reason": "Session is at full capacity."})
                    continue

                try:
                    EnrollModel.create({
                        "session_id": session_id,
                        "member_id": member_id,
                        "status": "registered",
                        "attendance_state": "pending",
                    })
                except Exception as e:
                    skipped.append({"member_id": member_id, "reason": str(e)})
                    continue
                added.append(member_id)

            # ── 3. Auto-enroll preference (multiday / permanent) ───────────────────
            if enroll_type in ("multiday", "permanent") and template:
                pref_mode = "multiday" if enroll_type == "multiday" else "permanent"
                day_vals = {
                    "pref_mon": pref_mon, "pref_tue": pref_tue, "pref_wed": pref_wed,
                    "pref_thu": pref_thu, "pref_fri": pref_fri, "pref_sat": pref_sat,
                    "pref_sun": pref_sun,
                }
                pref = AutoEnroll.with_context(active_test=False).search([
                    ("member_id", "=", member_id),
                    ("template_id", "=", template.id),
                ], limit=1)
                if pref:
                    # Upgrade mode if changing from limited to permanent
                    write_vals = {"active": True, **day_vals}
                    if enroll_type == "permanent" and pref.mode != "permanent":
                        write_vals["mode"] = "permanent"
                        write_vals["date_from"] = False
                        write_vals["date_to"] = False
                    elif enroll_type == "multiday" and pref.mode != "multiday":
                        write_vals["mode"] = "multiday"
                        write_vals["date_from"] = date_from or fields.Date.today()
                        write_vals["date_to"] = date_to or fields.Date.today()
                    elif enroll_type == "multiday" and pref.mode == "multiday":
                        # Update the date range even if already multiday
                        if date_from:
                            write_vals["date_from"] = date_from
                        if date_to:
                            write_vals["date_to"] = date_to
                    pref.write(write_vals)
                else:
                    create_vals = {
                        "member_id": member_id,
                        "template_id": template.id,
                        "active": True,
                        "mode": pref_mode,
                        **day_vals,
                    }
                    if pref_mode == "multiday":
                        create_vals["date_from"] = date_from or fields.Date.today()
                        create_vals["date_to"] = date_to or fields.Date.today()
                    AutoEnroll.create(create_vals)

        return {"success": True, "added": added, "skipped": skipped}

    @api.model
    def get_announcements(self, token):
        config = self.validate_token(token)
        return [
            {"id": a.id, "title": a.title or "", "body": html2plaintext(a.body or "").strip()}
            for a in config.announcement_ids.filtered("active")
        ]

    # -------------------------------------------------------------------------
    # Session helpers
    # -------------------------------------------------------------------------

    @api.model
    def get_todays_sessions(self, date=None):
        """Return open sessions for a given date (defaults to today), ordered by start time.

        The date bounds are computed in the company local timezone so that
        sessions are not missed when the server runs in UTC.
        """
        tz_name = (
            self.env.context.get("tz")
            or self.env.user.tz
            or self.env.company.partner_id.tz
            or "UTC"
        )
        tz = pytz.timezone(tz_name)
        if date:
            try:
                from datetime import datetime as _dt
                local_target = _dt.strptime(date, "%Y-%m-%d")
            except (ValueError, TypeError):
                local_target = datetime.now(tz).replace(tzinfo=None)
        else:
            local_target = datetime.now(tz).replace(tzinfo=None)

        today_start_local = local_target.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_local = local_target.replace(hour=23, minute=59, second=59, microsecond=999999)
        # Convert local midnight → end-of-day to UTC so the ORM query is correct
        today_start = tz.localize(today_start_local).astimezone(pytz.utc).replace(tzinfo=None)
        today_end = tz.localize(today_end_local).astimezone(pytz.utc).replace(tzinfo=None)

        sessions = self.env["dojo.class.session"].search([
            ("state", "in", ("open", "done")),
            ("start_datetime", ">=", fields.Datetime.to_string(today_start)),
            ("start_datetime", "<=", fields.Datetime.to_string(today_end)),
            ("company_id", "in", [self.env.company.id, False]),
        ], order="start_datetime asc")

        result = []
        for s in sessions:
            result.append({
                "id": s.id,
                "name": s.name,
                "state": s.state,
                "template_name": s.template_id.name if s.template_id else "",
                "program_name": s.template_id.program_id.name if (s.template_id and s.template_id.program_id) else "",
                "is_trial": s.template_id.program_id.is_trial if (s.template_id and s.template_id.program_id) else False,
                "start": fields.Datetime.to_string(s.start_datetime),
                "end": fields.Datetime.to_string(s.end_datetime),
                "seats_taken": s.seats_taken,
                "capacity": s.capacity,
                "instructor": s.instructor_profile_id.name if s.instructor_profile_id else "",
            })
        return result

    # -------------------------------------------------------------------------
    # Roster helpers
    # -------------------------------------------------------------------------

    @api.model
    def get_session_roster(self, session_id):
        """Return the enrolled roster for a session with attendance state."""
        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return []

        enrollments = session.enrollment_ids.filtered(
            lambda e: e.status == "registered"
        )

        # Build log map so we can correctly surface "late" (enrollment only tracks
        # present/absent/excused, but the attendance log stores the full status)
        logs = self.env["dojo.attendance.log"].search([
            ("session_id", "=", session_id),
            ("member_id", "in", enrollments.mapped("member_id").ids),
        ])
        log_by_member = {l.member_id.id: l.status for l in logs}

        result = []
        for enr in enrollments:
            member = enr.member_id
            # Prefer the log status (supports "late"); fall back to enrollment state
            att_state = log_by_member.get(member.id) or enr.attendance_state
            result.append(self._member_roster_entry(member, enr, att_state))

        # Append trial leads booked for this session
        trial_leads = self.env["crm.lead"].search([
            ("trial_session_id", "=", session_id),
            ("dojo_member_id", "=", False),
        ])
        for lead in trial_leads:
            program_name = ""
            if session.template_id and session.template_id.program_id:
                program_name = session.template_id.program_id.name
            partner_id = lead.partner_id.id if lead.partner_id else False
            result.append({
                "member_id": None,
                "lead_id": lead.id,
                "name": lead.contact_name or lead.partner_name or "Unknown",
                "is_trial": True,
                "trial_program": program_name,
                "partner_id": partner_id,
                "attendance_state": "present" if lead.trial_attended else "pending",
                "membership_state": "trial",
                "belt_rank": "",
                "belt_color": "",
                "issues": [],
            })
        return result

    def _member_roster_entry(self, member, enrollment=None, attendance_state=None):
        """Compact dict for a roster tile."""
        if attendance_state is None:
            attendance_state = enrollment.attendance_state if enrollment else "pending"
        return {
            "member_id": member.id,
            "name": member.name,
            "member_number": member.member_number or "",
            "image_url": "/web/image/dojo.member/%d/image_128" % member.id,
            "belt_rank": member.current_rank_id.name if member.current_rank_id else "",
            "belt_color": member.current_rank_id.color if member.current_rank_id else "",
            "attendance_state": attendance_state,
            "membership_state": member.membership_state if hasattr(member, "membership_state") else "",
            "issues": self._compute_issue_flags(member),
        }

    def _public_member_entry(self, member):
        """Minimal member payload safe for public kiosk search and barcode lookup."""
        return {
            "member_id": member.id,
            "name": member.name,
            "belt_rank": member.current_rank_id.name if member.current_rank_id else "",
            "is_trial": False,
        }

    def _public_trial_lead_dict(self, lead):
        """Minimal trial payload safe for public kiosk search results."""
        session = lead.trial_session_id
        program_name = ""
        if session and session.template_id and session.template_id.program_id:
            program_name = session.template_id.program_id.name
        session_dict = {}
        if session:
            session_dict = {
                "id": session.id,
                "name": session.name,
                "template_name": session.template_id.name if session.template_id else session.name,
                "program_name": program_name,
                "start": str(session.start_datetime) if session.start_datetime else "",
                "end": str(session.end_datetime) if session.end_datetime else "",
                "instructor": "",
            }
        return {
            "member_id": None,
            "lead_id": lead.id,
            "name": lead.contact_name or lead.partner_name or "Unknown",
            "is_trial": True,
            "trial_program": program_name,
            "trial_session": session_dict,
            "partner_id": lead.partner_id.id if lead.partner_id else False,
            "belt_rank": "",
        }

    # -------------------------------------------------------------------------
    # Member lookup
    # -------------------------------------------------------------------------

    @api.model
    def lookup_member_by_barcode(self, barcode):
        """Find a member by member_number (barcode scan)."""
        member = self.env["dojo.member"].search(
            [("member_number", "=", barcode), ("active", "=", True)], limit=1
        )
        if not member:
            return None
        return self._public_member_entry(member)

    @api.model
    def search_members(self, query, limit=20):
        """Search members by name, email, or phone for the kiosk search bar."""
        if not query or len(query.strip()) < 2:
            return []
        domain = [
            ("active", "=", True),
            "|", "|",
            ("name", "ilike", query.strip()),
            ("email", "ilike", query.strip()),
            ("phone", "ilike", query.strip()),
        ]
        members = self.env["dojo.member"].search(domain, limit=limit, order="name asc")
        return [self._public_member_entry(m) for m in members]

    def search_trial_leads(self, query, limit=10):
        """Search CRM leads with a booked trial session by name or email."""
        if not query or len(query.strip()) < 2:
            return []
        domain = [
            ("trial_session_id", "!=", False),
            ("trial_attended", "=", False),
            ("dojo_member_id", "=", False),
            "|",
            ("contact_name", "ilike", query.strip()),
            ("email_from", "ilike", query.strip()),
        ]
        leads = self.env["crm.lead"].search(domain, limit=limit, order="contact_name asc")
        return [self._public_trial_lead_dict(lead) for lead in leads]

    def _trial_lead_dict(self, lead):
        session = lead.trial_session_id
        program_name = ""
        if session and session.template_id and session.template_id.program_id:
            program_name = session.template_id.program_id.name
        session_dict = {}
        if session:
            session_dict = {
                "id": session.id,
                "name": session.name,
                "template_name": session.template_id.name if session.template_id else session.name,
                "program_name": program_name,
                "start": str(session.start_datetime) if session.start_datetime else "",
                "end": str(session.end_datetime) if session.end_datetime else "",
                "instructor": "",
            }
        partner_id = lead.partner_id.id if lead.partner_id else False
        return {
            "member_id": None,
            "lead_id": lead.id,
            "name": lead.contact_name or lead.partner_name or "Unknown",
            "email": lead.email_from or "",
            "is_trial": True,
            "trial_program": program_name,
            "trial_session": session_dict,
            "partner_id": partner_id,
            "membership_state": "trial",
            "belt_rank": "",
            "issues": [],
        }

    def checkin_trial_lead(self, lead_id, session_id=None):
        """Mark a trial lead as attended and record attendance on their session."""
        lead = self.env["crm.lead"].browse(lead_id)
        if not lead.exists():
            return {"success": False, "error": "Trial lead not found."}
        session = lead.trial_session_id
        if not session:
            return {"success": False, "error": "No trial session booked for this lead."}
        if session_id and session.id != session_id:
            return {"success": False, "error": "Session mismatch."}
        lead.write({"trial_attended": True})
        # Attempt to move to Trial-in-progress stage
        stage = self.env["crm.stage"].search([("name", "ilike", "Trial-in-progress")], limit=1)
        if not stage:
            stage = self.env["crm.stage"].search([("name", "ilike", "trial")], limit=1)
        if stage:
            lead.write({"stage_id": stage.id})
        program_name = ""
        if session.template_id and session.template_id.program_id:
            program_name = session.template_id.program_id.name
        return {
            "success": True,
            "session_name": session.template_id.name if session.template_id else session.name,
            "program_name": program_name,
            "status": "present",
        }

    # -------------------------------------------------------------------------
    # Member profile + issue flags
    # -------------------------------------------------------------------------

    @api.model
    def get_member_profile(self, member_id, session_id=None):
        """Full member profile for the profile card modal."""
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return None
        return self._member_profile_dict(member, session_id=session_id)

    def _member_profile_dict(self, member, session_id=None):
        issues = self._compute_issue_flags(member)

        # Current enrollment in the requested session
        attendance_state = "pending"
        enrolled = False
        if session_id:
            enr = self.env["dojo.class.enrollment"].search([
                ("session_id", "=", session_id),
                ("member_id", "=", member.id),
                ("status", "=", "registered"),
            ], limit=1)
            enrolled = bool(enr)
            # Use attendance log status so "late" is preserved
            log = self.env["dojo.attendance.log"].search([
                ("session_id", "=", session_id),
                ("member_id", "=", member.id),
            ], limit=1)
            attendance_state = log.status if log else (enr.attendance_state if enr else "pending")

        # Total attendance count
        total_attendance = self.env["dojo.attendance.log"].search_count([
            ("member_id", "=", member.id),
            ("status", "in", ["present", "late"]),
        ])

        # Upcoming enrolled sessions (appointments)
        now = fields.Datetime.now()
        upcoming_enrs = self.env["dojo.class.enrollment"].search([
            ("member_id", "=", member.id),
            ("status", "=", "registered"),
        ])
        # Filter future sessions and sort in Python (related field ops not supported in ORM order/search)
        future_enrs = [
            e for e in upcoming_enrs
            if e.session_id and e.session_id.start_datetime and e.session_id.start_datetime >= now
        ]
        future_enrs.sort(key=lambda e: e.session_id.start_datetime)
        appointments = []
        for enr in future_enrs[:7]:
            s = enr.session_id
            appointments.append({
                "session_id": s.id,
                "name": s.template_id.name if s.template_id else "",
                "start": fields.Datetime.to_string(s.start_datetime) if s.start_datetime else "",
                "end": fields.Datetime.to_string(s.end_datetime) if s.end_datetime else "",
            })

        # Active plan name + credit balance
        plan_name = ""
        credit_balance = 0
        credits_per_period = 0
        sub = member.active_subscription_id
        if sub and sub.plan_id:
            plan_name = sub.plan_id.name or ""
            credits_per_period = getattr(sub.plan_id, 'credits_per_period', 0) or 0
            credit_balance = getattr(sub, 'credit_balance', 0) or 0

        # Household + emergency contacts
        hh = member.partner_id.parent_id if member.partner_id.parent_id.is_household else None
        household = None
        if hh:
            contacts = []
            for ec in member.emergency_contact_ids:
                contacts.append({
                    "name": ec.name or "",
                    "relationship": ec.relationship or "",
                    "phone": ec.phone or "",
                    "email": ec.email or "",
                    "is_primary": bool(ec.is_primary),
                })
            # Get all contacts in the household (students AND guardian-only partners)
            hh_partners = self.env["res.partner"].sudo().search([
                ("parent_id", "=", hh.id),
                ("is_household", "=", False),
            ])
            household = {
                "id": hh.id,
                "name": hh.name or "",
                "members": [
                    {
                        "id": p.id,
                        "name": p.name or "",
                        "is_student": p.is_student,
                        "is_guardian": p.is_guardian,
                    }
                    for p in hh_partners
                ],
                "emergency_contacts": contacts,
            }

        # Belt progression: classes since last rank + per-program stats
        att_since_rank = getattr(member, "attendance_since_last_rank", 0) or 0
        programs = []
        if hasattr(member, "rank_history_ids"):
            prog_ranks = {}
            for rank_rec in member.rank_history_ids:
                prog = rank_rec.program_id
                prog_key = prog.id if prog else 0
                if prog_key not in prog_ranks or rank_rec.date_awarded > prog_ranks[prog_key]["date"]:
                    prog_ranks[prog_key] = {
                        "program_name": prog.name if prog else "General",
                        "rank_name": rank_rec.rank_id.name if rank_rec.rank_id else "",
                        "rank_color": rank_rec.rank_id.color if rank_rec.rank_id else "",
                        "date": rank_rec.date_awarded,
                    }
            # Count attendance logs per program
            all_logs = self.env["dojo.attendance.log"].search([
                ("member_id", "=", member.id),
                ("status", "in", ["present", "late"]),
            ])
            prog_attendance = {}
            for log in all_logs:
                tmpl = log.session_id.template_id if log.session_id else None
                if tmpl and tmpl.program_id:
                    pid = tmpl.program_id.id
                    prog_attendance[pid] = prog_attendance.get(pid, 0) + 1
            for prog_key, info in prog_ranks.items():
                programs.append({
                    "program_id": prog_key if prog_key != 0 else None,
                    "program_name": info["program_name"],
                    "rank_name": info["rank_name"],
                    "rank_color": info["rank_color"],
                    "attendance_count": prog_attendance.get(prog_key, 0),
                })
            programs.sort(key=lambda p: p["program_name"])

        # Guardians: people flagged as guardians in the same household
        guardians = []
        if hh:
            guardian_partners = self.env["res.partner"].sudo().search([
                ("parent_id", "=", hh.id),
                ("is_guardian", "=", True),
            ])
            for gp in guardian_partners:
                guardians.append({
                    "partner_id": gp.id,
                    "name": gp.name or "",
                    "relation": "guardian",
                    "is_primary": gp.id == hh.primary_guardian_id.id if hh.primary_guardian_id else False,
                    "phone": gp.phone or "",
                    "email": gp.email or "",
                })
        if not guardians:
            guardians.append({
                "member_id": member.id,
                "name": member.name or "",
                "relation": "self",
                "is_primary": True,
                "phone": member.phone or "",
                "email": member.email or "",
            })

        return {
            "member_id": member.id,
            "name": member.name,
            "email": member.email or "",
            "phone": member.phone or "",
            "is_student": member.partner_id.is_student,
            "is_guardian": member.partner_id.is_guardian,
            "member_number": member.member_number or "",
            "image_url": "/web/image/dojo.member/%d/image_128" % member.id,
            "date_of_birth": fields.Date.to_string(member.date_of_birth) if member.date_of_birth else "",
            "membership_state": member.membership_state,
            "belt_rank": member.current_rank_id.name if member.current_rank_id else "",
            "belt_color": member.current_rank_id.color if member.current_rank_id else "",
            "total_attendance": total_attendance,
            "credit_balance": credit_balance,
            "credits_per_period": credits_per_period,
            "issues": issues,
            "enrolled_in_session": enrolled,
            "attendance_state": attendance_state,
            "appointments": appointments,
            "plan_name": plan_name,
            "household": household,
            "attendance_since_last_rank": att_since_rank,
            "programs": programs,
            "guardians": guardians,
        }

    def _compute_issue_flags(self, member):
        flags = []
        if member.membership_state == "cancelled":
            flags.append({"code": "membership_cancelled", "label": "Membership Cancelled"})
        elif member.membership_state == "paused":
            flags.append({"code": "membership_on_hold", "label": "Membership On Hold"})
        elif member.membership_state == "lead":
            flags.append({"code": "membership_lead", "label": "Not Yet Active"})

        sub = member.active_subscription_id
        if not sub:
            flags.append({"code": "no_subscription", "label": "No Active Subscription"})
        elif sub.state in ("expired", "cancelled"):
            flags.append({"code": "membership_expired", "label": "Membership Expired"})

        # Flag credits exhausted when the plan has a credit limit and balance is zero.
        cpp = getattr(sub.plan_id, "credits_per_period", 0) if sub else 0
        credit_balance = getattr(sub, "credit_balance", 0) if sub else 0
        if cpp > 0 and credit_balance <= 0:
            flags.append({"code": "credits_exhausted", "label": "Ran Out of Credits"})

        return flags

    # -------------------------------------------------------------------------
    # Check-in
    # -------------------------------------------------------------------------

    @api.model
    def checkin_member(self, member_id, session_id):
        """
        Atomic check-in:
        1. Validate eligibility.
        2. Find or create enrollment (registered).
        3. Create attendance log (present / late).
        4. Sync enrollment.attendance_state.
        Returns dict with success flag, message, and updated profile.
        """
        member = self.env["dojo.member"].browse(member_id)
        session = self.env["dojo.class.session"].browse(session_id)

        if not member.exists() or not session.exists():
            return {"success": False, "error": "Member or session not found."}

        # --- Eligibility ---
        if member.membership_state in ("cancelled", "paused", "lead"):
            return {
                "success": False,
                "error": "Membership is not active. Please see the front desk.",
            }

        if not member.active_subscription_id:
            return {
                "success": False,
                "error": "No active subscription found. Please see the front desk.",
            }

        if session.state != "open":
            return {"success": False, "error": "This session is not currently open."}

        # --- Course roster check ---
        template = session.template_id
        if template.course_member_ids and member not in template.course_member_ids:
            return {
                "success": False,
                "error": "You are not enrolled in this course. Please see the front desk.",
            }

        # --- Capacity check ---
        if session.capacity > 0 and session.seats_taken >= session.capacity:
            return {"success": False, "error": "This session is full."}

        # --- Find or create enrollment ---
        existing_log = self.env["dojo.attendance.log"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
        ], limit=1)
        if existing_log:
            return {
                "success": False,
                "error": "Already checked in to this session.",
            }

        enrollment = self.env["dojo.class.enrollment"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
            ("status", "=", "registered"),
        ], limit=1)

        if not enrollment:
            enrollment = self.env["dojo.class.enrollment"].create({
                "session_id": session_id,
                "member_id": member_id,
                "status": "registered",
                "attendance_state": "pending",
            })

        # --- Determine present / late ---
        now = fields.Datetime.now()
        status = "late" if now > session.start_datetime else "present"

        log = self.env["dojo.attendance.log"].create({
            "session_id": session_id,
            "member_id": member_id,
            "enrollment_id": enrollment.id,
            "status": status,
            "checkin_datetime": now,
        })

        # Sync enrollment
        enrollment.attendance_state = "present"

        # Invalidate cached computed fields so the returned profile reflects
        # the newly created enrollment / attendance log.
        member.invalidate_recordset()

        # ── Points earned on this check-in ────────────────────────────────
        points_info = {}
        try:
            new_txns = self.env["dojo.points.transaction"].sudo().search([
                ("attendance_log_id", "=", log.id),
            ])
            points_info = {
                "points_earned": sum(new_txns.mapped("amount")),
                "new_total_points": member.total_points,
                "current_streak": member.current_streak,
                "tier": member.points_tier,
            }
        except Exception:
            pass  # points module not installed or fields missing — graceful fallback

        return {
            "success": True,
            "status": status,
            "log_id": log.id,
            "member": self._member_profile_dict(member, session_id=session_id),
            "session_name": session.name,
            "points": points_info,
        }

    # -------------------------------------------------------------------------
    # Instructor — member photo update
    # -------------------------------------------------------------------------

    @api.model
    def update_member_photo(self, member_id, image_data):
        """Write a new profile photo (base64) to the member's partner record."""
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}
        member.image_1920 = image_data
        return {
            "success": True,
            "image_url": "/web/image/dojo.member/%d/image_128?v=%s" % (member.id, int(datetime.now().timestamp())),
        }

    # -------------------------------------------------------------------------
    # Instructor — attendance
    # -------------------------------------------------------------------------

    @api.model
    def mark_attendance(self, session_id, member_id, attendance_status):
        """
        Instructor-side: mark a member present / late / absent / excused.
        Creates or updates the attendance log and enrollment state.
        """
        valid = ("present", "late", "absent", "excused")
        session = self.env["dojo.class.session"].browse(session_id)
        member = self.env["dojo.member"].browse(member_id)
        if not session.exists() or not member.exists():
            return {"success": False, "error": "Session or member not found."}
        log = self.env["dojo.attendance.log"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
        ], limit=1)
        # "pending" = instructor is un-marking (removing) attendance:
        # delete the log and reset the enrollment back to pending.
        if attendance_status == "pending":
            if log:
                log.unlink()
            enrollment = self.env["dojo.class.enrollment"].search([
                ("session_id", "=", session_id),
                ("member_id", "=", member_id),
                ("status", "=", "registered"),
            ], limit=1)
            if enrollment:
                enrollment.attendance_state = "pending"
            member.invalidate_recordset()
            return {"success": True, "removed": True}
        if attendance_status not in valid:
            return {"success": False, "error": "Invalid status."}

        if log:
            log.status = attendance_status
        else:
            log = self.env["dojo.attendance.log"].create({
                "session_id": session_id,
                "member_id": member_id,
                "status": attendance_status,
                "checkin_datetime": fields.Datetime.now(),
            })

        # Sync enrollment
        enrollment = self.env["dojo.class.enrollment"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
            ("status", "=", "registered"),
        ], limit=1)
        if enrollment:
            enrollment.attendance_state = (
                "present" if attendance_status in ("present", "late") else attendance_status
            )

        # Invalidate cached computed fields so any subsequent profile read is fresh
        member.invalidate_recordset()

        return {"success": True, "log_id": log.id}

    # -------------------------------------------------------------------------
    # Instructor — roster management
    # -------------------------------------------------------------------------

    @api.model
    def roster_add(self, session_id, member_id, override_settings=False, override_capacity=False):
        """Add a member to the session roster (creates enrollment).

        override_settings: bypass course-membership check and add member to course roster.
        override_capacity: bypass the per-session capacity limit.
        """
        from odoo.exceptions import ValidationError as _VE

        session = self.env["dojo.class.session"].browse(session_id)
        member = self.env["dojo.member"].browse(member_id)
        if not session.exists() or not member.exists():
            return {"success": False, "error": "Session or member not found."}

        template = session.template_id

        # --- Descriptive pre-checks (skipped when instructor overrides) ---
        if not override_settings:
            if template and template.course_member_ids and member not in template.course_member_ids:
                course_name = template.name or "this course"
                return {
                    "success": False,
                    "error": (
                        f"{member.name} is not enrolled in {course_name}. "
                        "Use the override option to add them anyway."
                    ),
                }

        if not override_capacity:
            if session.capacity > 0 and session.seats_taken >= session.capacity:
                return {
                    "success": False,
                    "error": (
                        f"Session is at full capacity ({session.capacity} seats). "
                        "Use the override option to add them anyway."
                    ),
                }

        # When overriding, add to the course roster so the ORM constraint is satisfied
        if override_settings and template and template.course_member_ids:
            if member not in template.course_member_ids:
                template.course_member_ids = [(4, member.id)]

        # Build enrollment model with bypass context when overriding
        enroll_ctx = dict(self.env.context)
        if override_settings:
            enroll_ctx["skip_course_membership_check"] = True
        EnrollModel = self.env["dojo.class.enrollment"].with_context(**enroll_ctx)

        existing = EnrollModel.search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
        ], limit=1)
        if existing:
            if existing.status != "registered":
                try:
                    existing.status = "registered"
                except _VE as e:
                    return {"success": False, "error": str(e)}
            return {"success": True, "enrollment_id": existing.id}

        try:
            enr = EnrollModel.create({
                "session_id": session_id,
                "member_id": member_id,
                "status": "registered",
                "attendance_state": "pending",
            })
        except _VE as e:
            return {"success": False, "error": str(e)}

        return {"success": True, "enrollment_id": enr.id}

    @api.model
    def roster_remove(self, session_id, member_id):
        """Remove a member from the session roster."""
        enrollment = self.env["dojo.class.enrollment"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
        ], limit=1)
        if enrollment:
            enrollment.status = "cancelled"
        return {"success": True}

    # -------------------------------------------------------------------------
    # Instructor — session close
    # -------------------------------------------------------------------------

    @api.model
    def close_session(self, session_id):
        """Mark a session as done.
        
        Requires all enrolled members to have attendance recorded (no pending).
        Empty sessions (zero enrollments) are always allowed to close.
        """
        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return {"success": False, "error": "Session not found."}

        # Guard: block close if any enrolled member still has attendance_state = pending
        pending_enrollments = session.enrollment_ids.filtered(
            lambda e: e.status == "registered" and e.attendance_state == "pending"
        )
        if pending_enrollments:
            count = len(pending_enrollments)
            return {
                "success": False,
                "error": "pending_attendance",
                "count": count,
                "message": (
                    f"{count} member(s) still have attendance pending. "
                    "Please record attendance for all members before marking done."
                ),
            }

        session.state = "done"
        return {"success": True}

    @api.model
    def reopen_session(self, session_id):
        """Re-open a session that was previously marked done."""
        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return {"success": False, "error": "Session not found."}
        if session.state != "done":
            return {"success": False, "error": "Only done sessions can be reopened."}
        session.state = "open"
        return {"success": True}

    @api.model
    def delete_session(self, session_id):
        """Cancel a session and all its registrations."""
        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return {"success": False, "error": "Session not found."}
        session.enrollment_ids.filtered(
            lambda e: e.status == "registered"
        ).write({"status": "cancelled"})
        session.state = "cancelled"
        return {"success": True}

    @api.model
    def update_session(self, session_id, capacity=None):
        """Update editable fields on an open session."""
        session = self.env["dojo.class.session"].browse(session_id)
        if not session.exists():
            return {"success": False, "error": "Session not found."}
        if capacity is not None:
            try:
                session.capacity = max(0, int(capacity))
            except (TypeError, ValueError):
                return {"success": False, "error": "Invalid capacity value."}
        return {"success": True}

    @api.model
    def get_templates(self):
        """Return active class templates for the current company, for use in the
        kiosk 'Create Session' flow."""
        templates = self.env["dojo.class.template"].search([
            ("active", "=", True),
            ("company_id", "in", [self.env.company.id, False]),
        ], order="name asc")
        result = []
        for t in templates:
            # recurrence_time is a float (e.g. 18.5 = 18:30); fall back to 09:00
            rt = t.recurrence_time or 9.0
            h = int(rt)
            m = int(round((rt - h) * 60))
            # Guard against rounding to 60 minutes
            if m >= 60:
                h += 1
                m = 0
            result.append({
                "id": t.id,
                "name": t.name,
                "program_name": t.program_id.name if t.program_id else "",
                "duration_minutes": t.duration_minutes or 60,
                "capacity": t.max_capacity or 20,
                "default_start": f"{h:02d}:{m:02d}",
            })
        return result

    @api.model
    def create_session_today(self, template_id, start_time, capacity=None, date=None):
        """Create an open session from *template_id* for the given date (defaults to today).

        Args:
            template_id (int): ID of the dojo.class.template to use.
            start_time (str): Local start time in "HH:MM" format.
            capacity (int|None): Override capacity; falls back to template max_capacity.
            date (str|None): ISO date "YYYY-MM-DD"; defaults to today in company tz.

        Returns:
            dict: {"success": True, "session": <serialized>} or {"success": False, "error": str}
        """
        template = self.env["dojo.class.template"].browse(template_id)
        if not template.exists():
            return {"success": False, "error": "Template not found."}

        tz_name = (
            self.env.context.get("tz")
            or self.env.user.tz
            or self.env.company.partner_id.tz
            or "UTC"
        )
        tz = pytz.timezone(tz_name)

        if date:
            try:
                local_date = datetime.strptime(date, "%Y-%m-%d")
            except (ValueError, TypeError):
                local_date = datetime.now(tz).replace(tzinfo=None)
        else:
            local_date = datetime.now(tz).replace(tzinfo=None)

        try:
            parts = start_time.split(":")
            h, m = int(parts[0]), int(parts[1])
        except Exception:
            return {"success": False, "error": "Invalid start_time format. Use HH:MM."}

        local_start = local_date.replace(hour=h, minute=m, second=0, microsecond=0)
        duration = template.duration_minutes or 60
        local_end = local_start + timedelta(minutes=duration)

        utc_start = tz.localize(local_start).astimezone(pytz.utc).replace(tzinfo=None)
        utc_end = tz.localize(local_end).astimezone(pytz.utc).replace(tzinfo=None)

        cap = int(capacity) if capacity is not None else (template.max_capacity or 20)

        session = self.env["dojo.class.session"].create({
            "template_id": template_id,
            "start_datetime": fields.Datetime.to_string(utc_start),
            "end_datetime": fields.Datetime.to_string(utc_end),
            "capacity": cap,
            "state": "open",
        })

        return {
            "success": True,
            "session": {
                "id": session.id,
                "name": session.name,
                "template_name": session.template_id.name if session.template_id else "",
                "start": fields.Datetime.to_string(session.start_datetime),
                "end": fields.Datetime.to_string(session.end_datetime),
                "seats_taken": 0,
                "capacity": session.capacity,
                "instructor": session.instructor_profile_id.name if session.instructor_profile_id else "",
            },
        }

    # -------------------------------------------------------------------------
    # PIN verification
    # -------------------------------------------------------------------------

    @api.model
    def verify_pin(self, pin, token=None, config_id=None):
        """
        Verify the 6-digit instructor PIN with rate limiting.
        Locks out after _MAX_PIN_ATTEMPTS failures for _LOCKOUT_MINUTES minutes.
        token takes priority over legacy config_id.
        """
        if token:
            try:
                config_record = self.validate_token(token)
            except AccessError:
                return {"success": False, "error": "invalid_token"}
        elif config_id:
            config_record = self.env["dojo.kiosk.config"].browse(int(config_id))
            if not config_record.exists() or not config_record.active:
                return {"success": False, "error": "invalid_token"}
        else:
            return {"success": False, "error": "invalid_token"}

        lock_state = config_record._get_pin_lock_state()
        if lock_state["locked"]:
            return {
                "success": False,
                "error": "locked",
                "retry_in_minutes": lock_state["retry_in_minutes"],
            }

        if config_record._verify_pin_value(pin):
            config_record._clear_pin_attempts()
            return {"success": True}

        failure = config_record._register_pin_failure(
            _MAX_PIN_ATTEMPTS,
            _LOCKOUT_MINUTES,
        )
        if failure["locked"]:
            return {
                "success": False,
                "error": "locked",
                "retry_in_minutes": failure["retry_in_minutes"],
            }
        return {
            "success": False,
            "error": "wrong_pin",
            "remaining_tries": failure["remaining_tries"],
        }

    # -------------------------------------------------------------------------
    # Check-out
    # -------------------------------------------------------------------------

    @api.model
    def checkout_member(self, member_id, session_id):
        """Record departure time on the attendance log."""
        log = self.env["dojo.attendance.log"].search([
            ("session_id", "=", session_id),
            ("member_id", "=", member_id),
        ], limit=1)
        if not log:
            return {"success": False, "error": "No attendance record found."}
        if log.status not in ("present", "late"):
            return {"success": False, "error": "Member is not marked present or late."}
        log.checkout_datetime = fields.Datetime.now()
        return {
            "success": True,
            "checkout_datetime": fields.Datetime.to_string(log.checkout_datetime),
        }

    # -------------------------------------------------------------------------
    # Instructor — belt rank management
    # -------------------------------------------------------------------------

    @api.model
    def get_available_belt_ranks(self, member_id, program_id=None):
        """Return all active belt ranks, with the member's current rank marked.

        If program_id is provided, ranks are filtered/ordered by the program's
        belt_rank_ids if that field exists; otherwise all company ranks are returned.
        """
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}

        # Determine current rank per program
        current_rank_id = None
        if hasattr(member, "current_rank_id") and member.current_rank_id:
            current_rank_id = member.current_rank_id.id

        # Build the candidate ranks list
        if program_id:
            program = self.env["dojo.program"].browse(program_id)
            if hasattr(program, "belt_rank_ids") and program.belt_rank_ids:
                ranks = program.belt_rank_ids.filtered("active").sorted("sequence")
            else:
                ranks = self.env["dojo.belt.rank"].search([
                    ("active", "=", True),
                    ("company_id", "in", [self.env.company.id, False]),
                ], order="sequence, name")
        else:
            ranks = self.env["dojo.belt.rank"].search([
                ("active", "=", True),
                ("company_id", "in", [self.env.company.id, False]),
            ], order="sequence, name")

        # Also get per-program current ranks from rank history
        prog_current = {}
        if hasattr(member, "rank_history_ids"):
            for rh in member.rank_history_ids:
                pid = rh.program_id.id if rh.program_id else 0
                if pid not in prog_current or rh.date_awarded > prog_current[pid]["date"]:
                    prog_current[pid] = {
                        "rank_id": rh.rank_id.id if rh.rank_id else None,
                        "date": rh.date_awarded,
                    }

        current_for_prog = prog_current.get(program_id or 0, {}).get("rank_id") or current_rank_id

        return {
            "success": True,
            "current_rank_id": current_for_prog,
            "ranks": [
                {
                    "id": r.id,
                    "name": r.name,
                    "color": r.color or "#ffffff",
                    "sequence": r.sequence,
                    "is_current": r.id == current_for_prog,
                }
                for r in ranks
            ],
        }

    @api.model
    def award_belt_rank(self, member_id, rank_id, program_id=None, notes=""):
        """Award a belt rank to a member and record in rank history."""
        member = self.env["dojo.member"].browse(member_id)
        rank = self.env["dojo.belt.rank"].browse(rank_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}
        if not rank.exists():
            return {"success": False, "error": "Rank not found."}

        # Check if this rank already exists for the member in the same program
        domain = [
            ("member_id", "=", member.id),
            ("rank_id", "=", rank.id),
        ]
        if program_id:
            domain.append(("program_id", "=", program_id))
        else:
            domain.append(("program_id", "=", False))
        if self.env["dojo.member.rank"].search_count(domain):
            return {"success": False, "error": f"{member.name} already holds {rank.name} in this program."}

        vals = {
            "member_id": member.id,
            "rank_id": rank.id,
            "date_awarded": fields.Date.today(),
        }
        if program_id:
            vals["program_id"] = program_id
        if notes:
            vals["notes"] = notes

        self.env["dojo.member.rank"].create(vals)

        # Update the member's current_rank_id (the model stores the most recent)
        if hasattr(member, "current_rank_id"):
            member.invalidate_recordset()

        return {
            "success": True,
            "rank_name": rank.name,
            "rank_color": rank.color or "#ffffff",
        }

    # -------------------------------------------------------------------------
    # Instructor — message parent / guardian
    # -------------------------------------------------------------------------

    @api.model
    def send_parent_message(self, member_id, subject, message, send_sms=True, send_email=True, guardian_member_ids=None):
        """Send SMS/email to guardian(s) for a member.

        If guardian_member_ids is provided, treats them as res.partner IDs
        and sends to each. Falls back to the primary guardian or the
        member's own contact info.
        """
        import logging
        _logger = logging.getLogger(__name__)

        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}

        if not subject or not message:
            return {"success": False, "error": "Subject and message are required."}

        # Build target list: (partner, name)
        targets = []
        if guardian_member_ids:
            for gid in guardian_member_ids:
                gp = self.env["res.partner"].browse(gid)
                if gp.exists():
                    targets.append((gp, gp.name or ""))
        if not targets:
            household = member.partner_id.parent_id
            if household and household.is_household and household.primary_guardian_id:
                guardian = household.primary_guardian_id
                targets.append((guardian, guardian.name or ""))
            else:
                targets.append((member.partner_id, member.name or ""))

        sent_email_total = False
        sent_sms_total = False
        errors = []
        recipient_names = []

        for partner, name in targets:
            if not partner:
                continue
            recipient_names.append(name)
            try:
                if send_email and partner.email:
                    mail = self.env["mail.mail"].create({
                        "subject": subject,
                        "body_html": f"<p>{message}</p>",
                        "email_to": partner.email,
                        "auto_delete": True,
                    })
                    mail.send()
                    sent_email_total = True
            except Exception as exc:
                _logger.error("Kiosk send_parent_message email failed: %s", exc)
                errors.append("Email failed: %s" % str(exc)[:80])

            try:
                if send_sms:
                    phone = getattr(partner, 'mobile', None) or partner.phone
                    if phone:
                        self.env["sms.sms"].create({
                            "number": phone,
                            "body": message,
                            "partner_id": partner.id,
                        }).send()
                        sent_sms_total = True
                    elif not errors:
                        errors.append("No mobile number on file for %s." % name)
            except Exception as exc:
                _logger.error("Kiosk send_parent_message SMS failed: %s", exc)
                errors.append("SMS failed: %s" % str(exc)[:80])

        if not sent_email_total and not sent_sms_total and errors:
            return {"success": False, "error": "; ".join(errors)}

        summary = []
        if sent_email_total:
            summary.append("email")
        if sent_sms_total:
            summary.append("SMS")
        return {
            "success": True,
            "sent_via": summary,
            "recipient_name": ", ".join(recipient_names),
            "recipients": recipient_names,
        }

    # -------------------------------------------------------------------------
    # Instructor — belt rank: next rank helper
    # -------------------------------------------------------------------------

    @api.model
    def get_next_belt_rank(self, member_id, program_id=None):
        """Return the immediate next rank above the member's current rank in sequence."""
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}

        if program_id:
            program = self.env["dojo.program"].browse(program_id)
            if hasattr(program, "belt_rank_ids") and program.belt_rank_ids:
                ranks = list(program.belt_rank_ids.filtered("active").sorted("sequence"))
            else:
                ranks = list(self.env["dojo.belt.rank"].search([
                    ("active", "=", True),
                    ("company_id", "in", [self.env.company.id, False]),
                ], order="sequence, name"))
        else:
            ranks = list(self.env["dojo.belt.rank"].search([
                ("active", "=", True),
                ("company_id", "in", [self.env.company.id, False]),
            ], order="sequence, name"))

        if not ranks:
            return {"success": True, "is_highest_rank": False, "current_rank": None, "next_rank": None}

        # Determine current rank
        current_rank_id = None
        if program_id and hasattr(member, "rank_history_ids"):
            best_date = None
            for rh in member.rank_history_ids:
                pid = rh.program_id.id if rh.program_id else None
                if pid == program_id and rh.rank_id:
                    if best_date is None or rh.date_awarded > best_date:
                        current_rank_id = rh.rank_id.id
                        best_date = rh.date_awarded
        if current_rank_id is None and hasattr(member, "current_rank_id") and member.current_rank_id:
            current_rank_id = member.current_rank_id.id

        def _rank_dict(r):
            return {"id": r.id, "name": r.name, "color": r.color or "#ffffff"}

        rank_ids = [r.id for r in ranks]
        if current_rank_id is None:
            return {
                "success": True,
                "is_highest_rank": False,
                "current_rank": None,
                "next_rank": _rank_dict(ranks[0]),
            }
        if current_rank_id not in rank_ids:
            return {
                "success": True,
                "is_highest_rank": False,
                "current_rank": None,
                "next_rank": _rank_dict(ranks[0]),
            }
        idx = rank_ids.index(current_rank_id)
        current_rank = ranks[idx]
        if idx >= len(ranks) - 1:
            return {
                "success": True,
                "is_highest_rank": True,
                "current_rank": _rank_dict(current_rank),
                "next_rank": None,
            }
        return {
            "success": True,
            "is_highest_rank": False,
            "current_rank": _rank_dict(current_rank),
            "next_rank": _rank_dict(ranks[idx + 1]),
        }

    # -------------------------------------------------------------------------
    # Instructor — available sessions to assign
    # -------------------------------------------------------------------------

    @api.model
    def get_available_sessions(self, member_id):
        """Return upcoming sessions for the member's enrolled templates (excluding already enrolled)."""
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}

        enrolled_template_ids = []
        if hasattr(member, "enrolled_template_ids"):
            enrolled_template_ids = member.enrolled_template_ids.ids
        if not enrolled_template_ids:
            return {"success": True, "sessions": []}

        now = fields.Datetime.now()
        sixty_days = now + timedelta(days=60)
        sessions = self.env["dojo.class.session"].search([
            ("template_id", "in", enrolled_template_ids),
            ("state", "in", ["draft", "open"]),
            ("start_datetime", ">=", fields.Datetime.to_string(now)),
            ("start_datetime", "<=", fields.Datetime.to_string(sixty_days)),
            ("company_id", "in", [self.env.company.id, False]),
        ], order="start_datetime asc")

        already_enrolled = set(self.env["dojo.class.enrollment"].search([
            ("member_id", "=", member_id),
            ("status", "=", "registered"),
            ("session_id", "in", sessions.ids),
        ]).mapped("session_id").ids)

        result = []
        for s in sessions:
            if s.id in already_enrolled:
                continue
            seats_available = (s.capacity - s.seats_taken) if s.capacity > 0 else 99
            result.append({
                "session_id": s.id,
                "name": s.template_id.name if s.template_id else "",
                "program_name": s.template_id.program_id.name if (s.template_id and s.template_id.program_id) else "",
                "start": fields.Datetime.to_string(s.start_datetime),
                "end": fields.Datetime.to_string(s.end_datetime),
                "seats_available": seats_available,
            })
        return {"success": True, "sessions": result}

    # -------------------------------------------------------------------------
    # Instructor — voice command (STT + AI + action execution)
    # -------------------------------------------------------------------------

    @api.model
    def process_voice_command(self, token, member_id, session_id, audio_data_b64, dry_run=False):
        """Process a voice command: STT → AI → (optionally execute) → return result.

        When dry_run=True the action is interpreted but NOT executed; the caller
        receives the parsed action + params so it can show a confirmation step
        before calling execute_voice_action().
        """
        import base64
        import json as _json
        import re as _re
        import logging
        _log = logging.getLogger(__name__)

        self.validate_token(token)
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member not found."}

        # Step 1: Speech-to-text
        try:
            audio_bytes = base64.b64decode(audio_data_b64)
            transcribed = self.env["elevenlabs.service"].sudo().transcribe_audio(audio_bytes)
        except Exception as e:
            _log.error("Voice STT error: %s", e)
            return {"success": False, "error": "Could not transcribe audio. Check ElevenLabs API key."}

        if not transcribed or not transcribed.strip():
            return {"success": False, "error": "Could not understand the audio."}

        # Step 2: Build context for AI prompt
        next_rank_data = self.get_next_belt_rank(member_id)
        avail_sessions_data = self.get_available_sessions(member_id)
        profile = self._member_profile_dict(member, session_id=session_id)

        enrolled_txt = ""
        for appt in (profile.get("appointments") or []):
            enrolled_txt += f"  - session_id={appt['session_id']}: {appt['name']} at {appt['start']}\n"

        avail_txt = ""
        for s in (avail_sessions_data.get("sessions") or [])[:10]:
            avail_txt += (
                f"  - session_id={s['session_id']}: {s['name']} "
                f"({s['program_name']}) at {s['start']} [{s['seats_available']} seats]\n"
            )

        guardians_txt = ""
        for g in (profile.get("guardians") or []):
            line = f"  - member_id={g['member_id']}: {g['name']} ({g['relation']})"
            if g.get("phone"):
                line += f" phone:{g['phone']}"
            if g.get("email"):
                line += f" email:{g['email']}"
            guardians_txt += line + "\n"

        current_rank_name = (next_rank_data.get("current_rank") or {}).get("name", "None")
        next_rank_name = (next_rank_data.get("next_rank") or {}).get("name", "None")
        next_rank_id = (next_rank_data.get("next_rank") or {}).get("id")

        system_prompt = (
            f"You are a voice assistant for a martial arts dojo instructor at a tablet kiosk.\n"
            f"The instructor is managing student: {member.name}\n"
            f"Current belt rank: {current_rank_name}\n"
            f"Next rank in sequence: {next_rank_name} (id: {next_rank_id})\n"
            f"Is already at highest rank: {str(next_rank_data.get('is_highest_rank', False))}\n"
            f"Currently enrolled upcoming sessions:\n{enrolled_txt or '  (none)\n'}"
            f"Available sessions to add (same program/templates):\n{avail_txt or '  (none)\n'}"
            f"Guardians/contacts:\n{guardians_txt or f'  - member_id={member.id}: {member.name} (self)\n'}"
            "IMPORTANT: Respond ONLY with valid JSON (no markdown, no extra text):\n"
            '{"action": "promote_rank" or "send_message" or "add_session" or "remove_session" or "info",\n'
            ' "params": {\n'
            '   <for send_message>: "guardian_member_ids": [int,...], "message": "...", "send_sms": true, "send_email": false\n'
            '   <for add_session or remove_session>: "session_id": int\n'
            '   <for promote_rank or info>: {}\n'
            ' },\n'
            ' "response_text": "Brief 1-2 sentence confirmation for the instructor"\n'
            "}\n"
            "Rules:\n"
            f"- promote_rank: awards next_rank_id={next_rank_id} to student (1 step up)\n"
            "- send_message: default to send_sms=true, send_email=false; compose a brief professional message\n"
            "- If instructor says 'contact all' or 'message everyone', include ALL guardian member_ids\n"
            "- If instructor names a specific guardian, map to their member_id from the list above\n"
            "- For info queries, use action=info and put the answer in response_text\n"
            "- If unclear, use action=info"
        )

        # Step 3: AI processing
        try:
            ai_proc = self.env["ai.processor"].sudo()
            provider = ai_proc._get_provider()
            if provider == "gemini":
                ai_response = ai_proc._process_gemini(transcribed, system_prompt, {})
            else:
                ai_response = ai_proc._process_openai(transcribed, system_prompt, {})
        except Exception as e:
            _log.error("Voice AI error: %s", e)
            return {
                "success": True,
                "transcribed_text": transcribed,
                "response_text": "AI is not configured. Please check ElevenLabs connector settings.",
                "action_taken": None,
                "action_data": {},
            }

        # Step 4: Parse JSON action from AI response
        action = "info"
        params = {}
        response_text = ai_response or ""
        try:
            match = _re.search(r'\{.*\}', ai_response or "", _re.DOTALL)
            if match:
                parsed = _json.loads(match.group())
                action = parsed.get("action", "info")
                params = parsed.get("params") or {}
                response_text = parsed.get("response_text") or ai_response
        except Exception:
            pass

        # Step 5: Execute action (skipped when dry_run=True)
        if dry_run or action == "info":
            return {
                "success": True,
                "transcribed_text": transcribed,
                "response_text": response_text,
                "action": action,
                "params": params,
                "action_taken": None,
                "action_data": {},
            }

        return self.execute_voice_action(member_id, session_id, action, params,
                                         transcribed_text=transcribed,
                                         response_text=response_text)

    @api.model
    def execute_voice_action(self, member_id, session_id, action, params,
                             transcribed_text="", response_text=""):
        """Execute a previously-interpreted voice action.

        Separated from process_voice_command so the kiosk can show a
        confirmation prompt before any mutation takes place.
        """
        import logging
        _log = logging.getLogger(__name__)

        action_taken = None
        action_data = {}

        if action == "promote_rank":
            next_rank_data = self.get_next_belt_rank(member_id)
            next_rank_id = (next_rank_data.get("next_rank") or {}).get("id")
            if next_rank_id and not next_rank_data.get("is_highest_rank"):
                try:
                    award_result = self.award_belt_rank(member_id, next_rank_id)
                    if award_result.get("success"):
                        action_taken = "promote_rank"
                        action_data = award_result
                except Exception as e:
                    _log.error("Voice promote_rank error: %s", e)

        elif action == "send_message":
            try:
                gids = params.get("guardian_member_ids") or []
                msg = params.get("message") or transcribed_text
                msg_result = self.send_parent_message(
                    member_id,
                    subject="Message from your Instructor",
                    message=msg,
                    send_sms=bool(params.get("send_sms", True)),
                    send_email=bool(params.get("send_email", False)),
                    guardian_member_ids=gids,
                )
                if msg_result.get("success"):
                    action_taken = "send_message"
                    action_data = msg_result
            except Exception as e:
                _log.error("Voice send_message error: %s", e)

        elif action == "add_session":
            try:
                sid = params.get("session_id")
                if sid:
                    add_result = self.roster_add(sid, member_id, override_settings=True)
                    if add_result.get("success"):
                        action_taken = "add_session"
                        action_data = add_result
            except Exception as e:
                _log.error("Voice add_session error: %s", e)

        elif action == "remove_session":
            try:
                sid = params.get("session_id")
                if sid:
                    rm_result = self.roster_remove(sid, member_id)
                    if rm_result.get("success"):
                        action_taken = "remove_session"
                        action_data = rm_result
            except Exception as e:
                _log.error("Voice remove_session error: %s", e)

        return {
            "success": True,
            "transcribed_text": transcribed_text,
            "response_text": response_text,
            "action_taken": action_taken,
            "action_data": action_data,
        }
