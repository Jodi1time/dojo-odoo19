"""1Club/Dreams-inspired kiosk companion capability layer.

This extends the existing kiosk service without replacing the proven check-in,
roster, attendance, or instructor workflows. Odoo remains the source of truth.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class DojoKioskCompanionService(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _has_model(self, name):
        return name in self.env.registry.models

    def _member_or_false(self, member_id):
        return self.env["dojo.member"].browse(member_id).exists() if member_id else False

    def _public_member_summary(self, member):
        return {
            "id": member.id,
            "name": member.name or "",
            "member_number": member.member_number or "",
            "rank": member.current_rank_id.name if member.current_rank_id else "",
            "membership_state": member.membership_state or "",
            "image_url": "/web/image/dojo.member/%d/image_128" % member.id,
        }

    @api.model
    def get_companion_context(self, token, member_id=None):
        config = self.validate_token(token)
        sessions = self.get_todays_sessions()
        member = self._member_or_false(member_id)

        capabilities = {
            "check_in": True,
            "classes": True,
            "member_search": True,
            "trial_check_in": self._has_model("crm.lead"),
            "membership": self._has_model("sale.subscription"),
            "family": self._has_model("res.partner"),
            "events_testing": self._has_model("dojo.belt.test"),
            "pos": self._has_model("pos.order"),
            "access_credentials": True,
            "credential_barcode": True,
            "credential_qr_payload": True,
            "credential_nfc": self._has_model("dojo.access.credential"),
            "credential_wallet": self._has_model("dojo.access.credential"),
            "facility_map": self._has_model("dojo.facility") or self._has_model("dojo.location"),
            "ai_companion": True,
        }

        next_actions = [
            {"id": "check_in", "label": "Check in", "icon": "how_to_reg", "enabled": True},
            {"id": "classes", "label": "Classes", "icon": "calendar_month", "enabled": True},
            {"id": "find_member", "label": "Find member", "icon": "person_search", "enabled": True},
            {"id": "membership", "label": "Membership", "icon": "card_membership", "enabled": capabilities["membership"]},
            {"id": "family", "label": "Family", "icon": "family_restroom", "enabled": capabilities["family"]},
            {"id": "events", "label": "Testing & events", "icon": "emoji_events", "enabled": capabilities["events_testing"]},
            {"id": "map", "label": "Facility map", "icon": "map", "enabled": capabilities["facility_map"]},
            {"id": "help", "label": "Ask Dojang", "icon": "auto_awesome", "enabled": True},
        ]

        payload = {
            "kiosk": {"id": config.id, "name": config.name, "view_mode": config.view_mode},
            "capabilities": capabilities,
            "next_actions": next_actions,
            "sessions": sessions,
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
        if member:
            payload["member"] = self._public_member_summary(member)
            payload["member"]["sessions"] = self.get_enrolled_sessions_today(member.id)
        return payload

    @api.model
    def resolve_companion_credential(self, credential, kind="barcode"):
        value = (credential or "").strip()
        kind = (kind or "barcode").strip().lower()
        if not value:
            return {"success": False, "found": False, "error": "credential_required"}
        if kind not in ("barcode", "qr"):
            return {
                "success": False,
                "found": False,
                "error": "credential_type_not_enabled",
            }

        member = self.lookup_member_by_barcode(value)
        if not member:
            return {"success": True, "found": False}
        # lookup_member_by_barcode already returns the public kiosk payload.
        return {"success": True, "found": True, "member": member, "kind": kind}

    @api.model
    def identify_by_credential(self, credential_type, value):
        """Resolve a member from a kiosk credential without creating a second identity store.

        Barcode and QR credentials currently resolve against dojo.member.member_number.
        NFC / wallet stay capability-gated until an authoritative credential provider
        is installed in Odoo.
        """
        credential_type = (credential_type or "").strip().lower()
        value = (value or "").strip()
        if not value:
            return {"success": False, "error": "credential_required"}

        if credential_type in ("barcode", "qr", "member_number"):
            member = self.env["dojo.member"].search([
                ("member_number", "=", value),
                ("active", "=", True),
            ], limit=1)
            if not member:
                return {"success": False, "error": "member_not_found"}
            return {
                "success": True,
                "credential_type": credential_type,
                "member": self._public_member_summary(member),
            }

        if credential_type in ("nfc", "wallet"):
            return {
                "success": False,
                "error": "credential_provider_not_configured",
                "credential_type": credential_type,
            }

        return {"success": False, "error": "unsupported_credential_type"}

    @api.model
    def ask_companion(self, text, member_id=None):
        """Run the existing AI assistant in kiosk role with a deliberately narrow surface.

        Natural-language reads are allowed. Mutating kiosk intents never execute here;
        they are converted into secure UI navigation so the normal kiosk policy/action
        endpoints remain authoritative.
        """
        text = (text or "").strip()
        if not text:
            return {"success": False, "error": "text_required"}

        member = self._member_or_false(member_id)
        if member:
            text_for_ai = "%s\nSelected kiosk member: %s (member id %s)." % (
                text, member.name, member.id,
            )
        else:
            text_for_ai = text

        result = self.env["ai.assistant.service"].sudo().handle_command(
            text_for_ai,
            role="kiosk",
            input_type="text",
            context={"kiosk_member_id": member.id if member else False},
        )

        intent = result.get("intent") or {}
        intent_type = (
            intent.get("intent_type")
            or intent.get("type")
            or result.get("intent_type")
            or "unknown"
        )

        safe_read_intents = {
            "class_list",
            "schedule_today",
            "belt_lookup",
            "capability_list",
            "help_request",
            "member_enrollment_list",
            "enrollment_availability",
            "next_class_session",
            "previous_class_session",
            "unknown",
        }
        routed_write_intents = {"attendance_checkin", "attendance_checkout"}

        if intent_type in routed_write_intents:
            if not member:
                return {
                    "success": True,
                    "mode": "route",
                    "intent_type": intent_type,
                    "response": "Select a member first, then I can take you to the secure check-in flow.",
                    "action": "find_member",
                }
            return {
                "success": True,
                "mode": "route",
                "intent_type": intent_type,
                "response": (
                    "I can help with that. Choose the class below so Dojang can validate "
                    "membership, capacity, booking and attendance before writing to Odoo."
                ),
                "action": "classes",
                "member": self._public_member_summary(member),
            }

        if intent_type not in safe_read_intents:
            return {
                "success": True,
                "mode": "route",
                "intent_type": intent_type,
                "response": "That action is not available from the shared kiosk. I can help with classes, check-in, family, membership and testing.",
                "action": "home",
            }

        member_scoped = {"belt_lookup", "member_enrollment_list"}
        if intent_type in member_scoped and not member:
            return {
                "success": True,
                "mode": "route",
                "intent_type": intent_type,
                "response": "Select a member first so I only use the right account.",
                "action": "find_member",
            }

        resolved = result.get("resolved_data") or {}
        if member and intent_type in member_scoped:
            resolved_member_id = resolved.get("member_id")
            if isinstance(resolved_member_id, (list, tuple)):
                resolved_member_id = resolved_member_id[0] if resolved_member_id else False
            if resolved_member_id and int(resolved_member_id) != member.id:
                return {
                    "success": False,
                    "error": "member_context_mismatch",
                    "response": "That request points to a different member. Switch members first.",
                }

        response = (
            result.get("response")
            or result.get("confirmation_prompt")
            or (result.get("result") or {}).get("message")
            or "I found the information in Odoo."
        )
        return {
            "success": bool(result.get("success", True)),
            "mode": "answer",
            "intent_type": intent_type,
            "response": response,
            "suggestions": result.get("suggestions") or [],
        }

    @api.model
    def get_household_context(self, member_id):
        member = self._member_or_false(member_id)
        if not member:
            return {"success": False, "error": "Member not found."}

        household = member.partner_id.parent_id
        if not household or not household.is_household:
            return {
                "success": True,
                "household": None,
                "members": [self._public_member_summary(member)],
                "guardians": [],
            }

        partners = self.env["res.partner"].sudo().search([
            ("parent_id", "=", household.id),
            ("is_household", "=", False),
        ], order="name asc")

        members = []
        guardians = []
        for partner in partners:
            dojo_member = partner.dojo_member_id
            if dojo_member:
                members.append(self._public_member_summary(dojo_member))
            if partner.is_guardian:
                guardians.append({
                    "id": partner.id,
                    "name": partner.name or "",
                    "is_primary": bool(
                        household.primary_guardian_id
                        and household.primary_guardian_id.id == partner.id
                    ),
                })

        return {
            "success": True,
            "household": {"id": household.id, "name": household.name or "Household"},
            "members": members,
            "guardians": guardians,
        }

    @api.model
    def get_membership_summary(self, member_id):
        member = self._member_or_false(member_id)
        if not member:
            return {"success": False, "error": "Member not found."}

        sub = getattr(member, "active_subscription_id", False)
        plan = sub.plan_id if sub and sub.plan_id else False
        issues = self._compute_issue_flags(member)
        return {
            "success": True,
            "member": self._public_member_summary(member),
            "membership": {
                "state": member.membership_state or "",
                "active": bool(sub and sub.state == "active"),
                "subscription_state": sub.state if sub else "",
                "plan_name": plan.name if plan else "",
                "plan_type": plan.plan_type if plan else "",
                "billing_period": plan.billing_period if plan else "",
                "paused": bool(getattr(sub, "paused", False)) if sub else False,
                "grace_period_end": (
                    fields.Date.to_string(sub.grace_period_end)
                    if sub and getattr(sub, "grace_period_end", False)
                    else ""
                ),
                "issues": issues,
            },
        }

    def _session_plan_eligibility(self, member, session):
        if member.membership_state in ("cancelled", "paused", "lead"):
            return False, "Membership is not active."

        sub = getattr(member, "active_subscription_id", False)
        if not sub or sub.state != "active":
            return False, "No active subscription found."

        if session.state != "open":
            return False, "Session is not open."

        template = session.template_id
        if template and template.course_member_ids and member not in template.course_member_ids:
            return False, "Member is not enrolled in this course."

        plan = sub.plan_id
        if plan:
            if plan.plan_type == "program" and plan.program_ids:
                program = template.program_id if template else False
                if not program or program not in plan.program_ids:
                    return False, "This class is not included in the active plan."
            elif plan.plan_type == "course" and plan.allowed_template_ids:
                if not template or template not in plan.allowed_template_ids:
                    return False, "This course is not included in the active plan."

        return True, ""

    @api.model
    def get_member_session_options(self, member_id, date=None):
        member = self._member_or_false(member_id)
        if not member:
            return {"success": False, "error": "Member not found.", "sessions": []}

        sessions = self.get_todays_sessions(date=date)
        result = []
        Enrollment = self.env["dojo.class.enrollment"]
        for row in sessions:
            session = self.env["dojo.class.session"].browse(row["id"]).exists()
            if not session:
                continue
            enrollment = Enrollment.search([
                ("session_id", "=", session.id),
                ("member_id", "=", member.id),
                ("status", "in", ["registered", "waitlist"]),
            ], limit=1)
            eligible, reason = self._session_plan_eligibility(member, session)
            full = bool(session.capacity > 0 and session.seats_taken >= session.capacity)
            result.append({
                **row,
                "eligible": eligible,
                "eligibility_reason": reason,
                "full": full,
                "enrollment_status": enrollment.status if enrollment else "",
                "can_book": bool(eligible and not enrollment and not full),
                "can_waitlist": bool(eligible and not enrollment and full),
                "can_check_in": bool(
                    eligible
                    and enrollment
                    and enrollment.status == "registered"
                    and enrollment.attendance_state == "pending"
                ),
                "attendance_state": enrollment.attendance_state if enrollment else "",
            })

        return {
            "success": True,
            "member": self._public_member_summary(member),
            "sessions": result,
        }

    @api.model
    def book_member_session(self, member_id, session_id):
        member = self._member_or_false(member_id)
        session = self.env["dojo.class.session"].browse(session_id).exists()
        if not member or not session:
            return {"success": False, "error": "Member or session not found."}

        eligible, reason = self._session_plan_eligibility(member, session)
        if not eligible:
            return {"success": False, "error": reason}

        Enrollment = self.env["dojo.class.enrollment"]
        existing = Enrollment.search([
            ("session_id", "=", session.id),
            ("member_id", "=", member.id),
        ], limit=1)
        if existing:
            if existing.status in ("registered", "waitlist"):
                return {
                    "success": True,
                    "status": existing.status,
                    "message": "Already booked." if existing.status == "registered" else "Already on waitlist.",
                }
            existing.unlink()

        status = "waitlist" if session.capacity > 0 and session.seats_taken >= session.capacity else "registered"
        try:
            enrollment = Enrollment.create({
                "session_id": session.id,
                "member_id": member.id,
                "status": status,
                "attendance_state": "pending",
            })
        except ValidationError as exc:
            return {"success": False, "error": str(exc)}

        return {
            "success": True,
            "status": enrollment.status,
            "session_id": session.id,
            "session_name": session.template_id.name if session.template_id else session.name,
            "message": "Added to waitlist." if enrollment.status == "waitlist" else "Class booked.",
        }

    @api.model
    def get_testing_options(self, member_id):
        member = self._member_or_false(member_id)
        if not member:
            return {"success": False, "error": "Member not found.", "tests": []}
        if not self._has_model("dojo.belt.test"):
            return {"success": False, "error": "Testing is not enabled.", "tests": []}

        today = fields.Date.today()
        tests = self.env["dojo.belt.test"].search([
            ("state", "in", ["scheduled", "in_progress"]),
            ("test_date", ">=", today),
            ("company_id", "in", [self.env.company.id, False]),
        ], order="test_date asc", limit=20)

        registrations = self.env["dojo.belt.test.registration"].search([
            ("member_id", "=", member.id),
            ("test_id", "in", tests.ids),
        ])
        reg_by_test = {reg.test_id.id: reg for reg in registrations}
        rows = []
        for test in tests:
            reg = reg_by_test.get(test.id)
            rows.append({
                "id": test.id,
                "name": test.name or "Belt Test",
                "date": fields.Date.to_string(test.test_date) if test.test_date else "",
                "location": test.location or "",
                "program": test.program_id.name if test.program_id else "",
                "state": test.state,
                "registered": bool(reg),
                "registration_result": reg.result if reg else "",
                "target_rank": reg.target_rank_id.name if reg and reg.target_rank_id else "",
            })
        return {
            "success": True,
            "member": self._public_member_summary(member),
            "test_invite_pending": bool(getattr(member, "test_invite_pending", False)),
            "tests": rows,
        }

    @api.model
    def cancel_member_session(self, member_id, session_id):
        member = self._member_or_false(member_id)
        session = self.env["dojo.class.session"].browse(session_id).exists()
        if not member or not session:
            return {"success": False, "error": "Member or session not found."}
        enrollment = self.env["dojo.class.enrollment"].search([
            ("session_id", "=", session.id),
            ("member_id", "=", member.id),
            ("status", "in", ["registered", "waitlist"]),
        ], limit=1)
        if not enrollment:
            return {"success": False, "error": "No active booking found."}
        enrollment.write({"status": "cancelled"})
        return {"success": True, "status": "cancelled"}
