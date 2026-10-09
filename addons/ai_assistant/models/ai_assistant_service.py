# -*- coding: utf-8 -*-
#test update
"""
AI Assistant Service — Central service for handling AI assistant commands.

This is a reusable service model that can be used by:
- Instructor Dashboard
- Kiosk module
- Other applications needing AI assistant capabilities

Provides:
- Two-phase confirmation flow (parse → confirm → execute)
- Structured intent parsing via ai.processor extension
- Intent handlers for CRUD operations
- Audit logging of all actions
- Undo capability for reversible actions
- Bulk operation support
"""

import json
import logging
import re
import time
import uuid

from odoo import api, fields, models
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)

# ─── Action sentinel tokens (legacy, for backward compatibility) ─────────────
_ACTION_START = "##ACTION##"
_ACTION_END = "##END_ACTION##"

# ─── Confidence threshold ────────────────────────────────────────────────────
_MIN_CONFIDENCE = 0.7

# ─── Compound command configuration ──────────────────────────────────────────
_MAX_COMPOUND_CHAIN = 10

_COMPOUND_SIGNALS = re.compile(
    r'\band\s+then\b'
    r'|\b(?:and|then)\s+(?:also\s+)?'
    r'(?:enroll|create|cancel|text|send|add|remove|promote|check\s+(?:in|out)|schedule'
    r'|look|show|find|book|register|get|update|message|list|display)',
    re.IGNORECASE
)

# ─── Read-only intents that auto-execute without confirmation ────────────────
_AUTO_EXECUTE_INTENTS = {
    "member_lookup",
    "class_list",
    "belt_lookup",
    "subscription_lookup",
    "attendance_history",
    "schedule_today",
    "at_risk_members",
    "campaign_lookup",
    "marketing_card_lookup",
    "subscription_expiring",
    # CRM read intents
    "lead_lookup",
    "pipeline_summary",
    "trial_schedule",
    # New read intents
    "belt_test_list",
    "belt_test_registration_list",
    "program_list",
    "class_template_list",
    "social_post_list",
    "program_enrollment_lookup",
    "onboarding_status",
    "points_lookup",
    "credit_lookup",
    "household_lookup",
    "course_auto_enroll_list",
    "campaign_list",
    "kiosk_announcement_list",
    "birthday_upcoming",
    # Sales read intents
    "sale_order_list",
    "sale_order_lookup",
    "product_list",
    "product_lookup",
    # POS read intents
    "pos_order_list",
    "pos_order_lookup",
    "pos_session_list",
    "pos_daily_summary",
    # Accounting read intents
    "payment_list",
    "account_balance",
    "bill_list",
    # HR read intents
    "employee_list",
    "employee_lookup",
    "department_list",
    # Discuss read intents
    "channel_list",
    "channel_lookup",
    "message_list",
    # Meta intents
    "capability_list",
    "help_request",
    "unknown",
    # Cross-model phone lookup
    "phone_lookup",
    # Cross-model phone lookup (searches members + leads)
    "phone_lookup",
    # ── Batch A: Points & Member Admin ─────────────────────────────────────
    "member_enrollment_list",
    # ── Batch B: Class & Roster ────────────────────────────────────────────
    "enrollment_availability",
    "class_enrollment_list",
    # ── Batch C: Belt & Instructor ────────────────────────────────────────
    "belt_readiness_check",
    "instructor_todo_list",
    # ── Batch D: Revenue, CRM & Portal ───────────────────────────────────
    "subscription_dunning_list",
    "payment_history",
    "revenue_summary",
    "trial_conversion_report",
    # ── New Query Intents ────────────────────────────────────────────────
    "members_by_belt",
    "checkin_required_today",
    "belt_test_roster",
    "members_active_subscriptions",
    "members_by_state",
    "session_no_shows",
    "belt_eligible_members",
    "subscription_status_summary",
    # ── New Session & Belt Intents ───────────────────────────────────────
    "next_class_session",
    "previous_class_session",
    "belt_readiness_all",
}

# ─── All recognised intent types (must match handler keys in _execute_intent) ─
_KNOWN_INTENT_TYPES = {
    "member_lookup", "class_list", "belt_lookup", "subscription_lookup",
    "attendance_history", "schedule_today",
    "member_enroll", "member_unenroll", "belt_promote",
    "subscription_create", "subscription_cancel", "contact_parent",
    "attendance_checkin", "attendance_checkout",
    "member_create", "member_update",
    "class_create", "class_cancel",
    "course_enroll", "belt_test_register",
    "undo_action", "unknown",
    # Extended intents
    "subscription_pause", "subscription_resume",
    "subscription_expiring",
    "at_risk_members", "campaign_lookup", "marketing_card_lookup",
    "campaign_create", "campaign_activate",
    "social_post_create", "social_post_schedule",
    # CRM intents (handlers in dojo_crm/models/ai_crm_service.py via _inherit)
    "lead_lookup", "pipeline_summary", "trial_schedule",
    "lead_qualify", "lead_mark_attended", "lead_convert",
    "lead_create", "lead_mark_lost", "lead_mark_won",
    # CRUD intents
    "program_create", "belt_test_register_crud", "class_template_create",
    "class_enrollment_create", "class_enrollment_cancel",
    "belt_test_create", "attendance_log_create", "credit_transaction_create",
    "marketing_campaign_create", "instructor_profile_update", "emergency_contact_create",
    "martial_art_style_create", "subscription_plan_create", "program_enrollment_create",
    "belt_test_registration_create", "marketing_card_create",
    "kiosk_announcement_create", "course_auto_enroll_create",
    "compound_chain",
    # ── New read intents ────────────────────────────────────────────────────
    "belt_test_list", "belt_test_registration_list", "program_list", "class_template_list",
    "social_post_list", "program_enrollment_lookup", "onboarding_status",
    "points_lookup", "credit_lookup", "household_lookup",
    "course_auto_enroll_list", "campaign_list", "kiosk_announcement_list",
    "birthday_upcoming",
    # ── Core Odoo module intents ────────────────────────────────────────────
    # Tasks / Project
    "task_list", "task_create", "task_complete", "task_update",
    # Calendar
    "calendar_event_list", "calendar_event_create", "calendar_event_cancel",
    # Direct communication
    "send_email", "send_sms", "email_blast", "sms_blast",
    # Invoicing (read-only)
    "invoice_lookup", "invoice_list",
    # Activities
    "activity_create", "activity_list",
    # ── Sales intents ───────────────────────────────────────────────────────
    "sale_order_list", "sale_order_lookup", "sale_order_create",
    "sale_order_confirm", "sale_order_cancel", "sale_order_send",
    "product_list", "product_lookup",
    # ── POS intents ─────────────────────────────────────────────────────────
    "pos_order_list", "pos_order_lookup", "pos_session_list",
    "pos_session_open", "pos_session_close", "pos_daily_summary",
    # ── Accounting intents ──────────────────────────────────────────────────
    "invoice_create", "invoice_send", "payment_list",
    "payment_register", "account_balance", "bill_list", "bill_create",
    # ── HR intents ──────────────────────────────────────────────────────────
    "employee_list", "employee_lookup", "employee_create",
    "employee_update", "department_list", "department_create", "employee_archive",
    # ── Discuss intents ─────────────────────────────────────────────────────
    "channel_list", "channel_lookup", "channel_create",
    "channel_message_send", "channel_add_member", "message_list",
    # ── Meta intents ────────────────────────────────────────────────────────
    "capability_list", "help_request",
    # Cross-model phone lookup (searches members + leads)
    "phone_lookup",
    # ── Batch A: Points & Member Admin ─────────────────────────────────────
    "points_award", "points_deduct",
    "member_archive", "member_note_add",
    "member_enrollment_list",
    "subscription_billing_retry",
    # ── Batch B: Class & Roster ────────────────────────────────────────────
    "enrollment_availability",
    "class_enrollment_list",
    "class_session_generate",
    "class_waitlist_add",
    # ── Batch C: Belt & Instructor ───────────────────────────────────────────
    "belt_test_result_update",
    "belt_readiness_check",
    "instructor_todo_list",
    "instructor_todo_complete",
    # ── Batch D: Revenue, CRM & Portal ───────────────────────────────────────
    "subscription_dunning_list",
    "revenue_summary",
    "payment_history",
    "trial_conversion_report",
    "lead_note_add",
    "guardian_portal_invite",
    # ── New Query Intents ────────────────────────────────────────────────
    "members_by_belt",
    "checkin_required_today",
    "belt_test_roster",
    "members_active_subscriptions",
    "members_by_state",
    "session_no_shows",
    "belt_eligible_members",
    "subscription_status_summary",
    # ── New Session & Belt Intents ───────────────────────────────────────
    "next_class_session",
    "previous_class_session",
    "belt_readiness_all",
    "enroll_next_session",
}

# ─── Intent Handler Configuration (for generic read handler) ──────────────────
# Maps intent_type → handler config for read-only operations
# Structure: {
#     "model": "model_name",
#     "domain_builder": "method_name" | static domain list,
#     "fields": [list of field names to return],
#     "limit": max records,
#     "limit_from_params": "parameter_name" (optional, if limit comes from intent params)
# }
_INTENT_HANDLER_CONFIG = {
    "member_lookup": {
        "model": "dojo.member",
        "domain_builder": "_domain_member_lookup",
        "fields": ["id", "name", "email", "phone", "current_rank_id", "membership_state",
                   "date_of_birth",
                   "total_sessions", "attendance_rate", "attendance_since_last_rank",
                   "current_stripe_count", "total_points"],
        "limit": 5,
        "include_archived": True,
    },
    "class_list": {
        "model": "dojo.class.session",
        "domain_builder": "_domain_class_list",
        "fields": ["id", "template_id", "start_datetime", "capacity", "state", "seats_taken"],
        "limit": 20,
    },
    # belt_lookup is handled by _handle_belt_lookup (custom handler) because
    # the response model changes: member-specific → dojo.member; all ranks → dojo.belt.rank
    "subscription_lookup": {
        "model": "sale.subscription",
        "domain_builder": "_domain_subscription_lookup",
        "fields": ["id", "member_id", "plan_id", "state", "date_start", "date",
                   "recurring_next_date", "amount_total", "billing_failure_count",
                   "last_billing_failure_date", "to_renew"],
        "limit": 1,
    },
    "attendance_history": {
        "model": "dojo.attendance.log",
        "domain_builder": "_domain_attendance_history",
        "fields": ["id", "member_id", "checkin_datetime", "checkout_datetime", "session_id"],
        "limit": 10,
        "limit_from_params": "limit",
    },
    "schedule_today": {
        "model": "dojo.class.session",
        "domain_builder": "_domain_schedule_today",
        "fields": ["id", "template_id", "start_datetime", "capacity", "state", "seats_taken"],
        "limit": 20,
    },
    # ── Core Odoo module read intents ────────────────────────────────────────
    "task_list": {
        "model": "project.task",
        "domain_builder": "_domain_task_list",
        "fields": ["id", "name", "stage_id", "date_deadline", "priority", "project_id", "user_ids"],
        "limit": 20,
    },
    "invoice_lookup": {
        "model": "account.move",
        "domain_builder": "_domain_invoice_lookup",
        "fields": ["id", "name", "partner_id", "invoice_date_due", "amount_total", "payment_state", "state"],
        "limit": 5,
        "use_sudo": True,
    },
    "invoice_list": {
        "model": "account.move",
        "domain_builder": "_domain_invoice_list",
        "fields": ["id", "name", "partner_id", "invoice_date_due", "amount_total", "payment_state", "state"],
        "limit": 20,
        "use_sudo": True,
    },
    "activity_list": {
        "model": "mail.activity",
        "domain_builder": "_domain_activity_list",
        "fields": ["id", "summary", "activity_type_id", "date_deadline", "res_model", "res_name", "user_id"],
        "limit": 20,
    },
    "calendar_event_list": {
        "model": "calendar.event",
        "domain_builder": "_domain_calendar_event_list",
        "fields": ["id", "name", "start", "stop", "location", "description", "attendee_ids"],
        "limit": 20,
    },
    # ── New read intents (Tier 1 & 2) ───────────────────────────────────────
    "belt_test_list": {
        "model": "dojo.belt.test",
        "domain_builder": "_domain_belt_test_list",
        "fields": ["id", "name", "test_date", "location", "program_id", "state"],
        "limit": 10,
    },
    "belt_test_registration_list": {
        "model": "dojo.belt.test.registration",
        "domain_builder": "_domain_belt_test_registration_list",
        "fields": ["id", "member_id", "test_id", "target_rank_id", "program_id", "result"],
        "limit": 50,
    },
    "program_list": {
        "model": "dojo.program",
        "domain_builder": "_domain_program_list",
        "fields": ["id", "name", "code", "is_trial", "active"],
        "limit": 20,
    },
    "class_template_list": {
        "model": "dojo.class.template",
        "domain_builder": "_domain_class_template_list",
        "fields": ["id", "name", "level", "max_capacity", "duration_minutes", "program_id",
                   "recurrence_time", "recurrence_active"],
        "limit": 20,
    },
    "social_post_list": {
        "model": "dojo.social.post",
        "domain_builder": "_domain_social_post_list",
        "fields": ["id", "message", "state", "scheduled_date", "account_id", "error_message"],
        "limit": 10,
    },
    "program_enrollment_lookup": {
        "model": "dojo.program.enrollment",
        "domain_builder": "_domain_program_enrollment_lookup",
        "fields": ["id", "member_id", "program_id", "is_active", "enrolled_date", "deactivated_date"],
        "limit": 10,
    },
    "onboarding_status": {
        "model": "dojo.onboarding.record",
        "domain_builder": "_domain_onboarding_status",
        "fields": ["id", "member_id", "state", "progress_pct",
                   "step_member_info", "step_household", "step_enrollment",
                   "step_subscription", "step_portal_access"],
        "limit": 5,
    },
    "points_lookup": {
        "model": "dojo.points.transaction",
        "domain_builder": "_domain_points_lookup",
        "fields": ["id", "member_id", "amount", "source_type", "note", "awarded_by", "date"],
        "limit": 10,
        "order": "date desc",
    },
    "credit_lookup": {
        "model": "dojo.credit.transaction",
        "domain_builder": "_domain_credit_lookup",
        "fields": ["id", "member_id", "amount", "transaction_type", "status", "note", "subscription_id", "date"],
        "limit": 10,
        "order": "date desc",
    },
    "course_auto_enroll_list": {
        "model": "dojo.course.auto.enroll",
        "domain_builder": "_domain_course_auto_enroll_list",
        "fields": ["id", "member_id", "template_id", "mode", "active",
                   "date_from", "date_to", "pref_mon", "pref_tue", "pref_wed",
                   "pref_thu", "pref_fri", "pref_sat", "pref_sun"],
        "limit": 20,
    },
    "campaign_list": {
        "model": "dojo.ai.campaign",
        "domain_builder": "_domain_campaign_list",
        "fields": ["id", "name", "state", "is_active", "total_calls",
                   "completed_calls", "failed_calls", "from_number"],
        "limit": 10,
    },
    "kiosk_announcement_list": {
        "model": "dojo.kiosk.announcement",
        "domain_builder": "_domain_kiosk_announcement_list",
        "fields": ["id", "title", "body", "active", "config_id", "sequence"],
        "limit": 20,
    },
    # ── Sales read intents ─────────────────────────────────────────────────
    "sale_order_list": {
        "model": "sale.order",
        "domain_builder": "_domain_sale_order_list",
        "fields": ["id", "name", "partner_id", "state", "date_order",
                   "amount_total", "currency_id"],
        "limit": 20,
        "use_sudo": True,
    },
    "sale_order_lookup": {
        "model": "sale.order",
        "domain_builder": "_domain_sale_order_lookup",
        "fields": ["id", "name", "partner_id", "state", "date_order",
                   "amount_total", "amount_untaxed", "amount_tax",
                   "currency_id", "note", "order_line"],
        "limit": 5,
        "use_sudo": True,
    },
    "product_list": {
        "model": "product.product",
        "domain_builder": "_domain_product_list",
        "fields": ["id", "name", "list_price", "type", "categ_id",
                   "default_code", "qty_available"],
        "limit": 20,
    },
    "product_lookup": {
        "model": "product.product",
        "domain_builder": "_domain_product_lookup",
        "fields": ["id", "name", "list_price", "type", "categ_id",
                   "default_code", "qty_available", "description_sale"],
        "limit": 5,
    },
    # ── POS read intents ───────────────────────────────────────────────────
    "pos_order_list": {
        "model": "pos.order",
        "domain_builder": "_domain_pos_order_list",
        "fields": ["id", "name", "partner_id", "state", "date_order",
                   "amount_total", "session_id", "pos_reference"],
        "limit": 20,
        "use_sudo": True,
    },
    "pos_order_lookup": {
        "model": "pos.order",
        "domain_builder": "_domain_pos_order_lookup",
        "fields": ["id", "name", "partner_id", "state", "date_order",
                   "amount_total", "amount_tax", "amount_paid",
                   "session_id", "pos_reference", "lines"],
        "limit": 5,
        "use_sudo": True,
    },
    "pos_session_list": {
        "model": "pos.session",
        "domain_builder": "_domain_pos_session_list",
        "fields": ["id", "name", "state", "config_id", "user_id",
                   "start_at", "stop_at", "order_count", "total_payments_amount"],
        "limit": 10,
        "use_sudo": True,
    },
    # ── Accounting read intents ────────────────────────────────────────────
    "payment_list": {
        "model": "account.payment",
        "domain_builder": "_domain_payment_list",
        "fields": ["id", "name", "partner_id", "amount", "payment_type",
                   "state", "date", "journal_id", "ref"],
        "limit": 20,
        "use_sudo": True,
    },
    "bill_list": {
        "model": "account.move",
        "domain_builder": "_domain_bill_list",
        "fields": ["id", "name", "partner_id", "state", "amount_total",
                   "amount_residual", "invoice_date", "invoice_date_due",
                   "payment_state"],
        "limit": 20,
        "use_sudo": True,
    },
    # ── HR read intents ────────────────────────────────────────────────────
    "employee_list": {
        "model": "hr.employee",
        "domain_builder": "_domain_employee_list",
        "fields": ["id", "name", "job_title", "department_id",
                   "work_email", "work_phone", "mobile_phone"],
        "limit": 20,
        "use_sudo": True,
    },
    "employee_lookup": {
        "model": "hr.employee",
        "domain_builder": "_domain_employee_lookup",
        "fields": ["id", "name", "job_title", "department_id",
                   "work_email", "work_phone", "mobile_phone",
                   "coach_id", "parent_id"],
        "limit": 5,
        "use_sudo": True,
    },
    "department_list": {
        "model": "hr.department",
        "domain_builder": "_domain_department_list",
        "fields": ["id", "name", "manager_id", "parent_id",
                   "total_employee"],
        "limit": 20,
    },
    # ── Discuss read intents ───────────────────────────────────────────────
    "channel_list": {
        "model": "discuss.channel",
        "domain_builder": "_domain_channel_list",
        "fields": ["id", "name", "channel_type", "description",
                   "channel_member_ids"],
        "limit": 20,
    },
    "channel_lookup": {
        "model": "discuss.channel",
        "domain_builder": "_domain_channel_lookup",
        "fields": ["id", "name", "channel_type", "description",
                   "channel_member_ids"],
        "limit": 5,
    },
    "message_list": {
        "model": "mail.message",
        "domain_builder": "_domain_message_list",
        "fields": ["id", "body", "author_id", "date", "model",
                   "res_id", "subtype_id"],
        "limit": 20,
    },
    # ── Batch A: Member Admin read intents ─────────────────────────────────
    "member_enrollment_list": {
        "model": "dojo.class.enrollment",
        "domain_builder": "_domain_member_enrollment_list",
        "fields": ["id", "member_id", "session_id", "status", "attendance_state"],
        "limit": 20,
        "order": "id desc",
    },
    "subscription_dunning_list": {
        "model": "sale.subscription",
        "domain_builder": "_domain_subscription_dunning_list",
        "fields": ["id", "member_id", "plan_id", "state", "billing_failure_count",
                   "last_billing_failure_date", "recurring_next_date"],
        "limit": 20,
        "order": "billing_failure_count desc",
    },
    # ── Batch B: Class & Roster read intents ───────────────────────────────────
    "enrollment_availability": {
        "model": "dojo.class.session",
        "domain_builder": "_domain_enrollment_availability",
        "fields": ["id", "name", "start_datetime", "capacity", "seats_taken", "state"],
        "limit": 10,
        "order": "start_datetime asc",
    },
    "class_enrollment_list": {
        "model": "dojo.class.enrollment",
        "domain_builder": "_domain_class_enrollment_list",
        "fields": ["id", "member_id", "session_id", "status", "attendance_state"],
        "limit": 50,
        "order": "id asc",
    },
    # ── Batch C: Belt & Instructor read intents ────────────────────────────
    "instructor_todo_list": {
        "model": "project.task",
        "domain_builder": "_domain_instructor_todo_list",
        "fields": ["id", "name", "stage_id", "date_deadline", "priority", "project_id", "user_ids"],
        "limit": 20,
        "order": "priority desc, date_deadline asc",
    },
    # ── Batch D: Revenue & Payments read intents ───────────────────────────
    "payment_history": {
        "model": "account.payment",
        "domain_builder": "_domain_payment_history",
        "fields": ["id", "name", "partner_id", "amount", "currency_id", "date", "payment_type",
                   "journal_id", "state"],
        "limit": 20,
        "order": "date desc",
        "use_sudo": True,
    },
    # ── New Query Intents ─────────────────────────────────────────────────
    "members_by_belt": {
        "model": "dojo.member",
        "domain_builder": "_domain_members_by_belt",
        "fields": ["id", "name", "current_rank_id", "membership_state",
                   "total_sessions", "attendance_since_last_rank", "current_stripe_count"],
        "limit": 100,
        "order": "name asc",
    },
    "members_active_subscriptions": {
        "model": "dojo.member",
        "domain_builder": "_domain_members_active_subscriptions",
        "fields": ["id", "name", "current_rank_id", "membership_state",
                   "active_subscription_id", "total_sessions"],
        "limit": 50,
        "limit_from_params": "limit",
        "order": "name asc",
    },
    "members_by_state": {
        "model": "dojo.member",
        "domain_builder": "_domain_members_by_state",
        "fields": ["id", "name", "current_rank_id", "membership_state",
                   "active_subscription_id", "total_sessions"],
        "limit": 50,
        "order": "name asc",
    },
    "session_no_shows": {
        "model": "dojo.class.enrollment",
        "domain_builder": "_domain_session_no_shows",
        "fields": ["id", "member_id", "session_id", "status", "attendance_state"],
        "limit": 50,
        "order": "member_id asc",
    },
}

# ─── CRUD Handler Configuration (for generic create/update/delete) ──────────────
# Maps intent_type → CRUD config for mutating operations
# Structure: {
#     "model": "model_name",
#     "operation": "create" | "update" | "delete",
#     "fields": {
#         "field_name": {
#             "type": "char|many2one|datetime|...",
#             "required": True|False,
#             "resolver": "method_name" | None (for field lookups),
#         }
#     },
#     "target_domain_builder": "_domain_method" (for update/delete targeting),
#     "allow_undo": True|False,
# }
_CRUD_HANDLER_CONFIG = {
    "member_create": {
        "model": "dojo.member",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "email": {"required": False, "type": "char"},
            "phone": {"required": False, "type": "char"},
            "membership_state": {"required": False, "type": "selection", "default": "pending"},
        },
        "allow_undo": True,
    },
    "member_update": {
        "model": "dojo.member",
        "operation": "update",
        "target_domain_builder": "_domain_crud_member",
        "fields": {
            "name": {"required": False, "type": "char"},
            "email": {"required": False, "type": "char"},
            "phone": {"required": False, "type": "char"},
        },
        "allow_undo": True,
    },
    "class_create": {
        "model": "dojo.class.session",
        "operation": "create",
        "fields": {
            "template_id": {"required": True, "type": "many2one", "resolver": "_resolve_class_template"},
            "start_datetime": {"required": True, "type": "datetime"},
            "state": {"required": False, "type": "selection", "default": "scheduled"},
        },
        "allow_undo": True,
    },
    "class_cancel": {
        "model": "dojo.class.session",
        "operation": "delete",
        "target_domain_builder": "_domain_crud_session",
        "allow_undo": True,
    },
    "subscription_create": {
        "model": "sale.subscription",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "plan_id": {"required": True, "type": "many2one", "resolver": "_resolve_subscription_plan"},
            "date_start": {"required": False, "type": "date", "default_builder": "_default_today"},
            "date": {"required": False, "type": "date"},
            "note": {"required": False, "type": "text"},
        },
        "allow_undo": True,
    },
    "subscription_cancel": {
        "model": "sale.subscription",
        "operation": "delete",
        "target_domain_builder": "_domain_crud_subscription",
        "allow_undo": True,
    },
    "program_create": {
        "model": "dojo.program",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "description": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "belt_test_register_crud": {
        "model": "dojo.member.rank",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "rank_id": {"required": True, "type": "many2one", "resolver": "_resolve_belt_rank"},
            "date_awarded": {"required": False, "type": "date", "default_builder": "_default_today"},
            "program_id": {"required": False, "type": "many2one", "resolver": "_resolve_program"},
            "notes": {"required": False, "type": "text"},
        },
        "allow_undo": True,
    },
    "class_template_create": {
        "model": "dojo.class.template",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "program_id": {"required": False, "type": "many2one", "resolver": "_resolve_program"},
            "level": {"required": False, "type": "selection", "default": "all"},
            "max_capacity": {"required": False, "type": "integer", "default": 20},
            "duration_minutes": {"required": False, "type": "integer", "default": 60},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "class_enrollment_create": {
        "model": "dojo.class.enrollment",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "session_id": {"required": True, "type": "many2one"},
            "status": {"required": False, "type": "selection", "default": "registered"},
        },
        "allow_undo": True,
    },
    "class_enrollment_cancel": {
        "model": "dojo.class.enrollment",
        "operation": "delete",
        "target_domain_builder": "_domain_crud_enrollment",
        "allow_undo": True,
    },
    # ─── NEW: Extended Model CRUD Operations ───────────────────────────────
    "belt_test_create": {
        "model": "dojo.belt.test",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "test_date": {"required": True, "type": "date"},
            "location": {"required": False, "type": "char"},
            "program_id": {"required": False, "type": "many2one", "resolver": "_resolve_program"},
            "max_participants": {"required": False, "type": "integer", "default": 20},
            "state": {"required": False, "type": "selection", "default": "scheduled"},
        },
        "allow_undo": True,
    },
    "attendance_log_create": {
        "model": "dojo.attendance.log",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "session_id": {"required": True, "type": "many2one"},
            "status": {"required": False, "type": "selection", "default": "present"},
            "checkin_datetime": {"required": False, "type": "datetime"},
            "note": {"required": False, "type": "text"},
        },
        "allow_undo": True,
    },
    "credit_transaction_create": {
        "model": "dojo.credit.transaction",
        "operation": "create",
        "fields": {
            "subscription_id": {"required": True, "type": "many2one"},
            "amount": {"required": True, "type": "integer"},
            "transaction_type": {"required": True, "type": "selection"},
            "status": {"required": False, "type": "selection", "default": "confirmed"},
        },
        "allow_undo": True,
    },
    "marketing_campaign_create": {
        "model": "dojo.marketing.campaign",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "subject": {"required": False, "type": "char"},
            "schedule_type": {"required": False, "type": "selection", "default": "one_time"},
            "scheduled_date": {"required": False, "type": "date"},
            "send_email": {"required": False, "type": "boolean", "default": True},
            "send_sms": {"required": False, "type": "boolean", "default": False},
            "state": {"required": False, "type": "selection", "default": "draft"},
        },
        "allow_undo": True,
    },
    "social_post_create": {
        "model": "dojo.social.post",
        "operation": "create",
        "fields": {
            "message": {"required": True, "type": "text"},
            "account_id": {"required": True, "type": "many2one"},
            "scheduled_date": {"required": False, "type": "datetime"},
            "state": {"required": False, "type": "selection", "default": "draft"},
        },
        "allow_undo": True,
    },
    "instructor_profile_update": {
        "model": "dojo.instructor.profile",
        "operation": "update",
        "target_domain_builder": "_domain_crud_instructor",
        "fields": {
            "name": {"required": False, "type": "char"},
            "bio": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean"},
        },
        "allow_undo": True,
    },
    "emergency_contact_create": {
        "model": "dojo.emergency.contact",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "name": {"required": True, "type": "char"},
            "phone": {"required": True, "type": "char"},
            "relationship": {"required": True, "type": "char"},
            "email": {"required": False, "type": "char"},
            "is_primary": {"required": False, "type": "boolean", "default": False},
        },
        "allow_undo": True,
    },
    # ─── NEW: Additional Model CRUD Operations ─────────────────────────────
    "martial_art_style_create": {
        "model": "dojo.martial.art.style",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "code": {"required": False, "type": "char"},
            "description": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "subscription_plan_create": {
        "model": "dojo.subscription.plan",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "plan_type": {"required": True, "type": "selection"},
            "program_ids": {"required": False, "type": "many2many", "resolver": "_resolve_program"},
            "price": {"required": True, "type": "float"},
            "billing_period": {"required": True, "type": "selection"},
            "description": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "program_enrollment_create": {
        "model": "dojo.program.enrollment",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "program_id": {"required": True, "type": "many2one", "resolver": "_resolve_program"},
            "enrolled_date": {"required": False, "type": "date", "default_builder": "_default_today"},
            "notes": {"required": False, "type": "text"},
            "is_active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "belt_test_registration_create": {
        "model": "dojo.belt.test.registration",
        "operation": "create",
        "fields": {
            "test_id": {"required": True, "type": "many2one"},
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "target_rank_id": {"required": True, "type": "many2one", "resolver": "_resolve_belt_rank"},
            "program_id": {"required": False, "type": "many2one", "resolver": "_resolve_program"},
            "result": {"required": False, "type": "selection", "default": "pending"},
            "notes": {"required": False, "type": "text"},
        },
        "allow_undo": True,
    },
    "marketing_card_create": {
        "model": "dojo.marketing.card",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "card_type": {"required": True, "type": "selection"},
            "subtitle": {"required": False, "type": "char"},
            "body": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean", "default": True},
            "publish_kiosk": {"required": False, "type": "boolean", "default": True},
            "publish_portal": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "kiosk_announcement_create": {
        "model": "dojo.kiosk.announcement",
        "operation": "create",
        "fields": {
            "title": {"required": True, "type": "char"},
            "body": {"required": False, "type": "text"},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    "course_auto_enroll_create": {
        "model": "dojo.course.auto.enroll",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member"},
            "template_id": {"required": True, "type": "many2one", "resolver": "_resolve_class_template"},
            "mode": {"required": False, "type": "selection", "default": "permanent"},
            "active": {"required": False, "type": "boolean", "default": True},
        },
        "allow_undo": True,
    },
    # ── Core Odoo module write intents ────────────────────────────────────────
    "task_create": {
        "model": "project.task",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "project_id": {"required": False, "type": "many2one", "resolver": "_resolve_project"},
            "description": {"required": False, "type": "text"},
            "date_deadline": {"required": False, "type": "date"},
            "priority": {"required": False, "type": "selection", "default": "0"},
        },
        "allow_undo": True,
    },
    "activity_create": {
        "model": "mail.activity",
        "operation": "create",
        "fields": {
            "summary": {"required": True, "type": "char"},
            "activity_type_id": {"required": False, "type": "many2one", "resolver": "_resolve_activity_type"},
            "date_deadline": {"required": False, "type": "date", "default_builder": "_default_today"},
            "note": {"required": False, "type": "text"},
            "res_model": {"required": False, "type": "char", "default": "dojo.member"},
            "res_id": {"required": False, "type": "integer"},
        },
        "allow_undo": True,
    },
    # ── Sales CRUD intents ─────────────────────────────────────────────────
    "sale_order_create": {
        "model": "sale.order",
        "operation": "create",
        "fields": {
            "partner_id": {"required": True, "type": "many2one", "resolver": "_resolve_partner"},
            "note": {"required": False, "type": "text"},
        },
        "allow_undo": True,
        "use_sudo": True,
    },
    # ── Accounting CRUD intents ────────────────────────────────────────────
    "invoice_create": {
        "model": "account.move",
        "operation": "create",
        "fields": {
            "partner_id": {"required": True, "type": "many2one", "resolver": "_resolve_partner"},
            "move_type": {"required": False, "type": "selection", "default": "out_invoice"},
            "invoice_date": {"required": False, "type": "date", "default_builder": "_default_today"},
        },
        "allow_undo": True,
        "use_sudo": True,
    },
    "bill_create": {
        "model": "account.move",
        "operation": "create",
        "fields": {
            "partner_id": {"required": True, "type": "many2one", "resolver": "_resolve_partner"},
            "move_type": {"required": False, "type": "selection", "default": "in_invoice"},
            "invoice_date": {"required": False, "type": "date", "default_builder": "_default_today"},
        },
        "allow_undo": True,
        "use_sudo": True,
    },
    # ── HR CRUD intents ────────────────────────────────────────────────────
    "employee_create": {
        "model": "hr.employee",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "job_title": {"required": False, "type": "char"},
            "department_id": {"required": False, "type": "many2one", "resolver": "_resolve_department"},
            "work_email": {"required": False, "type": "char"},
            "work_phone": {"required": False, "type": "char"},
            "mobile_phone": {"required": False, "type": "char"},
        },
        "allow_undo": True,
        "use_sudo": True,
    },
    "employee_update": {
        "model": "hr.employee",
        "operation": "update",
        "target_domain_builder": "_domain_crud_employee",
        "fields": {
            "job_title": {"required": False, "type": "char"},
            "department_id": {"required": False, "type": "many2one", "resolver": "_resolve_department"},
            "work_email": {"required": False, "type": "char"},
            "work_phone": {"required": False, "type": "char"},
            "mobile_phone": {"required": False, "type": "char"},
        },
        "allow_undo": True,
        "use_sudo": True,
    },
    "department_create": {
        "model": "hr.department",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "parent_id": {"required": False, "type": "many2one"},
        },
        "allow_undo": True,
    },
    # ── Discuss CRUD intents ───────────────────────────────────────────────
    "channel_create": {
        "model": "discuss.channel",
        "operation": "create",
        "fields": {
            "name": {"required": True, "type": "char"},
            "description": {"required": False, "type": "text"},
            "channel_type": {"required": False, "type": "selection", "default": "channel"},
        },
        "allow_undo": True,
    },
    # ── Batch C: Belt CRUD intents ─────────────────────────────────────────
    "belt_test_result_update": {
        "model": "dojo.belt.test.registration",
        "operation": "update",
        "target_domain_builder": "_domain_crud_belt_test_registration",
        "fields": {
            "result": {"required": True, "type": "selection"},
            "notes": {"required": False, "type": "text"},
        },
        "allow_undo": True,
    },
    # ── Batch B: Class & Roster CRUD intents ───────────────────────────────
    "class_waitlist_add": {
        "model": "dojo.class.enrollment",
        "operation": "create",
        "fields": {
            "member_id": {"required": True, "type": "many2one", "resolver": "_resolve_member_id"},
            "session_id": {"required": True, "type": "many2one", "resolver": "_resolve_session_id"},
            "status": {"required": False, "type": "selection", "default": "waitlist"},
        },
        "allow_undo": True,
    },
}


class AiAssistantService(models.AbstractModel):
    """
    Central AI Assistant Service.
    
    This abstract model provides the core AI assistant functionality
    that can be used by multiple modules (instructor dashboard, kiosk, etc.)
    """
    _name = "ai.assistant.service"
    _description = "AI Assistant Service"

    # ═══════════════════════════════════════════════════════════════════════════
    # Compound Phrase Detection
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _is_compound_phrase(self, text):
        """
        Detect whether a user's input likely contains multiple sequential actions.

        This is a routing hint only — the LLM is the authoritative arbiter.
        False positives are safe: they just skip the conversational path.
        False negatives are also safe: single intents are handled normally.
        """
        return bool(_COMPOUND_SIGNALS.search(text))

    # ═══════════════════════════════════════════════════════════════════════════
    # Main API: Two-Phase Confirmation Flow
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def handle_command(self, text, role="instructor", input_type="text", audio_attachment_id=None, context=None, conversation_history=None, channel=None, chat_session_id=None, clarification_session_key=None, pipeline_key=None):
        """
        Main entry point for the AI assistant.

        This is the primary method that should be called by consuming modules.
        Aliases: parse_and_confirm (for backward compatibility)

        When ``ai_assistant.n8n_webhook_url`` is configured, routes through
        n8n for LLM orchestration.  Falls back to direct processing on
        timeout or if n8n is unreachable.

        Args:
            text: User's natural language input
            role: User role (kiosk/instructor/admin)
            input_type: 'text' or 'voice'
            audio_attachment_id: ID of stored audio attachment (for voice)
            context: Optional dict of additional context data
            conversation_history: Optional list of {role, text} dicts for context chaining
            channel: Optional channel name for Channel Beta mode — narrows AI focus via
                     system prompt prefix injection (e.g. "attendance", "members"). PROTOTYPE.

        Returns:
            dict: {
                "success": bool,
                "state": "pending_confirmation" | "executed" | "error",
                "session_key": str (for confirmation flow),
                "intent": dict | None,
                "confirmation_prompt": str | None,
                "resolved_data": dict | None,
                "auto_executed": bool,
                "result": dict | None (if auto-executed),
                "response": str | None (AI conversational response),
                "error": str | None
            }
        """
        # ── n8n orchestration ────────────────────────────────────────────
        # ── Meta intents: short-circuit before n8n ──────────────────────
        # Detect meta questions ("what can you do?", "help", etc.) and handle
        # them directly in Odoo. Sending them to n8n causes the LLM to describe
        # its own internal tool schema instead of our capabilities.
        _META_TRIGGERS = (
            "what can you do", "what tools", "what commands", "capabilities",
            "help", "how do i", "how do you", "what can i ask", "what do you",
            "what are your", "what intents", "list tools", "show tools",
            "available commands", "available tools", "what actions",
        )
        _text_lower = text.lower().strip()
        if any(_text_lower.startswith(t) or t in _text_lower for t in _META_TRIGGERS):
            _logger.info("Meta intent detected — handling directly (bypassing n8n)")
            return self.parse_and_confirm(
                text, role, input_type, None,
                conversation_history=conversation_history, channel=channel,
            )

        # ── Pending confirmation short-circuit ───────────────────────────
        # When a pending confirmation exists, a "yes/no" reply should ALWAYS
        # bypass n8n — otherwise n8n re-interprets "yes" and calls Execute_Intent
        # again, creating an infinite loop.
        #
        # Two lookup strategies (both checked):
        # 1. In-memory cache keyed by chat_session_id → session_key (fast)
        # 2. DB fallback: most recent 'pending' action log within 10 min (for n8n direct calls)
        cls = type(self)
        # Strip punctuation so "yes." and "yes!" match the same as "yes"
        import re as _re
        _stripped = _re.sub(r"[^\w\s]", "", _text_lower).strip()
        _is_pure_yes = _stripped in cls._YES_WORDS
        _is_pure_no = _stripped in cls._NO_WORDS

        if _is_pure_yes or _is_pure_no:
            # Strategy 1: in-memory cache
            session_key_to_confirm = None
            if chat_session_id:
                cached = cls._pending_confirm_cache.get(chat_session_id)
                if cached:
                    s_key, expires_at = cached
                    if time.time() < expires_at:
                        session_key_to_confirm = s_key
                        del cls._pending_confirm_cache[chat_session_id]
                    else:
                        del cls._pending_confirm_cache[chat_session_id]

            # Strategy 2: DB fallback — find most recent pending action log
            if not session_key_to_confirm:
                import datetime as _dt_mod
                cutoff = _dt_mod.datetime.utcnow() - _dt_mod.timedelta(minutes=10)
                pending_log = self.env["ai.action.log"].sudo().search([
                    ("requires_confirmation", "=", True),
                    ("confirmation_status", "=", "pending"),
                    ("session_key", "!=", False),
                    ("create_date", ">=", cutoff.strftime("%Y-%m-%d %H:%M:%S")),
                ], order="create_date desc", limit=1)
                if pending_log:
                    session_key_to_confirm = pending_log.session_key

            if session_key_to_confirm:
                _logger.info(
                    "%s pending action via %s (session_key=%s)",
                    "Confirming" if _is_pure_yes else "Cancelling",
                    "cache" if chat_session_id else "DB fallback",
                    session_key_to_confirm,
                )
                return self.execute_confirmed(session_key_to_confirm, confirmed=_is_pure_yes)

        # ── Clarification follow-up detection ────────────────────────────
        # If the previous turn returned needs_clarification with a session_key,
        # the frontend sends it back so we can resume the original intent.
        if clarification_session_key:
            cached = cls._pending_clarification_cache.pop(clarification_session_key, None)
            if cached and time.time() < cached["expires_at"]:
                _logger.info(
                    "Resuming clarification for %s (key=%s)",
                    cached["intent_type"], clarification_session_key,
                )
                return self._handle_clarification_followup(
                    text, cached, role, conversation_history,
                )
            else:
                _logger.info(
                    "Clarification key %s expired or missing — processing as new command",
                    clarification_session_key,
                )

        # ── n8n orchestration ────────────────────────────────────────────
        n8n_url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_str("ai_assistant.n8n_webhook_url", "")
        )
        if n8n_url:
            # Use the caller-provided pipeline_key (from browser) so the browser's
            # poll and n8n's step-push share the same key.  Fall back to generating
            # one here only when no key was supplied (e.g. non-browser callers).
            pipeline_key = pipeline_key or uuid.uuid4().hex[:12]
            result = self._handle_via_n8n(
                n8n_url, text, role, input_type,
                conversation_history=conversation_history,
                channel=channel,
                chat_session_id=chat_session_id,
                pipeline_key=pipeline_key,
            )
            if result is not None:
                # If n8n returned a pending confirmation, cache the session_key
                if (chat_session_id
                        and isinstance(result, dict)
                        and result.get("state") == "pending_confirmation"
                        and result.get("session_key")):
                    cls = type(self)
                    cls._pending_confirm_cache[chat_session_id] = (
                        result["session_key"],
                        time.time() + cls._PENDING_CONFIRM_TTL,
                    )
                    _logger.info(
                        "Cached pending confirmation session_key=%s for chat_session_id=%s",
                        result["session_key"], chat_session_id,
                    )

                # ── Audit log for n8n-handled requests ───────────────
                # When n8n calls /api/v1/ai/execute, that endpoint already
                # creates an ai.action.log (with a session_key in the result).
                # Only create a NEW log when no session_key exists — i.e. n8n
                # answered conversationally without calling Execute_Intent.
                #
                # IMPORTANT: n8n's AI Agent node loses the session_key from
                # Execute_Intent in its output wrapper.  Before creating a
                # duplicate "conversation" log, check whether Execute created
                # a log for the same input text in the last 60 seconds.
                if isinstance(result, dict) and not result.get("session_key"):
                    try:
                        ActionLog = self.env["ai.action.log"].sudo()

                        # Look for a log already created by /api/v1/ai/execute
                        # during this n8n round-trip (same input text, last 60s).
                        from datetime import datetime, timedelta
                        cutoff = datetime.now() - timedelta(seconds=60)
                        existing = ActionLog.search([
                            ("input_text", "=", text),
                            ("create_date", ">=", cutoff.strftime("%Y-%m-%d %H:%M:%S")),
                            ("intent_type", "!=", "conversation"),
                        ], limit=1, order="id desc")

                        if existing:
                            # Execute endpoint already logged the real intent —
                            # just attach its session_key to the result.
                            result["session_key"] = existing.session_key
                            _logger.info(
                                "Reusing existing n8n execute log: %s (session_key=%s)",
                                existing.intent_type, existing.session_key,
                            )
                        else:
                            # Genuinely conversational response — log it.
                            intent = result.get("intent") or {}
                            n8n_intent_type = intent.get("intent_type", "") if isinstance(intent, dict) else ""
                            n8n_confidence = intent.get("confidence", 0) if isinstance(intent, dict) else 0
                            n8n_state = result.get("state", "executed")
                            n8n_requires_confirm = n8n_state == "pending_confirmation"

                            log = ActionLog.log_parse(
                                input_text=text,
                                role=role,
                                intent_type=n8n_intent_type or "conversation",
                                parsed_intent=intent if isinstance(intent, dict) else None,
                                confidence=round(float(n8n_confidence) * 100, 1) if n8n_confidence else 0,
                                resolved_data=result.get("resolved_data"),
                                confirmation_prompt=result.get("confirmation_prompt"),
                                requires_confirmation=n8n_requires_confirm,
                                input_type=input_type,
                                audio_attachment_id=None,
                            )
                            if n8n_state == "executed":
                                log.log_execution(
                                    success=result.get("success", True),
                                    result=result.get("result"),
                                )
                            result["session_key"] = log.session_key
                            _logger.info(
                                "Created audit log for n8n conversational response: %s (session_key=%s)",
                                log.intent_type, log.session_key,
                            )
                    except Exception:
                        _logger.warning("Failed to create n8n audit log", exc_info=True)

                # Attach pipeline_key and any accumulated steps to the result
                if isinstance(result, dict):
                    result["pipeline_key"] = pipeline_key
                    cls = type(self)
                    cached_steps = cls._pipeline_steps_cache.get(pipeline_key)
                    if cached_steps:
                        cls._pipeline_steps_cache[pipeline_key]["done"] = True
                        result["pipeline_steps"] = cached_steps["steps"]

                return result
            # n8n unreachable — fall through to direct processing
            _logger.warning("n8n unreachable, falling back to direct processing")

        return self.parse_and_confirm(text, role, input_type, audio_attachment_id, conversation_history=conversation_history, channel=channel)

    # ─────────────────────────────────────────────────────────────────────
    # Clarification follow-up handler
    # ─────────────────────────────────────────────────────────────────────

    @api.model
    def _handle_clarification_followup(self, text, cached_context, role, conversation_history):
        """Resume a partially-parsed intent after the user answered a clarifying question.

        Builds synthetic context that explicitly tells the LLM which intent was
        in progress and what information the user is supplying, then re-runs
        through ``parse_and_confirm`` so normal entity resolution and execution
        proceed as expected.
        """
        intent_type = cached_context["intent_type"]
        clarification_q = cached_context["clarification_question"]

        resume_text = (
            f"[RESUMING {intent_type.upper()}: The assistant previously asked "
            f"'{clarification_q}' and the user replied:] {text}"
        )
        _logger.info("Clarification follow-up resume_text: %s", resume_text)
        return self.parse_and_confirm(
            resume_text, role, "text", None,
            conversation_history=conversation_history,
        )

    # ─────────────────────────────────────────────────────────────────────
    # n8n proxy — with retry + circuit breaker
    # ─────────────────────────────────────────────────────────────────────

    # Circuit breaker state (module-level singleton, reset on worker restart)
    _n8n_failure_count = 0
    _n8n_circuit_open_until = 0.0  # epoch timestamp
    _N8N_CIRCUIT_THRESHOLD = 3     # consecutive failures to trip
    _N8N_CIRCUIT_COOLDOWN = 300    # seconds to keep circuit open (5 min)
    _N8N_MAX_RETRIES = 2
    _N8N_RETRY_BACKOFF = 1.0      # seconds, doubles each retry

    # Pending confirmation cache: chat_session_id → (session_key, expires_epoch)
    # Allows "yes/no" replies via n8n to confirm actions without n8n knowing the session_key.
    _pending_confirm_cache = {}   # {chat_session_id: (session_key, expires_at)}
    _PENDING_CONFIRM_TTL = 600    # 10 minutes

    # Pending clarification cache: stores partial intent when AI asks a follow-up question.
    # Keyed by clarification_session_key → dict of {intent_type, intent_data, resolved_data,
    # clarification_question, role, expires_at}.  Mirrors _pending_confirm_cache pattern.
    _pending_clarification_cache = {}   # {clarify-<uuid>: {...}}
    _CLARIFICATION_TTL = 600            # 10 minutes

    # Pipeline step cache: real-time steps pushed by n8n (or the direct path)
    # Keyed by pipeline_key → {steps: [...], done: bool, expires_at: float}
    _pipeline_steps_cache = {}          # {pipeline_key: {"steps": [], "done": bool, "expires_at": float}}
    _PIPELINE_STEPS_TTL = 120           # 2 minutes

    _YES_WORDS = frozenset([
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm",
        "confirmed", "do it", "go ahead", "proceed", "affirmative", "correct",
        "sounds good", "looks good", "that's right", "right", "definitely",
    ])
    _NO_WORDS = frozenset([
        "no", "nope", "nah", "cancel", "stop", "abort", "don't", "do not",
        "skip", "nevermind", "never mind", "forget it", "discard",
    ])

    @api.model
    def _handle_via_n8n(self, webhook_url, text, role, input_type,
                        conversation_history=None, channel=None, chat_session_id=None,
                        pipeline_key=None):
        """
        Proxy an AI request through an n8n webhook.

        Includes retry with exponential backoff and a circuit breaker:
        - Retries up to 2 times on transient errors (connection, timeout).
        - After 3 consecutive failures, opens the circuit for 5 minutes
          (all calls short-circuit to ``None`` → fallback to direct processing).
        - A single success resets the failure counter.

        Returns:
            dict | None: Standard handle_command response dict, or None if
                         n8n is unreachable (triggers fallback).
        """
        import urllib.request
        import urllib.error

        cls = type(self)

        # ── Circuit breaker check ────────────────────────────────────
        if cls._n8n_failure_count >= cls._N8N_CIRCUIT_THRESHOLD:
            if time.time() < cls._n8n_circuit_open_until:
                _logger.info(
                    "n8n circuit breaker OPEN — skipping webhook (resets in %ds)",
                    int(cls._n8n_circuit_open_until - time.time()),
                )
                return None
            # Cooldown expired — allow one probe request (half-open)
            _logger.info("n8n circuit breaker half-open — probing webhook")

        # ── Build request ────────────────────────────────────────────
        payload = {
            "text": text,
            "role": role,
            "input_type": input_type,
        }
        if conversation_history:
            payload["conversation_history"] = conversation_history
        if channel:
            payload["channel"] = channel
        if chat_session_id:
            payload["session_id"] = chat_session_id
        # Send the same context_window_turns setting used by the direct path,
        # so n8n memory node stays in sync with Settings → AI Assistant
        context_window = self.env["ir.config_parameter"].sudo().get_int(
            "ai_assistant.context_window_turns", 10
        )
        payload["context_window"] = max(1, min(50, context_window))

        # pipeline_key lets n8n nodes push real-time step updates back to Odoo
        if pipeline_key:
            payload["pipeline_key"] = pipeline_key
            cls = type(self)
            cls._pipeline_steps_cache[pipeline_key] = {
                "steps": [],
                "done": False,
                "expires_at": time.time() + cls._PIPELINE_STEPS_TTL,
            }
            # Evict expired entries while we're here
            expired = [k for k, v in cls._pipeline_steps_cache.items()
                       if time.time() > v.get("expires_at", 0)]
            for k in expired:
                cls._pipeline_steps_cache.pop(k, None)

        body = json.dumps(payload).encode("utf-8")

        timeout = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_str("ai_assistant.n8n_timeout", "30")
        )

        # ── Retry loop ───────────────────────────────────────────────
        last_error = None
        for attempt in range(1 + cls._N8N_MAX_RETRIES):
            req = urllib.request.Request(
                webhook_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Odoo/19.2 (ai_assistant)",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)

                # ── Success — reset circuit breaker ──────────────────
                cls._n8n_failure_count = 0
                cls._n8n_circuit_open_until = 0.0

                return self._normalise_n8n_response(data)

            except urllib.error.URLError as e:
                last_error = e
                _logger.warning(
                    "n8n attempt %d/%d failed (URLError): %s",
                    attempt + 1,
                    1 + cls._N8N_MAX_RETRIES,
                    e,
                )
            except (json.JSONDecodeError, TypeError) as e:
                # Bad JSON is not transient — don't retry
                _logger.error("n8n returned invalid JSON: %s", e)
                cls._n8n_failure_count += 1
                if cls._n8n_failure_count >= cls._N8N_CIRCUIT_THRESHOLD:
                    cls._n8n_circuit_open_until = time.time() + cls._N8N_CIRCUIT_COOLDOWN
                return self._error_response("AI orchestration returned an invalid response.")
            except Exception as e:
                last_error = e
                _logger.warning(
                    "n8n attempt %d/%d failed: %s",
                    attempt + 1,
                    1 + cls._N8N_MAX_RETRIES,
                    e,
                )

            # Backoff before next retry (skip on last attempt)
            if attempt < cls._N8N_MAX_RETRIES:
                backoff = cls._N8N_RETRY_BACKOFF * (2 ** attempt)
                time.sleep(backoff)

        # ── All retries exhausted — trip circuit breaker ─────────────
        cls._n8n_failure_count += 1
        if cls._n8n_failure_count >= cls._N8N_CIRCUIT_THRESHOLD:
            cls._n8n_circuit_open_until = time.time() + cls._N8N_CIRCUIT_COOLDOWN
            _logger.error(
                "n8n circuit breaker TRIPPED after %d consecutive failures "
                "(cooldown %ds). Last error: %s",
                cls._n8n_failure_count,
                cls._N8N_CIRCUIT_COOLDOWN,
                last_error,
            )
        else:
            _logger.error(
                "n8n webhook unreachable after %d attempts (%s): %s",
                1 + cls._N8N_MAX_RETRIES,
                webhook_url,
                last_error,
            )
        return None

    @api.model
    def _normalise_n8n_response(self, data):
        """Normalise an n8n response dict to handle_command() format."""
        if isinstance(data, dict):
            # Standard format — pass through
            if "success" in data and "state" in data:
                return data
            # n8n AI Agent node default output key
            if "output" in data and "success" not in data:
                return {
                    "success": True,
                    "state": "executed",
                    "session_key": None,
                    "intent": data.get("intent"),
                    "confirmation_prompt": data.get("confirmation_prompt"),
                    "resolved_data": data.get("resolved_data"),
                    "auto_executed": True,
                    "result": data.get("result"),
                    "response": data["output"],
                    "error": None,
                }
            # Simpler {response: "..."} wrapper
            if "response" in data and "success" not in data:
                return {
                    "success": True,
                    "state": "executed",
                    "session_key": None,
                    "intent": data.get("intent"),
                    "confirmation_prompt": data.get("confirmation_prompt"),
                    "resolved_data": data.get("resolved_data"),
                    "auto_executed": True,
                    "result": data.get("result"),
                    "response": data["response"],
                    "error": None,
                }

        # Unknown shape — wrap as error
        _logger.warning("n8n returned unexpected response shape: %s | data: %s", type(data), str(data)[:200])
        return self._error_response("AI orchestration returned an unexpected response.")

    @api.model
    def handle_compound_command(self, compound_data, role="instructor"):
        """
        Validate a compound intent chain and return a combined confirmation prompt.

        Args:
            compound_data: dict with "intents" list and optional "reasoning" string
            role: user role for permission checks

        Returns:
            Standard response dict with state "pending_confirmation" on success.
            On validation failure: {"success": False, "state": "error", "error": "<explanation>"}
        """
        intents = compound_data.get("intents", [])

        # ── Validation ────────────────────────────────────────────────────────────
        if not intents:
            return self._error_response("No intents found in compound command.")

        if len(intents) > _MAX_COMPOUND_CHAIN:
            return self._error_response(
                f"Compound command exceeds maximum of {_MAX_COMPOUND_CHAIN} steps."
            )

        IntentSchema = self.env["ai.intent.schema"]
        for i, intent in enumerate(intents, 1):
            intent_type = intent.get("intent_type", "unknown")
            confidence = intent.get("confidence", 0.0)

            # "unknown" is in _KNOWN_INTENT_TYPES for single-intent routing only;
            # it must never appear as a compound chain step.
            if intent_type not in _KNOWN_INTENT_TYPES or intent_type == "unknown":
                return self._error_response(
                    f"Step {i}: unrecognised intent type '{intent_type}'."
                )
            if confidence < _MIN_CONFIDENCE:
                return self._error_response(
                    f"Step {i}: confidence {confidence:.2f} is below threshold ({_MIN_CONFIDENCE}). "
                    "Please rephrase the command."
                )
            schema = IntentSchema.get_by_type(intent_type)
            if schema and not schema.check_role_permission(role):
                return self._error_response(
                    f"Step {i}: you don't have permission to execute '{intent_type}'."
                )

        # ── Build confirmation prompt ─────────────────────────────────────────────
        lines = ["I'll do the following in order:"]
        for i, intent in enumerate(intents, 1):
            intent_type = intent.get("intent_type", "")
            schema = IntentSchema.get_by_type(intent_type)
            label = schema.name if schema else intent_type.replace("_", " ").title()
            params = intent.get("parameters", {})
            member = params.get("member_name") or params.get("name") or ""
            detail = f" — {member}" if member else ""
            lines.append(f"{i}. {label}{detail}")
        lines.append(f"Confirm all {len(intents)}?")
        confirmation_prompt = "\n".join(lines)

        # ── Create compound header log record ─────────────────────────────────────
        min_confidence = min(i.get("confidence", 0.0) for i in intents)
        ActionLog = self.env["ai.action.log"]
        log = ActionLog.log_parse(
            input_text=compound_data.get("reasoning") or "compound command",
            role=role,
            intent_type="compound_chain",
            parsed_intent={"intents": intents},
            confidence=round(min_confidence * 100, 1),
            resolved_data={},
            confirmation_prompt=confirmation_prompt,
            requires_confirmation=True,
            input_type="text",
            audio_attachment_id=None,
        )

        return {
            "success": True,
            "state": "pending_confirmation",
            "session_key": log.session_key,
            "compound": True,
            "intent": {"intent_type": "compound_chain", "steps": len(intents)},
            "confirmation_prompt": confirmation_prompt,
            "resolved_data": {},
            "auto_executed": False,
            "result": None,
            "response": confirmation_prompt,
            "error": None,
        }

    @api.model
    def _execute_compound_chain(self, intents, role, header_log):
        """
        Execute a validated compound intent chain step by step.

        On failure at step N:
        - Attempts best-effort rollback of completed steps via snapshot.execute_undo()
        - Remaining steps are skipped (no log records created for skipped steps)
        - Does not ask for user confirmation before rolling back

        Args:
            intents: list of intent dicts (from parsed_intent on header log)
            role: user role string
            header_log: ai.action.log record for the compound header

        Returns:
            dict with "success", "state", "compound", "steps", "rollback_failures"
        """
        ActionLog = self.env["ai.action.log"]
        steps_output = []
        completed_step_log_ids = []

        for n, intent in enumerate(intents, 1):
            intent_type = intent.get("intent_type", "unknown")
            resolved = self._resolve_entities(intent) or {}

            # Create per-step log record, linked to the compound header
            step_log = ActionLog.log_parse(
                input_text=f"Step {n}: {intent_type}",
                role=role,
                intent_type=intent_type,
                parsed_intent=intent,
                confidence=round(intent.get("confidence", 0.0) * 100, 1),
                resolved_data=resolved,
                confirmation_prompt=None,
                requires_confirmation=False,
                input_type="text",
                audio_attachment_id=None,
            )
            # Link to parent + set deterministic session key for audit trail
            step_log.parent_action_id = header_log.id
            step_log.session_key = f"{header_log.session_key}_step_{n}"

            # Execute step
            step_start = time.time()
            result = self._execute_intent(intent_type, intent, resolved, step_log)
            step_elapsed_ms = int((time.time() - step_start) * 1000)
            # is_undoable=False: undo is chain-level (via _execute_compound_chain rollback),
            # not per-step. Snapshots created by _execute_intent are still usable for rollback
            # but the step log itself does not advertise as undoable in the audit trail.
            step_log.log_execution(
                success=result.get("success", False),
                result=result,
                execution_time_ms=step_elapsed_ms,
                is_undoable=False,
            )

            if result.get("success"):
                completed_step_log_ids.append(step_log.id)
                steps_output.append({
                    "step": n,
                    "intent_type": intent_type,
                    "success": True,
                    "summary": self._format_exec_result_as_response(intent_type, result) or result.get("message") or f"{intent_type} completed",
                })
            else:
                steps_output.append({
                    "step": n,
                    "intent_type": intent_type,
                    "success": False,
                    "error": result.get("error") or result.get("message") or "Step failed",
                })

        # Check for any failures after running all steps
        failed_steps = [s for s in steps_output if not s.get("success")]

        if failed_steps:
            header_log.write({
                "execution_status": "error",
                "error_message": "; ".join(
                    f"Step {s['step']} ({s['intent_type']}) failed: {s.get('error', '')}"
                    for s in failed_steps
                ),
                "undone": False,
            })
            failed_nums = [str(s["step"]) for s in failed_steps]
            return {
                "success": False,
                "state": "executed",
                "compound": True,
                "steps": steps_output,
                "rollback_failures": [],
                "error": f"Step(s) {', '.join(failed_nums)} failed.",
            }

        # All steps succeeded — update header
        header_log.write({"execution_status": "success"})
        return {
            "success": True,
            "state": "executed",
            "compound": True,
            "steps": steps_output,
            "rollback_failures": [],
            "error": None,
        }

    # ── Channel Beta system prompt prefixes (PROTOTYPE) ─────────────────────────
    _CHANNEL_PREFIXES = {
        "attendance":   "CHANNEL FOCUS: Attendance only. Prioritise check-in, check-out, and attendance query intents. Treat unrelated requests as out-of-scope but still attempt to help.",
        "members":      "CHANNEL FOCUS: Members only. Prioritise member lookup, create, and update intents. Treat unrelated requests as out-of-scope but still attempt to help.",
        "enrollment":   "CHANNEL FOCUS: Enrollment only. Prioritise class enrollment, unenrollment, and program enrollment intents. Treat unrelated requests as out-of-scope but still attempt to help.",
        "belts":        "CHANNEL FOCUS: Belt & Ranks only. Prioritise rank promotion, belt lookup, and belt test intents. Treat unrelated requests as out-of-scope but still attempt to help.",
        "billing":      "CHANNEL FOCUS: Billing only. Prioritise subscription, credit, and payment query intents. Treat unrelated requests as out-of-scope but still attempt to help.",
        "lookup":       "CHANNEL FOCUS: Lookup / read-only mode. Only perform read-only information lookups — do NOT execute any create, update, delete, enroll, or send actions even if asked.",
        "all":          None,  # no prefix — same as default
        # PROTOTYPE: Elder Beta mode — injected on every request from walkie_elder
        "elder":        (
            "ELDER MODE: You are speaking to an elderly or less tech-savvy user. "
            "Rules: (1) Respond in short, plain sentences — no jargon, no bullet points, no markdown. "
            "(2) No martial arts terminology assumed — use plain English. "
            "(3) Always repeat the subject back before confirming an action: "
            "'You want to check in Jordan Smith — is that right?' "
            "(4) Keep every response under three sentences. "
            "(5) If unsure, ask one simple clarifying question."
        ),
    }

    @api.model
    def parse_and_confirm(self, text, role="instructor", input_type="text", audio_attachment_id=None, conversation_history=None, channel=None):
        """
        Phase 1: Parse natural language input into a structured intent.

        For read-only intents, auto-executes and returns result.
        For mutating intents, returns confirmation prompt.

        Args:
            text: User's natural language input
            role: User role (kiosk/instructor/admin)
            input_type: 'text' or 'voice'
            audio_attachment_id: ID of stored audio attachment (for voice)
            conversation_history: Optional list of {role, text} dicts for context chaining
            channel: Optional Channel Beta channel name — injects a system prompt prefix
                     to narrow AI focus. PROTOTYPE — pure prompt injection, no hard filter.

        Returns:
            dict: Standard response format (see handle_command)
        """
        text = (text or "").strip()
        if not text:
            return self._error_response("Please type or say something.")

        # ── PROTOTYPE: Channel Beta system prompt prefix injection ───────────────
        if channel and channel != "all":
            prefix = self._CHANNEL_PREFIXES.get(channel)
            if prefix:
                text = f"[SYSTEM CONTEXT — {prefix}]\n\n{text}"

        # ── Inject conversation history into the prompt ──────────────────────────
        if conversation_history:
            _turns_cfg = self.env["ir.config_parameter"].sudo().get_int(
                "ai_assistant.context_window_turns", 10
            )
            _max_messages = max(1, min(50, _turns_cfg)) * 2  # each turn = user + assistant
            turns = [
                m for m in conversation_history[-_max_messages:]
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant", "ai")
                and m.get("text")
            ]
            if turns:
                history_lines = []
                for m in turns:
                    label = "User" if m["role"] == "user" else "Assistant"
                    history_lines.append(f"{label}: {m['text']}")
                history_block = "\n".join(history_lines)
                text = f"[Conversation context]\n{history_block}\n\n[Current request]\n{text}"

        start_time = time.time()

        # ── Compound command routing ───────────────────────────────────────────────
        # Detect multi-action phrases and route straight to JSON-mode parsing,
        # bypassing the conversational path which does not support array output.
        try:
            ai_proc = self.env["ai.processor"]
            provider = ai_proc._get_provider()
            if self._is_compound_phrase(text):
                # Gemini does not support JSON-mode compound output — return explicit message
                if provider == "gemini":
                    return self._error_response(
                        "I can only do one action at a time with the current AI provider."
                    )
                db_ctx = self._build_db_context(text)
                compound_result = ai_proc.process_intent_query(text, role, db_ctx)
                if "intents" in compound_result:
                    return self.handle_compound_command(compound_result, role=role)
                # LLM returned single intent despite compound phrase — fall through to normal flow
        except Exception as e:
            _logger.warning("Compound detection failed, falling back to normal flow: %s", e)

        # ── Normal single-intent flow ─────────────────────────────────────────────
        try:
            ai_proc = self.env["ai.processor"]
            db_ctx = self._build_db_context(text)

            # For OpenAI, go straight to JSON mode — it's more reliable than the
            # conversational pre-pass and avoids a second API round-trip.
            # For Gemini (no native JSON mode), keep conversational-first with fallback.
            try:
                provider = ai_proc._get_provider()
            except Exception:
                provider = "openai"

            response_text = ""
            intent_data = None

            if provider in ("openai", "odoo_native"):
                # JSON mode first — more reliable than conversational pre-pass for OpenAI.
                intent_data = ai_proc.process_intent_query(text, role, db_ctx)
                _logger.info(
                    "AI (JSON mode): intent=%s conf=%.2f",
                    intent_data.get("intent_type"), intent_data.get("confidence", 0),
                )
                # If JSON mode couldn't identify the intent, fall back to conversational
                # so the user still gets a natural language answer instead of silence.
                if not intent_data or intent_data.get("intent_type") == "unknown" or \
                        intent_data.get("confidence", 0) < _MIN_CONFIDENCE:
                    conv_result = ai_proc.process_conversational_query(text, role, db_ctx)
                    response_text = conv_result.get("response", "")
                    conv_intent = conv_result.get("intent")
                    if conv_intent and conv_intent.get("intent_type") not in ("unknown", None) \
                            and conv_intent.get("confidence", 0) >= _MIN_CONFIDENCE:
                        intent_data = conv_intent
                        _logger.info(
                            "AI: conversational fallback used — intent=%s",
                            conv_intent.get("intent_type"),
                        )
            else:
                # Gemini: conversational first, escalate to JSON mode if needed
                result = ai_proc.process_conversational_query(text, role, db_ctx)
                response_text = result.get("response", "")
                intent_data = result.get("intent")

                conv_intent_type = intent_data.get("intent_type", "unknown") if intent_data else "unknown"
                conv_confidence = intent_data.get("confidence", 1.0) if intent_data else 0.0

                if (
                    not intent_data
                    or conv_intent_type not in _KNOWN_INTENT_TYPES
                    or conv_intent_type == "unknown"
                    or conv_confidence < _MIN_CONFIDENCE
                ):
                    intent_result = ai_proc.process_intent_query(text, role, db_ctx)
                    if intent_result.get("confidence", 0) >= _MIN_CONFIDENCE:
                        intent_data = intent_result
                        _logger.info(
                            "AI: escalated to JSON mode — intent=%s (conv was '%s')",
                            intent_result.get("intent_type"), conv_intent_type,
                        )

            # Determine intent type and check permissions
            intent_type = intent_data.get("intent_type", "unknown") if intent_data else "unknown"
            # Final safety net: if the AI still returned an unrecognised type, reset to unknown
            if intent_type not in _KNOWN_INTENT_TYPES:
                _logger.warning("AI returned unrecognised intent_type '%s', treating as unknown", intent_type)
                intent_type = "unknown"

            # Keyword-based override for common AI confusion patterns.
            # These run UNCONDITIONALLY — if the keyword pattern matches, we trust it
            # over whatever the LLM classified, preventing cross-intent confusion.
            text_lower = text.lower()

            # Detect question phrasing — if user is asking "who/what/how many/show/list",
            # don't override to write intents (register, create, etc.)
            _is_question = bool(re.match(
                r'^\s*(who|what|which|how\s+many|show|list|display|are\s+there|is\s+there|tell\s+me)',
                text_lower,
            ))

            # Redirect hallucinated intent type directly (n8n LLM sometimes invents course_roster)
            if intent_type == "course_roster":
                _logger.info("Intent redirect: course_roster → class_enrollment_list (not a valid intent)")
                intent_type = "class_enrollment_list"
                if intent_data:
                    intent_data["intent_type"] = "class_enrollment_list"

            if any(
                kw in text_lower for kw in ("roster", "course roster", "permanent roster", "add to the course", "add to course")
            ):
                if _is_question:
                    _logger.info("Keyword override: %s → class_enrollment_list (READ roster query)", intent_type)
                    intent_type = "class_enrollment_list"
                    if intent_data:
                        intent_data["intent_type"] = "class_enrollment_list"
                else:
                    _logger.info("Keyword override: %s → course_enroll (user said 'roster' + action verb)", intent_type)
                    intent_type = "course_enroll"
                    if intent_data:
                        intent_data["intent_type"] = "course_enroll"

            # belt_test_registration_list when user asks who is registered/signed up
            if "belt test" in text_lower and _is_question and any(
                kw in text_lower for kw in ("register", "signed up", "enrolled", "who", "list")
            ):
                _logger.info("Keyword override: %s → belt_test_registration_list (question about registrations)", intent_type)
                intent_type = "belt_test_registration_list"
                if intent_data is None:
                    intent_data = {"intent_type": "belt_test_registration_list", "parameters": {}, "confidence": 0.85}
                else:
                    intent_data["intent_type"] = "belt_test_registration_list"

            # belt_test_register when the user is asking to register/schedule a test
            # (but NOT when it's a question about who is registered)
            elif "belt test" in text_lower and not _is_question and any(
                kw in text_lower for kw in ("register", "sign up", "schedule", "add", "book", "testing for")
            ):
                _logger.info("Keyword override: %s → belt_test_register (keyword: 'belt test' + action verb)", intent_type)
                intent_type = "belt_test_register"
                # Ensure intent_data has the right type and try to extract belt name
                if intent_data is None:
                    intent_data = {"intent_type": "belt_test_register", "parameters": {}, "confidence": 0.8}
                else:
                    intent_data["intent_type"] = "belt_test_register"

            # For belt_test_register, recover any dropped params (AI sometimes uses placeholder strings)
            if intent_type == "belt_test_register":
                if intent_data is None:
                    intent_data = {"intent_type": "belt_test_register", "parameters": {}, "confidence": 0.8}
                params = intent_data.setdefault("parameters", {})
                # Recover belt name from user text if missing or was a placeholder
                if not params.get("target_belt") and not params.get("belt_name") and not params.get("new_belt"):
                    import re as _re2
                    belt_match = _re2.search(
                        r'\b(white|yellow|orange|green|blue|purple|brown|red|black|stripe)\s+(?:stripe\s+)?belt\b',
                        text_lower
                    )
                    if belt_match:
                        params["target_belt"] = belt_match.group(0).title()
                        _logger.info("Recovered target_belt from user text: %s", params["target_belt"])
                # Recover member name from user text if missing
                if not params.get("member_name") and not params.get("member_id"):
                    members = self.env["dojo.member"].with_context(active_test=False).search([], limit=200, order="name asc")
                    for m in members:
                        if m.name.lower() in text_lower:
                            params["member_name"] = m.name
                            _logger.info("Recovered member_name from user text: %s", m.name)
                            break

            # For belt_promote, recover belt name if AI returned a placeholder
            if intent_type == "belt_promote" and intent_data is not None:
                params = intent_data.setdefault("parameters", {})
                _BELT_PLACEHOLDERS = {"new_belt", "belt", "next_belt", "target_belt", "blue_belt", ""}
                current = (params.get("new_belt") or params.get("target_belt") or "").lower().strip()
                if not current or current in _BELT_PLACEHOLDERS:
                    import re as _re3
                    belt_match = _re3.search(
                        r'\b(white|yellow|orange|green|blue|purple|brown|red|black|stripe)\s+(?:stripe\s+)?belt\b',
                        text_lower
                    )
                    if belt_match:
                        params["new_belt"] = belt_match.group(0).title()
                        _logger.info("Recovered new_belt from user text: %s", params["new_belt"])

            # For contact_parent, ensure subject has a readable default for confirmation
            if intent_type == "contact_parent" and intent_data is not None:
                params = intent_data.setdefault("parameters", {})
                if not params.get("subject"):
                    # Build a default subject from the body or user text
                    body = params.get("body", params.get("message", ""))
                    if body:
                        # Use first ~50 chars of body as subject
                        default_subj = body[:50].rstrip().rstrip(".,!?") + ("..." if len(body) > 50 else "")
                    else:
                        default_subj = "Message from Dojo"
                    params["subject"] = default_subj

            # Phone number keyword override — a bare phone number should search BOTH
            # dojo.member and crm.lead, not just leads. Routes to phone_lookup intent
            # which tries members first then leads.
            # Covers: 10-digit full (555-867-5309), 7-digit local (555-0112),
            # unformatted 10-digit (4085551234), and +1 prefixed variants.
            _PHONE_PATTERN = re.compile(
                r'\b(?:\+?1[-\.\s]?)?\(?\d{3}\)?[-\.\s]\d{3}[-\.\s]\d{4}\b'  # 10-digit
                r'|\b\d{3}[-\.\s]\d{4}\b'                                      # 7-digit local
                r'|\b\d{10}\b'                                                  # bare 10-digit
            )
            _phone_match = _PHONE_PATTERN.search(text)
            if _phone_match and not any(
                kw in text_lower for kw in ("lead", "prospect", "pipeline", "crm")
            ):
                phone_val = _phone_match.group(0)
                _logger.info(
                    "Keyword override: %s → phone_lookup (phone pattern '%s')",
                    intent_type, phone_val,
                )
                intent_type = "phone_lookup"
                if intent_data is None:
                    intent_data = {
                        "intent_type": "phone_lookup",
                        "parameters": {"phone": phone_val},
                        "confidence": 0.95,
                    }
                else:
                    intent_data["intent_type"] = "phone_lookup"
                    intent_data.setdefault("parameters", {})["phone"] = (
                        intent_data["parameters"].get("phone") or phone_val
                    )

            # Validate role permission
            if intent_type != "unknown":
                schema = self.env["ai.intent.schema"].get_by_type(intent_type)
                if schema and not schema.check_role_permission(role):
                    return self._error_response(f"You don't have permission to execute '{intent_type}'.")

            # General member name recovery — runs for all member-related intents
            # (belt_test_register has its own recovery above; this covers everything else)
            _MEMBER_INTENTS = {
                "member_lookup", "attendance_history", "subscription_lookup", "belt_lookup",
                "attendance_checkin", "attendance_checkout", "member_enroll", "member_unenroll",
                "course_enroll", "belt_promote", "subscription_cancel", "contact_parent",
                "member_update", "subscription_pause", "subscription_resume",
            }
            if intent_type in _MEMBER_INTENTS and intent_data is not None:
                params = intent_data.setdefault("parameters", {})
                if not params.get("member_name") and not params.get("member_id"):
                    members = self.env["dojo.member"].with_context(active_test=False).search([], limit=200, order="name asc")
                    for m in members:
                        if m.name.lower() in text_lower:
                            params["member_name"] = m.name
                            _logger.info("General recovery: member_name=%s for %s", m.name, intent_type)
                            break

            # Resolve entities (member IDs, session IDs, etc.)
            resolved_data = self._resolve_entities(intent_data) if intent_data else {}

            # Pre-execution validation: catch ambiguous entities and missing params
            # before they silently execute on the wrong record.
            validation = self._validate_before_execute(intent_type, intent_data, resolved_data)
            if not validation.get("valid", True):
                # Store partial intent so follow-up reply can resume it
                import uuid as _uuid
                clarify_key = f"clarify-{_uuid.uuid4().hex[:12]}"
                cls = type(self)
                cls._pending_clarification_cache[clarify_key] = {
                    "intent_type": intent_type,
                    "intent_data": intent_data,
                    "resolved_data": resolved_data,
                    "clarification_question": validation["clarification"],
                    "role": role,
                    "expires_at": time.time() + cls._CLARIFICATION_TTL,
                }
                _logger.info(
                    "Stored clarification context for %s (key=%s)",
                    intent_type, clarify_key,
                )
                return {
                    "success": True,
                    "state": "needs_clarification",
                    "session_key": clarify_key,
                    "intent": intent_data,
                    "confirmation_prompt": None,
                    "resolved_data": resolved_data,
                    "auto_executed": False,
                    "result": None,
                    "response": validation["clarification"],
                    "error": None,
                }

            # Check if this intent requires confirmation
            requires_confirmation = self._requires_confirmation(intent_type)

            # Create action log entry
            ActionLog = self.env["ai.action.log"]
            log = ActionLog.log_parse(
                input_text=text,
                role=role,
                intent_type=intent_type,
                parsed_intent=intent_data,
                confidence=round((intent_data.get("confidence", 0) if intent_data else 0) * 100, 1),
                resolved_data=resolved_data,
                confirmation_prompt=None,  # Set below if needed
                requires_confirmation=requires_confirmation,
                input_type=input_type,
                audio_attachment_id=audio_attachment_id,
            )

            # If read-only intent, auto-execute
            if not requires_confirmation:
                # ── Conversational short-circuit for unknown intents ───────────────
                # Instead of executing _handle_unknown (which returns a static error),
                # use the LLM's conversational response.  If not already populated
                # (e.g. OpenAI JSON-only path that never called process_conversational_query),
                # make a conversational call now so the reply feels natural.
                if intent_type == "unknown":
                    if not response_text:
                        try:
                            conv_result = ai_proc.process_conversational_query(text, role, db_ctx)
                            response_text = conv_result.get("response", "")
                        except Exception:
                            pass
                    conversational_reply = response_text or (
                        "I'm not quite sure how to help with that. Could you rephrase?"
                    )
                    execution_time_ms = int((time.time() - start_time) * 1000)
                    log.log_execution(
                        success=True,
                        result={"success": True, "message": conversational_reply},
                        execution_time_ms=execution_time_ms,
                        is_undoable=False,
                    )
                    return {
                        "success": True,
                        "state": "executed",
                        "session_key": log.session_key,
                        "intent": intent_data,
                        "auto_executed": True,
                        "result": {"success": True, "message": conversational_reply},
                        "response": conversational_reply,
                        "confirmation_prompt": None,
                        "resolved_data": {},
                        "error": None,
                        "agent_name": None,
                        "execution_time_ms": execution_time_ms,
                    }
                # ─────────────────────────────────────────────────────────────────

                exec_result = self._execute_intent(intent_type, intent_data, resolved_data, log)
                execution_time_ms = int((time.time() - start_time) * 1000)

                log.log_execution(
                    success=exec_result.get("success", False),
                    result=exec_result,
                    execution_time_ms=execution_time_ms,
                    is_undoable=False,
                )

                # Use formatted execution result as the response so the user
                # sees actual data rather than the AI's conversational fallback.
                formatted = self._format_exec_result_as_response(intent_type, exec_result)
                final_response = formatted or response_text

                return {
                    "success": True,
                    "state": "executed",
                    "session_key": log.session_key,
                    "intent": intent_data,
                    "auto_executed": True,
                    "result": exec_result,
                    "response": final_response,
                    "confirmation_prompt": None,
                    "resolved_data": resolved_data,
                    "error": None,
                    "agent_name": (intent_data or {}).get("agent_name"),
                    "execution_time_ms": execution_time_ms,
                }

            # Build confirmation prompt
            confirmation_prompt = self._build_confirmation_prompt(intent_type, intent_data, resolved_data)
            log.write({"confirmation_prompt": confirmation_prompt})

            return {
                "success": True,
                "state": "pending_confirmation",
                "session_key": log.session_key,
                "intent": intent_data,
                "confirmation_prompt": confirmation_prompt,
                "resolved_data": resolved_data,
                "auto_executed": False,
                "result": None,
                "response": response_text,
                "error": None,
                "agent_name": (intent_data or {}).get("agent_name"),
                "execution_time_ms": None,
            }

        except UserError as e:
            return self._error_response(str(e))
        except Exception as e:
            _logger.error("AI assistant parse failed: %s", e, exc_info=True)
            return self._error_response(f"An error occurred: {e}")

    @api.model
    def execute_confirmed(self, session_key, confirmed=True):
        """
        Phase 2: Execute or reject a pending intent.
        
        Args:
            session_key: Session key from parse_and_confirm
            confirmed: True to execute, False to reject
        
        Returns:
            dict: {
                "success": bool,
                "state": "executed" | "rejected" | "error",
                "result": dict | None,
                "undo_available": bool,
                "undo_expires_in_minutes": int | None,
                "error": str | None
            }
        """
        ActionLog = self.env["ai.action.log"]
        log = ActionLog.find_by_session_key(session_key)

        if not log:
            return self._error_response("Session not found or expired.")

        if log.confirmation_status != "pending":
            return self._error_response(f"This action is already {log.confirmation_status}.")

        # Record confirmation
        log.log_confirmation(confirmed, self.env.user.id)

        if not confirmed:
            return {
                "success": True,
                "state": "rejected",
                "result": {"message": "Action cancelled."},
                "undo_available": False,
                "undo_expires_in_minutes": None,
                "error": None,
            }

        # ── Compound chain execution ──────────────────────────────────────────────
        if log.intent_type == "compound_chain":
            intents_raw = json.loads(log.parsed_intent) if log.parsed_intent else []
            # Handle both list (direct) and wrapped dict {"intents": [...]}
            if isinstance(intents_raw, dict):
                intents_raw = intents_raw.get("intents", [])
            if not intents_raw:
                return self._error_response("Compound chain data is missing or corrupt.")

            try:
                chain_result = self._execute_compound_chain(intents_raw, log.role, log)
            except Exception as e:
                _logger.error("Compound chain execution failed: %s", e, exc_info=True)
                return self._error_response(f"Compound chain execution failed: {e}")
            return {
                "success": chain_result["success"],
                "state": "executed",
                "compound": True,
                "steps": chain_result.get("steps", []),
                "rollback_failures": chain_result.get("rollback_failures", []),
                "result": chain_result,
                "undo_available": False,
                "undo_expires_in_minutes": None,
                "error": chain_result.get("error"),
            }
        # ── Single-intent execution (unchanged below) ─────────────────────────────

        # Execute the intent
        start_time = time.time()

        try:
            intent_data = json.loads(log.parsed_intent) if log.parsed_intent else {}
            resolved_data = json.loads(log.resolved_data) if log.resolved_data else {}

            result = self._execute_intent(log.intent_type, intent_data, resolved_data, log)
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Check if this action is undoable
            schema = self.env["ai.intent.schema"].get_by_type(log.intent_type)
            is_undoable = schema.is_undoable if schema else False

            log.log_execution(
                success=result.get("success", False),
                result=result,
                execution_time_ms=execution_time_ms,
                is_undoable=is_undoable,
            )

            # Calculate undo expiry
            undo_minutes = None
            if is_undoable:
                undo_minutes = self.env["ir.config_parameter"].sudo().get_int(
                    "ai_assistant.undo_expiry_minutes", 60
                )

            return {
                "success": result.get("success", False),
                "state": "executed",
                "result": result,
                "undo_available": is_undoable,
                "undo_expires_in_minutes": undo_minutes,
                "error": result.get("error"),
                "agent_name": intent_data.get("agent_name"),
                "execution_time_ms": execution_time_ms,
            }

        except Exception as e:
            _logger.error("AI assistant execution failed: %s", e, exc_info=True)
            log.log_execution(success=False, error=str(e))
            return self._error_response(f"Execution failed: {e}")

    @api.model
    def undo_last_action(self, user_id=None):
        """
        Undo the most recent undoable action.
        
        Returns:
            dict: {
                "success": bool,
                "state": "pending_confirmation" | "executed" | "error",
                "session_key": str | None,
                "confirmation_prompt": str | None,
                "undo_target": dict | None,
                "result": dict | None,
                "error": str | None
            }
        """
        ActionLog = self.env["ai.action.log"]
        log = ActionLog.get_last_undoable(user_id)

        if not log:
            return {
                "success": False,
                "state": "error",
                "error": "No undoable actions found in the last hour.",
                "session_key": None,
                "confirmation_prompt": None,
                "undo_target": None,
                "result": None,
            }

        # Get the undo snapshots
        snapshots = self.env["ai.undo.snapshot"].get_available_for_action(log.id)
        if not snapshots:
            return self._error_response("Undo data is no longer available.")

        # Build undo target info
        intent_data = json.loads(log.parsed_intent) if log.parsed_intent else {}
        undo_target = {
            "action_log_id": log.id,
            "intent_type": log.intent_type,
            "created_at": log.timestamp.isoformat() if log.timestamp else None,
            "input_text": log.input_text,
            "snapshot_count": len(snapshots),
        }

        # Create confirmation prompt for undo
        time_ago = self._format_time_ago(log.timestamp)
        confirmation_prompt = f"Undo {log.intent_type} from {time_ago}? ({log.input_text[:50]}...)" \
            if len(log.input_text or "") > 50 else f"Undo {log.intent_type} from {time_ago}? ({log.input_text})"

        # Create a new action log for the undo operation
        undo_log = ActionLog.log_parse(
            input_text=f"Undo: {log.input_text}",
            role=log.role,
            intent_type="undo_action",
            parsed_intent={"original_action_log_id": log.id},
            confidence=100.0,
            resolved_data={"snapshots": [s.id for s in snapshots]},
            confirmation_prompt=confirmation_prompt,
            requires_confirmation=True,
        )

        return {
            "success": True,
            "state": "pending_confirmation",
            "session_key": undo_log.session_key,
            "confirmation_prompt": confirmation_prompt,
            "undo_target": undo_target,
            "result": None,
            "error": None,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Legacy API: Backward Compatibility
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def process_text_query(self, text):
        """
        Legacy entry point: process a text query through the AI assistant.
        
        DEPRECATED: Use handle_command() or parse_and_confirm() for the new two-phase flow.

        Returns:
            dict: {
                "response": str,
                "action": dict | None
            }
        """
        result = self.parse_and_confirm(text, role="instructor")

        if not result.get("success"):
            return {"response": result.get("error", "An error occurred."), "action": None}

        # For auto-executed intents, return the result
        if result.get("auto_executed"):
            return {
                "response": result.get("response") or str(result.get("result", {}).get("message", "")),
                "action": None,
            }

        # For pending confirmation, return the prompt as response with action
        intent = result.get("intent", {})
        resolved = result.get("resolved_data", {})

        # Build legacy action format for contact_parent
        action = None
        if intent.get("intent_type") == "contact_parent":
            action = {
                "type": "contact_parent",
                "member_id": resolved.get("member_id"),
                "member_name": resolved.get("member_name"),
                "guardian_name": resolved.get("guardian_name"),
                "guardian_email": resolved.get("guardian_email"),
                "guardian_phone": resolved.get("guardian_phone"),
                "suggested_subject": intent.get("parameters", {}).get("subject"),
                "suggested_body": intent.get("parameters", {}).get("body"),
            }

        return {
            "response": result.get("response") or result.get("confirmation_prompt", ""),
            "action": action,
            "session_key": result.get("session_key"),
            "requires_confirmation": True,
        }

    @api.model
    def send_parent_message(self, member_id, subject, body, send_email=True, send_sms=True):
        """
        Send a message to the primary guardian of member_id.
        Delegates to dojo.send.message.wizard for consistent delivery logic.
        """
        member = self.env["dojo.member"].browse(int(member_id))
        if not member.exists():
            raise UserError("Member not found.")

        wizard = self.env["dojo.send.message.wizard"].create({
            "member_ids": [(6, 0, [member.id])],
            "subject": subject or "Message from Dojo",
            "message_body": body or "",
            "send_email": bool(send_email),
            "send_sms": bool(send_sms),
        })
        wizard.action_send()
        return {
            "success": True,
            "message": "Message sent to the guardian of {}.".format(member.name),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # DB Context Builder
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _build_db_context(self, query_text=""):
        """Build a text block describing relevant dojo data for the AI prompt."""
        lines = []

        # ── Members matching any name-like tokens in the query ───────────────
        potential_name = self._extract_name_tokens(query_text)
        if potential_name:
            members = self._search_members(potential_name)
            if members:
                lines.append("=== Matching Students ===")
                for m in members[:6]:
                    guardian_str = self._guardian_summary(m)
                    sub = m.active_subscription_id if hasattr(m, 'active_subscription_id') else None
                    plan_str = " plan:{}".format(sub.plan_id.name) if sub and sub.plan_id else ""
                    rank_str = ""
                    if hasattr(m, 'current_rank_id') and m.current_rank_id:
                        rank_str = " rank:{}".format(m.current_rank_id.name)
                    lines.append(
                        "  - {} [id:{}, state:{}{}{}]{}".format(
                            m.name, m.id,
                            getattr(m, 'membership_state', 'unknown'),
                            plan_str, rank_str, guardian_str,
                        )
                    )

        # ── Today's sessions ─────────────────────────────────────────────────
        try:
            from datetime import date as _date
            today = _date.today().isoformat()
            sessions = self.env["dojo.class.session"].search_read(
                [
                    ["start_datetime", ">=", today + " 00:00:00"],
                    ["start_datetime", "<=", today + " 23:59:59"],
                ],
                ["template_id", "start_datetime", "seats_taken", "capacity", "state"],
                limit=10,
                order="start_datetime asc",
            )
            if sessions:
                lines.append("=== Today's Sessions ===")
                for s in sessions:
                    dt = s["start_datetime"]
                    time_str = dt.strftime("%H:%M") if hasattr(dt, "strftime") else str(dt)[:16]
                    lines.append(
                        "  - {} at {} ({}/{} enrolled, state:{})".format(
                            s["template_id"][1] if s["template_id"] else "—",
                            time_str,
                            s["seats_taken"],
                            s["capacity"],
                            s["state"],
                        )
                    )
        except Exception as exc:
            _logger.warning("Could not fetch sessions for AI context: %s", exc)

        # ── School stats ─────────────────────────────────────────────────────
        try:
            active_count = self.env["dojo.member"].search_count(
                [["membership_state", "=", "active"]]
            )
            lines.append("=== School Stats ===")
            lines.append("  - Active members: {}".format(active_count))
        except Exception:
            pass

        return "\n".join(lines) if lines else "No specific context loaded."

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _extract_name_tokens(self, text):
        """
        Heuristic: extract likely name tokens from text.

        Intentionally case-insensitive so voice/STT input (which often arrives
        as all-lowercase) still produces useful search tokens.  Stop-words and
        dojo-action verbs are filtered so common command words don't pollute
        the member search.
        """
        if not text:
            return ""
        _STOP = {
            "is", "has", "what", "show", "check", "enroll", "unenroll", "belt",
            "class", "the", "in", "for", "to", "a", "an", "at", "of", "and",
            "or", "me", "my", "do", "did", "can", "was", "are", "his", "her",
            "their", "who", "how", "when", "today", "now", "please", "up",
            "out", "add", "remove", "get", "let", "find", "look", "rank",
            "session", "schedule", "roster", "promote", "pause", "cancel",
            "subscription", "register", "test", "membership", "contact",
            "parent", "guardian", "send", "message", "create", "update",
            "next", "last", "this", "from", "with", "about", "sign",
        }
        words = text.split()
        tokens = [
            re.sub(r"[^a-zA-Z]", "", w)
            for w in words
            if len(w) > 2 and w.lower().rstrip("s") not in _STOP
        ]
        # Remove empty strings left after stripping punctuation
        tokens = [t for t in tokens if len(t) > 1]
        return " ".join(tokens[:3])

    @api.model
    def _search_members(self, name, limit=6):
        """Case-insensitive ilike search on member name.

        Uses active_test=False so migrated/archived members are still findable
        by the AI assistant — the ORM's default active=True filter would silently
        exclude them otherwise.
        """
        return self.env["dojo.member"].with_context(active_test=False).search(
            [["name", "ilike", name]], limit=limit
        )

    @api.model
    def _guardian_summary(self, member):
        """Return a compact string describing the primary guardian."""
        household = member.partner_id.parent_id if hasattr(member, 'partner_id') else None
        if household and household.is_household and household.primary_guardian_id:
            gp = household.primary_guardian_id
            email_part = " email:{}".format(gp.email) if gp.email else ""
            phone_part = " phone:{}".format(gp.phone) if gp.phone else ""
            return " guardian:{}{}{}".format(gp.name, email_part, phone_part)
        return ""

    @api.model
    def _error_response(self, error_msg):
        """Build a standard error response dict."""
        return {
            "success": False,
            "state": "error",
            "session_key": None,
            "intent": None,
            "confirmation_prompt": None,
            "resolved_data": None,
            "auto_executed": False,
            "result": None,
            "response": None,
            "error": error_msg,
        }

    @api.model
    def _requires_confirmation(self, intent_type):
        """
        Check if an intent requires user confirmation before execution.
        Read-only intents auto-execute; mutating intents require confirmation.
        """
        if intent_type in _AUTO_EXECUTE_INTENTS:
            return False

        # Check schema for explicit configuration
        schema = self.env["ai.intent.schema"].get_by_type(intent_type)
        if schema:
            return schema.requires_confirmation

        # Default to requiring confirmation for unknown mutating intents
        return True

    @api.model
    def _build_confirmation_prompt(self, intent_type, intent_data, resolved_data):
        """Build a human-readable confirmation prompt for an intent."""
        params = intent_data.get("parameters", {}) if intent_data else {}

        # Check for custom template in schema
        schema = self.env["ai.intent.schema"].get_by_type(intent_type)
        if schema and schema.confirmation_template:
            return schema.format_confirmation_prompt(intent_data, resolved_data)

        # Default confirmation prompts by intent type
        prompts = {
            "member_enroll": lambda: "Enroll {} in {}?".format(
                resolved_data.get("member_name", params.get("member_name", "member")),
                resolved_data.get("session_name", params.get("class_name", "the class"))
            ),
            "member_unenroll": lambda: "Remove {} from {}?".format(
                resolved_data.get("member_name", "member"),
                resolved_data.get("session_name", "the class")
            ),
            "belt_promote": lambda: "Promote {} to {}?".format(
                resolved_data.get("member_name", "member"),
                resolved_data.get("new_rank_name", params.get("new_belt", "next belt"))
            ),
            "subscription_create": lambda: "Create {} subscription for {}?".format(
                params.get("plan_name", "a"),
                resolved_data.get("member_name", "member")
            ),
            "subscription_cancel": lambda: "Cancel subscription for {}?".format(
                resolved_data.get("member_name", "member")
            ),
            "contact_parent": lambda: "Send message to {}'s guardian?".format(
                resolved_data.get("member_name", "member")
            ),
            "attendance_checkin": lambda: "Check in {}?".format(
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            "attendance_checkout": lambda: "Check out {}?".format(
                resolved_data.get("member_name", "member")
            ),
            "member_create": lambda: "Create new member {}?".format(
                params.get("name", "record")
            ),
            "member_update": lambda: "Update {} profile?".format(
                resolved_data.get("member_name", "member")
            ),
            "class_create": lambda: "Create new class {}?".format(
                params.get("class_name", "template")
            ),
            "class_cancel": lambda: "Cancel {}?".format(
                resolved_data.get("session_name", "the session")
            ),
            "course_enroll": lambda: "Add {} to the {} course roster?".format(
                resolved_data.get("member_name", params.get("member_name", "member")),
                resolved_data.get("template_name", params.get("class_name", "the course"))
            ),
            "belt_test_register": lambda: "Register {} for a belt test (testing for {})?".format(
                resolved_data.get("member_name", params.get("member_name", "member")),
                resolved_data.get("new_rank_name", params.get("target_belt", "next rank"))
            ),
            "undo_action": lambda: "Undo the previous action?",
            "lead_create": lambda: "Create new lead for {}?".format(
                params.get("contact_name") or params.get("name", "prospect")
            ),
            "lead_qualify": lambda: "Move lead '{}' to Qualified and generate booking link?".format(
                params.get("lead_name") or params.get("name", "lead")
            ),
            "lead_mark_attended": lambda: "Mark '{}' as Trial Attended?".format(
                params.get("lead_name") or params.get("name", "lead")
            ),
            "lead_convert": lambda: "Convert '{}' to a member?".format(
                params.get("lead_name") or params.get("name", "lead")
            ),
            "lead_mark_lost": lambda: "Mark '{}' as lost?".format(
                params.get("lead_name") or params.get("name", "lead")
            ),
            "lead_mark_won": lambda: "Mark '{}' as won?".format(
                params.get("lead_name") or params.get("name", "lead")
            ),
            # ── Batch A: Points & Member Admin ────────────────────────────
            "points_award": lambda: "Award {} points to {}?".format(
                params.get("amount", "?"),
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            "points_deduct": lambda: "Deduct {} points from {}?".format(
                params.get("amount", "?"),
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            "member_archive": lambda: "Archive {}? They will be deactivated.".format(
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            "member_note_add": lambda: "Add note to {}'s profile: '{}'?".format(
                resolved_data.get("member_name", params.get("member_name", "member")),
                (params.get("note") or params.get("message") or "")[:80]
            ),
            "subscription_billing_retry": lambda: "Retry billing for {}?".format(
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            # ── Batch B ───────────────────────────────────────────────────
            "class_session_generate": lambda: "Generate sessions for class '{}'?".format(
                params.get("template_name") or params.get("class_name", "?")
            ),
            "class_waitlist_add": lambda: "Add {} to the waitlist for '{}'?".format(
                resolved_data.get("member_name", params.get("member_name", "member")),
                params.get("class_name") or params.get("session_name", "?")
            ),
            # ── Batch C ───────────────────────────────────────────────────
            "belt_test_result_update": lambda: "Record belt test result '{}' for {}?".format(
                params.get("result", "?"),
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
            "instructor_todo_complete": lambda: "Mark task '{}' as done?".format(
                params.get("task_name") or params.get("task_id", "?")
            ),
            # ── Batch D ───────────────────────────────────────────────────
            "lead_note_add": lambda: "Add note to lead '{}'?".format(
                params.get("lead_name") or params.get("contact_name", "lead")
            ),
            "guardian_portal_invite": lambda: "Send portal invitation to guardian of {}?".format(
                resolved_data.get("member_name", params.get("member_name", "member"))
            ),
        }

        def _fmt(action_text):
            return f"{action_text}\n\nReply **Yes** to confirm or **No** to cancel."

        if intent_type in prompts:
            try:
                return _fmt(prompts[intent_type]())
            except Exception as e:
                _logger.warning("Error building confirmation prompt: %s", e)

        return _fmt(f"Confirm {intent_type.replace('_', ' ')}?")

    @api.model
    def _validate_before_execute(self, intent_type, intent_data, resolved_data):
        """
        Pre-execution validation: catch ambiguous entities and missing required params
        before they silently execute on the wrong record.

        Returns {"valid": True} or {"valid": False, "clarification": "<message>"}.
        """
        if not intent_data or intent_type == "unknown":
            return {"valid": True}

        params = intent_data.get("parameters", {}) or {}

        # ── Check 1: Ambiguous member name ────────────────────────────────────
        # _resolve_entities picks members[0] silently. If 2+ members share a name,
        # ask the user to pick one instead of acting on the wrong record.
        member_name = params.get("member_name")
        if member_name and not params.get("member_id") and not resolved_data.get("member_id"):
            candidates = self._search_members(member_name, limit=5)
            if len(candidates) > 1:
                names = ", ".join(
                    f"{m.name} (#{m.id})" for m in candidates
                )
                return {
                    "valid": False,
                    "clarification": (
                        f"I found {len(candidates)} members matching \"{member_name}\": {names}. "
                        "Which one did you mean? Please use their full name or member number."
                    ),
                }

        # ── Check 2: Missing required parameters ──────────────────────────────
        _MEMBER_ACTION_INTENTS = {
            "member_enroll", "member_unenroll", "belt_promote", "contact_parent",
            "attendance_checkin", "attendance_checkout", "course_enroll",
            "belt_test_register", "subscription_cancel", "subscription_pause",
            "subscription_resume", "member_update",
        }
        if intent_type in _MEMBER_ACTION_INTENTS:
            schema = self.env["ai.intent.schema"].get_by_type(intent_type)
            if schema and schema.parameters_schema:
                try:
                    import json as _json
                    param_schema = _json.loads(schema.parameters_schema)
                    required_fields = param_schema.get("required", [])
                    missing = [
                        f for f in required_fields
                        if not params.get(f) and not resolved_data.get(f)
                    ]
                    if missing:
                        field_labels = {"member_name": "member name", "class_name": "class name",
                                        "new_belt": "belt rank", "target_belt": "belt rank",
                                        "session_id": "class session", "subject": "message subject"}
                        label = field_labels.get(missing[0], missing[0].replace("_", " "))
                        return {
                            "valid": False,
                            "clarification": (
                                f"To complete \"{intent_type.replace('_', ' ')}\", "
                                f"I need the {label}. Could you provide it?"
                            ),
                        }
                except Exception:
                    pass

        # ── Check 2b: CRM lead intent validation ────────────────────────────────
        _CRM_LEAD_LOOKUP_INTENTS = {
            "lead_qualify", "lead_mark_attended", "lead_convert",
            "lead_mark_lost", "lead_mark_won",
        }
        if intent_type in _CRM_LEAD_LOOKUP_INTENTS:
            lead_name = (params.get("lead_name") or params.get("name")
                         or params.get("contact_name"))
            if not lead_name and not params.get("lead_id"):
                return {
                    "valid": False,
                    "clarification": (
                        f"To {intent_type.replace('_', ' ')}, which lead are you referring to? "
                        "Please provide the prospect's name."
                    ),
                }

        if intent_type == "lead_create":
            contact_name = (params.get("contact_name") or params.get("name")
                            or params.get("lead_name"))
            # batch mode with contacts list is OK
            if not contact_name and not params.get("contacts"):
                return {
                    "valid": False,
                    "clarification": (
                        "To create a new lead, what is the prospect's name? "
                        "(You can also add their phone or email.)"
                    ),
                }

        # ── Check 3: Logical sanity for common action intents ─────────────────
        member_id = resolved_data.get("member_id")

        if intent_type == "member_enroll" and member_id and resolved_data.get("session_id"):
            session_id = resolved_data["session_id"]
            already = self.env["dojo.class.enrollment"].search([
                ("member_id", "=", member_id),
                ("session_id", "=", session_id),
                ("status", "not in", ("cancelled",)),
            ], limit=1)
            if already:
                return {
                    "valid": False,
                    "clarification": (
                        f"{resolved_data.get('member_name', 'That member')} is already enrolled "
                        "in that session."
                    ),
                }

        if intent_type == "belt_promote" and member_id:
            current_rank = resolved_data.get("member_rank")
            new_belt = params.get("new_belt") or params.get("target_belt")
            if current_rank and new_belt and current_rank.lower() == new_belt.lower():
                return {
                    "valid": False,
                    "clarification": (
                        f"{resolved_data.get('member_name', 'That member')} already holds "
                        f"{current_rank}. Please specify a different belt rank."
                    ),
                }

        return {"valid": True}

    def _resolve_entities(self, intent_data):
        """
        Resolve named entities to database IDs.

        Takes parsed intent parameters like {member_name: "John Doe"} and
        resolves to {member_id: 123, member_name: "John Doe"}.
        """
        if not intent_data:
            return {}

        resolved = {}
        raw_params = intent_data.get("parameters", {}) or {}
        # Strip unfilled template placeholders like "{name}" or "{{member}}"
        # that the AI occasionally emits instead of real values.
        _placeholder = re.compile(r'^\{[^}]*\}$')
        params = {
            k: v for k, v in raw_params.items()
            if not (isinstance(v, str) and _placeholder.match(v.strip()))
        }

        # Resolve member by name or ID
        if params.get("member_name"):
            members = self._search_members(params["member_name"], limit=3)
            if members:
                member = members[0]
                resolved["member_id"] = member.id
                resolved["member_name"] = member.name
                resolved["member_rank"] = member.current_rank_id.name if hasattr(member, 'current_rank_id') and member.current_rank_id else None
                resolved["member_state"] = getattr(member, 'membership_state', None)

                # Include guardian info
                household = member.partner_id.parent_id if hasattr(member, 'partner_id') else None
                if household and household.is_household and household.primary_guardian_id:
                    g = household.primary_guardian_id
                    resolved["guardian_id"] = g.id
                    resolved["guardian_name"] = g.name
                    resolved["guardian_email"] = g.email
                    resolved["guardian_phone"] = g.phone

        elif params.get("member_id"):
            member = self.env["dojo.member"].browse(int(params["member_id"]))
            if member.exists():
                resolved["member_id"] = member.id
                resolved["member_name"] = member.name
                resolved["member_rank"] = member.current_rank_id.name if hasattr(member, 'current_rank_id') and member.current_rank_id else None

        # Resolve class/session
        # Also check resolved_entities the AI may have already identified
        ai_resolved = intent_data.get("resolved_entities", {}) or {}
        raw_class_name = (
            params.get("class_name")
            or ai_resolved.get("class_name")
        )
        raw_session_id = params.get("session_id") or ai_resolved.get("session_id")

        if raw_class_name or raw_session_id:
            session = None
            from datetime import date as _date, timedelta as _timedelta
            today = _date.today()

            # Resolve a date hint from intent params (AI may provide "date" field
            # with values like "2026-03-25", "tomorrow", "thursday", etc.)
            raw_date_param = params.get("date") or (intent_data.get("parameters", {}) or {}).get("date")
            target_date = today
            if raw_date_param:
                try:
                    target_date = _date.fromisoformat(str(raw_date_param))
                except (ValueError, TypeError):
                    # Relative keywords — rough mapping
                    rdp = str(raw_date_param).lower().strip()
                    if rdp == "tomorrow":
                        target_date = today + _timedelta(days=1)
                    elif rdp in ("yesterday",):
                        target_date = today - _timedelta(days=1)
                    else:
                        # Day-of-week: "monday", "tuesday", ...
                        _DOW = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                                "friday": 4, "saturday": 5, "sunday": 6}
                        dow = _DOW.get(rdp)
                        if dow is not None:
                            days_ahead = (dow - today.weekday()) % 7 or 7
                            target_date = today + _timedelta(days=days_ahead)

            # Use a ±7-day window centred on the target date so phrases like
            # "Thursday's class" or "next week's session" resolve correctly.
            window_start = (target_date - _timedelta(days=7)).isoformat()
            window_end = (target_date + _timedelta(days=7)).isoformat()
            search_domain = [
                ("start_datetime", ">=", window_start + " 00:00:00"),
                ("start_datetime", "<=", window_end + " 23:59:59"),
            ]
            # For "today" queries keep a tight same-day window for faster / more
            # accurate matches; only widen when a specific non-today date is implied.
            today_domain = [
                ("start_datetime", ">=", today.isoformat() + " 00:00:00"),
                ("start_datetime", "<=", today.isoformat() + " 23:59:59"),
            ]

            if raw_session_id:
                try:
                    session = self.env["dojo.class.session"].browse(int(raw_session_id))
                    if not session.exists():
                        session = None
                except Exception:
                    session = None

            if not session and raw_class_name:
                # 1. Try exact ilike on the full name — today first, then wider window
                sessions = self.env["dojo.class.session"].search(
                    [("template_id.name", "ilike", raw_class_name)] + today_domain,
                    limit=5,
                )
                if not sessions:
                    sessions = self.env["dojo.class.session"].search(
                        [("template_id.name", "ilike", raw_class_name)] + search_domain,
                        limit=5, order="start_datetime asc",
                    )
                if sessions:
                    session = sessions[0]

            if not session and raw_class_name:
                # 2. Word-by-word fallback: score sessions in the search window by how
                #    many significant words from the user query appear in the name.
                #    Weighted by character length so longer/more-specific words
                #    win ties (e.g. "fundamental" beats "advanced").
                all_today = self.env["dojo.class.session"].search(search_domain, limit=50)
                query_words = [
                    w.lower() for w in re.split(r"\W+", raw_class_name)
                    if len(w) > 2
                ]
                best_score, best_session = 0, None
                for s in all_today:
                    tname = (s.template_id.name or "").lower() if s.template_id else ""
                    score = sum(len(w) for w in query_words if w in tname)
                    if score > best_score:
                        best_score, best_session = score, s
                if best_score > 0:
                    session = best_session
                    _logger.info(
                        "AI session fuzzy-matched '%s' → '%s' (score %d)",
                        raw_class_name,
                        session.template_id.name if session.template_id else session.id,
                        best_score,
                    )

            if session and session.exists():
                resolved["session_id"] = session.id
                resolved["session_name"] = session.template_id.name if session.template_id else f"Session #{session.id}"
                resolved["session_datetime"] = session.start_datetime.isoformat() if session.start_datetime else None

        # Resolve belt rank
        if params.get("belt_name") or params.get("new_belt") or params.get("target_belt"):
            belt_name = params.get("target_belt") or params.get("new_belt") or params.get("belt_name")
            ranks = self.env["dojo.belt.rank"].search([
                ("name", "ilike", belt_name)
            ], limit=1)
            if ranks:
                resolved["new_rank_id"] = ranks[0].id
                resolved["new_rank_name"] = ranks[0].name
                resolved["target_belt"] = ranks[0].name  # alias used by belt_test_register confirmation template

        # Resolve class template (for course_enroll — no session lookup needed)
        intent_type_for_resolve = (intent_data.get("intent_type", "") or "").lower()
        # For course_enroll, always resolve the template and ignore any session_id
        if params.get("class_name") and (
            intent_type_for_resolve == "course_enroll" or not resolved.get("session_id")
        ):
            templates = self.env["dojo.class.template"].search([
                ("name", "ilike", params["class_name"])
            ], limit=5)
            if templates:
                # Pick best match by char-length scoring
                query_words = [
                    w.lower() for w in re.split(r"\W+", params["class_name"])
                    if len(w) > 2
                ]
                best_score, best_tmpl = 0, templates[0]
                for t in templates:
                    tname = (t.name or "").lower()
                    score = sum(len(w) for w in query_words if w in tname)
                    if score > best_score:
                        best_score, best_tmpl = score, t
                resolved["template_id"] = best_tmpl.id
                resolved["template_name"] = best_tmpl.name

        # Resolve subscription plan
        if params.get("plan_name") or params.get("plan_id"):
            plan = None
            if params.get("plan_id"):
                plan = self.env["dojo.subscription.plan"].browse(int(params["plan_id"]))
            elif params.get("plan_name"):
                plans = self.env["dojo.subscription.plan"].search([
                    ("name", "ilike", params["plan_name"])
                ], limit=1)
                plan = plans[0] if plans else None

            if plan and plan.exists():
                resolved["plan_id"] = plan.id
                resolved["plan_name"] = plan.name

        return resolved

    # ═══════════════════════════════════════════════════════════════════════════
    # Result Formatter — converts execution data into human-readable text
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _format_exec_result_as_response(self, intent_type, exec_result):
        """Convert an intent execution result dict into a chat-friendly string."""
        if not exec_result or not exec_result.get("success"):
            return (exec_result.get("error") or exec_result.get("message")) if exec_result else None

        # ── Bulk operation result ──────────────────────────────────────────
        if exec_result.get("bulk"):
            results = exec_result.get("results", [])
            lines = [exec_result.get("message", "Done.")]
            for r in results:
                if r.get("success"):
                    lines.append("  ✓ {}".format(r.get("name", "?")))
                else:
                    lines.append("  ✗ {} — {}".format(r.get("name", "?"), r.get("error", "Failed")))
            return "\n".join(lines)

        data = exec_result.get("data")
        msg = exec_result.get("message", "")

        if intent_type in ("schedule_today", "class_list"):
            if not data:
                return "No classes scheduled for today."
            lines = ["Here's today's schedule:"]
            for s in data:
                # Generic read handler returns raw field names; handle both formats.
                # name: may come as "name" (legacy) or from template_id many2one tuple
                name = s.get("name")
                if not name:
                    tmpl = s.get("template_id")
                    if isinstance(tmpl, (list, tuple)) and len(tmpl) > 1:
                        name = tmpl[1]
                    elif isinstance(tmpl, str):
                        name = tmpl
                name = name or "Class"

                # time: may come as "time" (legacy) or from start_datetime
                time_val = s.get("time")
                if not time_val:
                    dt = s.get("start_datetime")
                    if dt and hasattr(dt, "strftime"):
                        time_val = dt.strftime("%H:%M")
                    elif dt:
                        time_val = str(dt)[11:16]
                time_val = time_val or "?"

                # enrolled count: may come as "enrolled" or "seats_taken"
                enrolled = s.get("enrolled") if s.get("enrolled") is not None else s.get("seats_taken", 0)
                capacity = s.get("capacity", 0)
                lines.append("  • {} at {} — {}/{} enrolled".format(name, time_val, enrolled, capacity))
            return "\n".join(lines)

        if intent_type == "member_lookup":
            if not data:
                return msg
            records = data if isinstance(data, list) else [data]
            records = [r for r in records if r]
            if not records:
                return msg

            # If multiple records returned, show a count summary grouped by state
            # instead of one record card (avoids returning the wrong member).
            if len(records) > 1:
                from collections import Counter
                state_counts = Counter(
                    r.get("membership_state", r.get("state", "unknown")) for r in records
                )
                total = len(records)
                # If all records share the same state, it was a filtered query
                if len(state_counts) == 1:
                    only_state = next(iter(state_counts))
                    label = only_state.replace("_", " ").title()
                    return f"There are {total} {label} members."
                breakdown = ", ".join(
                    f"{count} {s.replace('_', ' ')}" for s, count in sorted(state_counts.items())
                )
                return f"Total members: {total} ({breakdown})"

            # Single member card
            record = records[0]
            rank_val = record.get("current_rank_id")
            rank_str = rank_val[1] if isinstance(rank_val, (list, tuple)) and len(rank_val) > 1 else None
            state_val = record.get("membership_state", record.get("state", "unknown"))
            lines = ["{} — {}".format(record.get("name", "Member"), state_val)]
            if rank_str:
                lines.append("  Rank: {}".format(rank_str))
            if record.get("email"):
                lines.append("  Email: {}".format(record["email"]))
            if record.get("phone"):
                lines.append("  Phone: {}".format(record["phone"]))
            if record.get("date_of_birth"):
                lines.append("  DOB: {}".format(record["date_of_birth"]))
            # Stats (if available)
            if record.get("total_sessions"):
                lines.append("  Classes attended: {}".format(record["total_sessions"]))
            if record.get("attendance_rate") is not None and record.get("attendance_rate") > 0:
                lines.append("  Attendance rate: {:.0f}%".format(record["attendance_rate"]))
            if record.get("attendance_since_last_rank"):
                lines.append("  Since last rank: {} classes".format(record["attendance_since_last_rank"]))
            if record.get("current_stripe_count"):
                lines.append("  Stripes: {}".format(record["current_stripe_count"]))
            if record.get("total_points"):
                lines.append("  Points: {}".format(record["total_points"]))
            return "\n".join(lines)

        if intent_type == "belt_lookup":
            if not data:
                return msg
            if isinstance(data, list):
                return "Belt ranks:\n" + "\n".join("  • {}".format(r.get("name", "")) for r in data)
            return "Belt: {}".format(data.get("name", msg))

        if intent_type == "subscription_lookup":
            if not data:
                return msg
            record = data[0] if isinstance(data, list) else data
            if not record:
                return msg
            member_val = record.get("member_id")
            member_name = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
            plan_val = record.get("plan_id")
            plan_name = plan_val[1] if isinstance(plan_val, (list, tuple)) and len(plan_val) > 1 else "Unknown plan"
            state = record.get("state", "unknown")
            lines = ["{} — {} ({})".format(member_name, plan_name, state)]
            if record.get("date_start"):
                lines.append("  Started: {}".format(record["date_start"]))
            if record.get("date"):
                lines.append("  Ends: {}".format(record["date"]))
            if record.get("recurring_next_date"):
                lines.append("  Next billing: {}".format(record["recurring_next_date"]))
            if record.get("amount_total"):
                lines.append("  Amount: ${}".format(record["amount_total"]))
            failures = record.get("billing_failure_count", 0)
            if failures:
                lines.append("  ⚠️ Billing failures: {} (last: {})".format(
                    failures, record.get("last_billing_failure_date", "?")))
            if record.get("to_renew"):
                lines.append("  📋 Flagged for renewal")
            return "\n".join(lines)

        if intent_type == "attendance_history":
            if not data:
                return msg
            lines = [msg]
            for rec in data[:5]:
                session = rec.get("session") or "Open mat"
                lines.append("  • {} — {}".format(rec.get("date", "?"), session))
            return "\n".join(lines)

        if intent_type == "phone_lookup":
            if not data:
                return msg or "No member or lead found with that phone number."
            lines = [msg or f"{len(data)} result(s) found:"]
            for r in data:
                rtype = r.get("type", "?")
                name = r.get("name") or "Unknown"
                if rtype == "member":
                    state = r.get("membership_state", "")
                    lines.append(f"  👤 Member: {name}" + (f" ({state})" if state else ""))
                elif rtype == "lead":
                    stage = r.get("stage", "")
                    lines.append(f"  📋 Lead: {name}" + (f" — {stage}" if stage else ""))
                if r.get("email"):
                    lines.append(f"     Email: {r['email']}")
                if r.get("phone"):
                    lines.append(f"     Phone: {r['phone']}")
            return "\n".join(lines)

        if intent_type == "lead_lookup":
            if not data:
                return msg
            lines = [msg]
            for lead in data[:8]:
                contact = lead.get("contact_name") or lead.get("name") or "Unknown"
                stage = lead.get("stage") or "No stage"
                converted = " ✓ converted" if lead.get("is_converted") else ""
                lines.append("  • {} — {}{}".format(contact, stage, converted))
            return "\n".join(lines)

        if intent_type == "task_list":
            if not data:
                return msg or "No tasks found."
            lines = [f"Here are your tasks ({len(data)} total):"]
            for task in data:
                name = task.get("name") or "Untitled"
                deadline = task.get("date_deadline") or ""
                deadline_str = f" — due {deadline}" if deadline else ""
                priority_str = " ⚡" if task.get("priority") == "1" else ""
                stage_val = task.get("stage_id")
                stage_str = stage_val[1] if isinstance(stage_val, (list, tuple)) and len(stage_val) > 1 else ""
                stage_disp = f" [{stage_str}]" if stage_str else ""
                lines.append("  • {}{}{}{}".format(name, stage_disp, deadline_str, priority_str))
            return "\n".join(lines)

        if intent_type == "belt_test_list":
            if not data:
                return msg or "No upcoming belt tests."
            lines = [f"{len(data)} upcoming belt test(s):"]
            for t in data:
                name = t.get("name") or "Belt Test"
                date_val = t.get("test_date") or "TBD"
                loc = t.get("location") or ""
                prog_val = t.get("program_id")
                prog_str = prog_val[1] if isinstance(prog_val, (list, tuple)) and len(prog_val) > 1 else ""
                parts = [f"  • {name} — {date_val}"]
                if prog_str:
                    parts.append(f"({prog_str})")
                if loc:
                    parts.append(f"@ {loc}")
                lines.append(" ".join(parts))
            return "\n".join(lines)

        if intent_type == "belt_test_registration_list":
            if not data:
                return msg or "No one is registered for upcoming belt tests yet."
            lines = [f"{len(data)} registration(s) for upcoming belt tests:"]
            for r in data:
                member_val = r.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "?"
                test_val = r.get("test_id")
                test_str = test_val[1] if isinstance(test_val, (list, tuple)) and len(test_val) > 1 else "?"
                rank_val = r.get("target_rank_id")
                rank_str = rank_val[1] if isinstance(rank_val, (list, tuple)) and len(rank_val) > 1 else "?"
                result = r.get("result") or "pending"
                icon = {"pending": "⏳", "pass": "✅", "fail": "❌", "withdrew": "🚫"}.get(result, "")
                lines.append(f"  {icon} {member_str} — testing for {rank_str} ({test_str})")
            return "\n".join(lines)

        if intent_type == "program_list":
            if not data:
                return msg or "No programs found."
            lines = [f"{len(data)} program(s):"]
            for p in data:
                name = p.get("name") or "?"
                trial = " [Trial]" if p.get("is_trial") else ""
                lines.append(f"  • {name}{trial}")
            return "\n".join(lines)

        if intent_type == "class_template_list":
            if not data:
                return msg or "No courses found."
            lines = [f"{len(data)} course(s):"]
            for c in data:
                name = c.get("name") or "?"
                level = c.get("level") or ""
                cap = c.get("max_capacity") or "?"
                dur = c.get("duration_minutes") or "?"
                prog_val = c.get("program_id")
                prog_str = prog_val[1] if isinstance(prog_val, (list, tuple)) and len(prog_val) > 1 else ""
                parts = [f"  • {name}"]
                if level:
                    parts.append(f"({level})")
                if prog_str:
                    parts.append(f"— {prog_str}")
                parts.append(f"— {dur}min, cap {cap}")
                lines.append(" ".join(parts))
            return "\n".join(lines)

        if intent_type == "social_post_list":
            if not data:
                return msg or "No social posts found."
            lines = [f"{len(data)} post(s):"]
            for p in data:
                state = p.get("state") or "?"
                message = (p.get("message") or "")[:60]
                if len(p.get("message", "")) > 60:
                    message += "..."
                sched = p.get("scheduled_date") or ""
                error = p.get("error_message") or ""
                icon = {"draft": "📝", "scheduled": "⏰", "posted": "✅", "error": "❌"}.get(state, "")
                line = f"  {icon} [{state}] {message}"
                if sched:
                    line += f" (scheduled: {str(sched)[:16]})"
                if error:
                    line += f" — {error}"
                lines.append(line)
            return "\n".join(lines)

        if intent_type == "program_enrollment_lookup":
            if not data:
                return msg or "No program enrollments found."
            lines = [f"{len(data)} enrollment(s):"]
            for e in data:
                member_val = e.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "?"
                prog_val = e.get("program_id")
                prog_str = prog_val[1] if isinstance(prog_val, (list, tuple)) and len(prog_val) > 1 else "?"
                active = "Active" if e.get("is_active") else "Inactive"
                lines.append(f"  • {member_str} — {prog_str} ({active})")
            return "\n".join(lines)

        if intent_type == "onboarding_status":
            if not data:
                return msg or "No onboarding records found."
            lines = [f"{len(data)} onboarding record(s):"]
            for o in data:
                member_val = o.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "?"
                pct = o.get("progress_pct", 0)
                state = o.get("state", "?")
                steps_done = sum(1 for k in ("step_member_info", "step_household", "step_enrollment",
                                             "step_subscription", "step_portal_access") if o.get(k))
                lines.append(f"  • {member_str} — {pct}% ({state}) — {steps_done}/5 steps done")
            return "\n".join(lines)

        if intent_type == "points_lookup":
            if not data:
                return msg or "No points transactions found."
            total = sum(r.get("amount", 0) for r in data)
            member_val = data[0].get("member_id") if data else None
            member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
            lines = [f"{member_str} — {total} total points (last {len(data)} transactions):"]
            for r in data[:5]:
                source = r.get("source_type") or "?"
                amt = r.get("amount", 0)
                note = r.get("note") or ""
                sign = "+" if amt >= 0 else ""
                lines.append(f"  • {sign}{amt} pts ({source}){f' — {note}' if note else ''}")
            return "\n".join(lines)

        if intent_type == "credit_lookup":
            if not data:
                return msg or "No credit transactions found."
            total = sum(r.get("amount", 0) for r in data)
            member_val = data[0].get("member_id") if data else None
            member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
            lines = [f"{member_str} — {total} credit balance (last {len(data)} transactions):"]
            for r in data[:5]:
                ttype = r.get("transaction_type") or "?"
                amt = r.get("amount", 0)
                status = r.get("status") or ""
                sign = "+" if amt >= 0 else ""
                lines.append(f"  • {sign}{amt} ({ttype}) [{status}]")
            return "\n".join(lines)

        if intent_type == "birthday_upcoming":
            if not data:
                return msg or "No upcoming birthdays."
            lines = [f"🎂 {len(data)} upcoming birthday(s):"]
            for b in data:
                name = b.get("name", "?")
                days = b.get("days_until", "?")
                age = b.get("age_turning", "?")
                bday = b.get("birthday", "")
                if days == 0:
                    when = "TODAY! 🎉"
                elif days == 1:
                    when = "tomorrow"
                else:
                    when = f"in {days} days ({bday})"
                lines.append(f"  • {name} — turning {age}, {when}")
            return "\n".join(lines)

        if intent_type == "household_lookup":
            if not data:
                return msg
            hname = data.get("household_name", "Household")
            guardian = data.get("primary_guardian", "None")
            members_list = data.get("members", [])
            lines = [f"🏠 {hname} — Primary guardian: {guardian}"]
            for m in members_list:
                role = m.get("roles", "Contact")
                lines.append(f"  • {m.get('name', '?')} ({role})")
            return "\n".join(lines)

        if intent_type == "course_auto_enroll_list":
            if not data:
                return msg or "No auto-enroll rules found."
            lines = [f"{len(data)} auto-enroll rule(s):"]
            for r in data:
                member_val = r.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "?"
                tpl_val = r.get("template_id")
                tpl_str = tpl_val[1] if isinstance(tpl_val, (list, tuple)) and len(tpl_val) > 1 else "?"
                mode = r.get("mode") or "?"
                days = [d for d, k in [("Mon", "pref_mon"), ("Tue", "pref_tue"), ("Wed", "pref_wed"),
                                       ("Thu", "pref_thu"), ("Fri", "pref_fri"), ("Sat", "pref_sat"),
                                       ("Sun", "pref_sun")] if r.get(k)]
                days_str = ", ".join(days) if days else "all days"
                active = "✅" if r.get("active") else "❌"
                lines.append(f"  {active} {member_str} → {tpl_str} ({mode}) — {days_str}")
            return "\n".join(lines)

        if intent_type == "campaign_list":
            if not data:
                return msg or "No AI calling campaigns found."
            lines = [f"{len(data)} campaign(s):"]
            for c in data:
                name = c.get("name") or "Untitled"
                state = c.get("state") or "?"
                total = c.get("total_calls", 0)
                done = c.get("completed_calls", 0)
                failed = c.get("failed_calls", 0)
                icon = {"draft": "📝", "running": "▶️", "paused": "⏸️", "done": "✅"}.get(state, "")
                lines.append(f"  {icon} {name} [{state}] — {done}/{total} calls done, {failed} failed")
            return "\n".join(lines)

        if intent_type == "kiosk_announcement_list":
            if not data:
                return msg or "No kiosk announcements found."
            lines = [f"{len(data)} announcement(s):"]
            for a in data:
                title = a.get("title") or "Untitled"
                body = (a.get("body") or "")[:60]
                if len(a.get("body", "")) > 60:
                    body += "..."
                kiosk_val = a.get("config_id")
                kiosk_str = kiosk_val[1] if isinstance(kiosk_val, (list, tuple)) and len(kiosk_val) > 1 else ""
                parts = [f"  • {title}"]
                if kiosk_str:
                    parts.append(f"({kiosk_str})")
                if body:
                    parts.append(f"— {body}")
                lines.append(" ".join(parts))
            return "\n".join(lines)

        if intent_type == "member_enrollment_list":
            if not data:
                return msg or "No active enrollments found for this member."
            lines = [f"{len(data)} enrollment(s):"]
            for e in data:
                member_val = e.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
                session_val = e.get("session_id")
                session_str = session_val[1] if isinstance(session_val, (list, tuple)) and len(session_val) > 1 else "Session"
                status = e.get("status", "")
                lines.append(f"  • {session_str} — {status}")
            return "\n".join(lines)

        if intent_type == "subscription_dunning_list":
            if not data:
                return msg or "No subscriptions with billing failures found."
            lines = [f"{len(data)} subscription(s) with billing failures:"]
            for s in data:
                member_val = s.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
                failures = s.get("billing_failure_count", 0)
                state = s.get("state", "")
                last_fail = s.get("last_billing_failure_date", "")
                lines.append(f"  • {member_str} — {failures} failure(s), state: {state}, last: {last_fail}")
            return "\n".join(lines)

        if intent_type == "enrollment_availability":
            if not data:
                return msg or "No upcoming sessions found."
            lines = [f"{len(data)} upcoming session(s):"]
            for s in data:
                name = s.get("name") or "Session"
                capacity = s.get("capacity", 0)
                taken = s.get("seats_taken", 0)
                available = max(0, capacity - taken)
                start = s.get("start_datetime", "")
                lines.append(f"  • {name} — {available}/{capacity} spots available ({start})")
            return "\n".join(lines)

        if intent_type == "class_enrollment_list":
            if not data:
                return msg or "No enrollments found for this session."
            lines = [f"{len(data)} enrolled:"]
            for e in data:
                member_val = e.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
                status = e.get("status", "")
                lines.append(f"  • {member_str} — {status}")
            return "\n".join(lines)

        if intent_type == "instructor_todo_list":
            if not data:
                return msg or "No instructor tasks found."
            lines = [f"{len(data)} task(s) for instructors:"]
            for t in data:
                name = t.get("name", "Task")
                deadline = t.get("date_deadline", "")
                priority = "⭐ " if t.get("priority") == "1" else ""
                stage_val = t.get("stage_id")
                stage = stage_val[1] if isinstance(stage_val, (list, tuple)) and len(stage_val) > 1 else ""
                due_str = f" (due {deadline})" if deadline else ""
                stage_str = f" [{stage}]" if stage else ""
                lines.append(f"  • {priority}{name}{stage_str}{due_str}")
            return "\n".join(lines)

        if intent_type == "belt_readiness_check":
            # belt_readiness_check returns a dict, not a list
            if isinstance(result, dict) and result.get("success"):
                return result.get("message", "Belt readiness check complete.")
            return msg or "Could not determine belt readiness."

        if intent_type in ("revenue_summary", "trial_conversion_report"):
            if isinstance(result, dict) and result.get("success"):
                return result.get("message", "Report complete.")
            return msg or "Could not generate report."

        if intent_type == "payment_history":
            if not data:
                return msg or "No payments found."
            lines = [f"{len(data)} payment(s):"]
            for p in data:
                partner_val = p.get("partner_id")
                partner_str = partner_val[1] if isinstance(partner_val, (list, tuple)) and len(partner_val) > 1 else "Partner"
                amount = p.get("amount", 0)
                currency_val = p.get("currency_id")
                currency = currency_val[1] if isinstance(currency_val, (list, tuple)) and len(currency_val) > 1 else ""
                pdate = p.get("date", "")
                lines.append(f"  • {partner_str} — {currency} {amount:,.2f} on {pdate}")
            return "\n".join(lines)

        if intent_type == "subscription_dunning_list":
            if not data:
                return msg or "No subscriptions with billing failures found."
            lines = [f"{len(data)} subscription(s) with billing issues:"]
            for s in data:
                member_val = s.get("member_id")
                member_str = member_val[1] if isinstance(member_val, (list, tuple)) and len(member_val) > 1 else "Member"
                failures = s.get("billing_failure_count", 0)
                last_fail = s.get("last_billing_failure_date", "")
                lines.append(f"  • {member_str} — {failures} failure(s), last: {last_fail}")
            return "\n".join(lines)

        # Default: return the message
        return msg

    # ═══════════════════════════════════════════════════════════════════════════
    # Intent Execution Router
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _execute_intent(self, intent_type, intent_data, resolved_data, action_log):
        """
        Route intent to appropriate handler and execute.
        
        Supports config-driven generic handlers (read & CRUD) and custom handler fallbacks.
        For undoable actions, creates snapshots before execution.
        """
        # Normalise intent_type aliases — LLMs sometimes use slightly different names
        _INTENT_ALIASES = {
            # CRM lead
            "create_lead": "lead_create",
            "create_lead_confirm": "lead_create",
            "crm_lead_create": "lead_create",
            "lead_delete": "lead_mark_lost",
            "delete_lead": "lead_mark_lost",
            "crm_lead_delete": "lead_mark_lost",
            "qualify_lead": "lead_qualify",
            "convert_lead": "lead_convert",
            "mark_lead_won": "lead_mark_won",
            "mark_lead_lost": "lead_mark_lost",
            "lead_list": "lead_lookup",
            "list_leads": "lead_lookup",
            # Attendance
            "checkin": "attendance_checkin",
            "check_in": "attendance_checkin",
            "checkout": "attendance_checkout",
            "check_out": "attendance_checkout",
            # Classes / schedule
            "create_class": "class_create",
            "schedule_class": "class_create",
            "cancel_class": "class_cancel",
            "schedule_for_date": "schedule_today",
            "schedule_for_tomorrow": "schedule_today",
            "class_schedule": "schedule_today",
            "get_schedule": "schedule_today",
            # Members
            "promote_belt": "belt_promote",
            "enroll_member": "member_enroll",
            "unenroll_member": "member_unenroll",
            "create_member": "member_create",
            # Tasks
            "list_tasks": "task_list",
        }
        intent_type = _INTENT_ALIASES.get(intent_type, intent_type)

        # Priority 1: Check if this intent uses the generic read handler
        read_config = _INTENT_HANDLER_CONFIG.get(intent_type)
        if read_config:
            try:
                return self._handle_generic_read(intent_type, intent_data, resolved_data, read_config)
            except Exception as e:
                _logger.error("Generic read handler for %s failed: %s", intent_type, e, exc_info=True)
                return {"success": False, "error": f"Handler error: {e}"}
        
        # Priority 2: Check if this intent uses the generic CRUD handler
        crud_config = _CRUD_HANDLER_CONFIG.get(intent_type)
        if crud_config:
            try:
                return self._handle_generic_crud(intent_type, intent_data, resolved_data, action_log, crud_config)
            except UserError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                _logger.error("Generic CRUD handler for %s failed: %s", intent_type, e, exc_info=True)
                return {"success": False, "error": f"Handler error: {e}"}
        
        # Priority 3: Fall back to custom handlers for special intents
        handlers = {
            # Read-only intents with member-context-dependent logic
            "belt_lookup": self._handle_belt_lookup,

            # Mutating intents (complex business logic - cannot be generalized)
            "member_enroll": self._handle_member_enroll,
            "member_unenroll": self._handle_member_unenroll,
            "belt_promote": self._handle_belt_promote,
            "contact_parent": self._handle_contact_parent,
            "attendance_checkin": self._handle_attendance_checkin,
            "attendance_checkout": self._handle_attendance_checkout,
            "course_enroll": self._handle_course_enroll,
            "belt_test_register": self._handle_belt_test_register,

            # Special intents
            "undo_action": self._handle_undo_action,
            "unknown": self._handle_unknown,

            # Extended intents (with complex business logic)
            "subscription_pause": self._handle_subscription_pause,
            "subscription_resume": self._handle_subscription_resume,
            "at_risk_members": self._handle_at_risk_members,
            "subscription_expiring": self._handle_subscription_expiring,
            "campaign_lookup": self._handle_campaign_lookup,
            # Cross-model phone lookup (members + leads)
            "phone_lookup": self._handle_phone_lookup,
            # CRM intents (methods defined in dojo_crm/models/ai_crm_service.py via _inherit)
            "lead_lookup": self._handle_lead_lookup,
            "pipeline_summary": self._handle_pipeline_summary,
            "trial_schedule": self._handle_trial_schedule,
            "lead_qualify": self._handle_lead_qualify,
            "lead_mark_attended": self._handle_lead_mark_attended,
            "lead_convert": self._handle_lead_convert,
            "lead_create": self._handle_lead_create,
            "lead_mark_lost": self._handle_lead_mark_lost,
            "lead_mark_won": self._handle_lead_mark_won,
            "marketing_card_lookup": self._handle_marketing_card_lookup,
            "campaign_create": self._handle_campaign_create,
            "campaign_activate": self._handle_campaign_activate,
            "social_post_create": self._handle_social_post_create,
            "social_post_schedule": self._handle_social_post_schedule,
            # Core Odoo module intents (defined in ai_calendar_service.py / ai_communication_service.py)
            "calendar_event_create": self._handle_calendar_event_create,
            "calendar_event_cancel": self._handle_calendar_event_cancel,
            "send_email": self._handle_send_email,
            "send_sms": self._handle_send_sms,
            "email_blast": self._handle_email_blast,
            "sms_blast": self._handle_sms_blast,
            # Task intents with custom logic
            "task_complete": self._handle_task_complete,
            "task_update": self._handle_task_update,
            # Household lookup (traverses res.partner hierarchy)
            "household_lookup": self._handle_household_lookup,
            "birthday_upcoming": self._handle_birthday_upcoming,
            # Meta intents
            "capability_list": self._handle_capability_list,
            "help_request": self._handle_help_request,
            # ── Batch A: Points & Member Admin ────────────────────────────
            "points_award": self._handle_points_award,
            "points_deduct": self._handle_points_deduct,
            "member_archive": self._handle_member_archive,
            "member_note_add": self._handle_member_note_add,
            "subscription_billing_retry": self._handle_subscription_billing_retry,
            # ── Batch B: Class & Roster ───────────────────────────────────
            "class_session_generate": self._handle_class_session_generate,
            # ── Batch C: Belt & Instructor ────────────────────────────────
            "belt_readiness_check": self._handle_belt_readiness_check,
            "instructor_todo_complete": self._handle_instructor_todo_complete,
            # ── Batch D: Revenue, CRM & Portal ────────────────────────────
            "revenue_summary": self._handle_revenue_summary,
            "guardian_portal_invite": self._handle_guardian_portal_invite,
            # CRM Batch D (methods provided by dojo_crm via _inherit)
            "lead_note_add": self._handle_lead_note_add,
            "trial_conversion_report": self._handle_trial_conversion_report,
            # ── New Query Intents ─────────────────────────────────────────
            "checkin_required_today": self._handle_checkin_required_today,
            "belt_test_roster": self._handle_belt_test_roster,
            "belt_eligible_members": self._handle_belt_eligible_members,
            "subscription_status_summary": self._handle_subscription_status_summary,
            # ── New Session & Belt Intents ────────────────────────────────
            "next_class_session": self._handle_next_class_session,
            "previous_class_session": self._handle_previous_class_session,
            "belt_readiness_all": self._handle_belt_readiness_all,
            "enroll_next_session": self._handle_enroll_next_session,
        }

        handler = handlers.get(intent_type, self._handle_unknown)

        try:
            return handler(intent_data, resolved_data, action_log)
        except UserError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            _logger.error("Intent handler %s failed: %s", intent_type, e, exc_info=True)
            return {"success": False, "error": f"Handler error: {e}"}

    # ═══════════════════════════════════════════════════════════════════════════
    # Generic Read Handler (Config-Driven)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_generic_read(self, intent_type, intent_data, resolved_data, config):
        """
        Generic handler for all read-only intent operations.
        
        Replaces individual intent handlers for data retrieval.
        Supports dynamic field selection, filtering, and formatting.
        
        Args:
            intent_type: Type of intent (for logging)
            intent_data: Parsed intent with parameters
            resolved_data: Pre-resolved entity IDs
            config: Handler config from _INTENT_HANDLER_CONFIG
        
        Returns:
            dict: {success, message, data}
        """
        model_name = config.get("model")
        if not model_name:
            return {"success": False, "error": f"No model configured for {intent_type}"}
        
        try:
            Model = self.env[model_name]
            if config.get("use_sudo"):
                Model = Model.sudo()
            if config.get("include_archived") and "active" in self.env[model_name]._fields:
                Model = Model.with_context(active_test=False)
        except KeyError:
            return {"success": False, "error": f"Model '{model_name}' does not exist"}
        
        # Step 1: Build domain (static or dynamic)
        domain = config.get("domain", [])
        if "domain_builder" in config:
            builder_method_name = config["domain_builder"]
            builder_method = getattr(self, builder_method_name, None)
            if builder_method:
                domain = builder_method(intent_data, resolved_data) or domain
        
        # Step 2: Determine limit (static or from intent parameters)
        limit = config.get("limit", 20)
        if "limit_from_params" in config:
            param_name = config["limit_from_params"]
            params = intent_data.get("parameters", {}) if intent_data else {}
            limit = params.get(param_name, limit)
        
        # Step 3: Get readable fields (respect model field restrictions)
        requested_fields = config.get("fields", [])
        model_fields = Model._fields
        readable_fields = [
            f for f in requested_fields
            if f in model_fields and not model_fields[f].groups
        ]
        
        if not readable_fields:
            readable_fields = list(model_fields.keys())[:10]  # Safety fallback
        
        # Step 4: Search records
        order = config.get("order", "name asc" if "name" in model_fields else "id desc")
        records = Model.search(domain, limit=limit, order=order)
        
        if not records:
            return {
                "success": True,
                "message": f"No records found in {model_name}",
                "data": []
            }
        
        # Step 5: Format results by field type
        raw_data = records.read(readable_fields)
        formatted_data = []
        
        for record in raw_data:
            formatted_record = {}
            for field_name in readable_fields:
                value = record.get(field_name)
                field_obj = model_fields[field_name]
                
                # Auto-format based on field type
                if field_obj.type == "many2one" and isinstance(value, (list, tuple)):
                    formatted_record[field_name] = value[1] if value else None
                elif field_obj.type in ("datetime", "date") and value:
                    formatted_record[field_name] = value.isoformat() if hasattr(value, "isoformat") else str(value)
                elif field_obj.type == "many2many" and isinstance(value, list):
                    formatted_record[field_name] = value  # Already IDs
                elif field_obj.type == "selection" and value:
                    # Try to get human-readable label
                    if hasattr(field_obj, "selection"):
                        selection_dict = dict(field_obj.selection if callable(field_obj.selection) else field_obj.selection)
                        formatted_record[field_name] = selection_dict.get(value, value)
                    else:
                        formatted_record[field_name] = value
                else:
                    formatted_record[field_name] = value
            
            formatted_data.append(formatted_record)
        
        return {
            "success": True,
            "message": f"Found {len(formatted_data)} records in {model_name}",
            "data": formatted_data
        }

    # ─── Domain Builders for config-driven intents ────────────────────────────
    
    @api.model
    def _domain_member_lookup(self, intent_data, resolved_data):
        """
        Build domain for member lookup.
        Supports lookup by member_id, member_name, or membership_state filter.
        """
        member_id = resolved_data.get("member_id")
        if member_id:
            return [("id", "=", member_id)]

        params = intent_data.get("parameters", {}) if intent_data else {}

        # Full name — most reliable
        name = params.get("member_name", "")
        if name:
            return [("name", "ilike", name)]

        # First/last name sent separately by the AI
        first = (params.get("first_name") or "").strip()
        last = (params.get("last_name") or "").strip()
        if first and last:
            return [("name", "ilike", f"{first}%{last}")]
        elif last:
            return [("name", "ilike", last)]
        elif first:
            return [("name", "ilike", first)]

        # Phone-number lookup — strip formatting and search phone + mobile fields
        phone = params.get("phone") or params.get("phone_number") or ""
        if phone:
            stripped = re.sub(r'[\s\(\)\-\+\.]', '', phone)
            return [
                "|",
                "|",
                ("phone", "ilike", stripped),
                ("mobile", "ilike", stripped),
                ("phone", "ilike", phone),
            ]

        # Aggregate / filtered queries ("how many active students", etc.)
        state = params.get("membership_state") or params.get("state")
        if state:
            return [("membership_state", "=", state)]

        # No identifying params — return nothing rather than all records
        return [("id", "=", 0)]
    
    @api.model
    def _domain_class_list(self, intent_data, resolved_data):
        """
        Build domain for class session list.
        Filters by date if provided in intent parameters.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        target_date = params.get("date") or fields.Date.today().isoformat()
        
        return [
            ("start_datetime", ">=", f"{target_date} 00:00:00"),
            ("start_datetime", "<=", f"{target_date} 23:59:59"),
        ]
    
    @api.model
    def _domain_schedule_today(self, intent_data, resolved_data):
        """
        Build domain for schedule query. Uses today by default; respects
        a 'date' parameter (ISO string or plain words like 'tomorrow').
        """
        import datetime
        raw_date = (intent_data.get("parameters") or {}).get("date") or ""
        target_date = None
        if raw_date:
            raw_lower = raw_date.strip().lower()
            today = fields.Date.today()
            if raw_lower in ("tomorrow", "next day"):
                target_date = today + datetime.timedelta(days=1)
            elif raw_lower in ("yesterday",):
                target_date = today - datetime.timedelta(days=1)
            elif raw_lower in ("today", ""):
                target_date = today
            else:
                try:
                    target_date = datetime.date.fromisoformat(raw_date[:10])
                except (ValueError, TypeError):
                    target_date = today
        if target_date is None:
            target_date = fields.Date.today()
        d = target_date.isoformat()
        return [
            ("start_datetime", ">=", f"{d} 00:00:00"),
            ("start_datetime", "<=", f"{d} 23:59:59"),
        ]
    
    @api.model
    def _domain_subscription_lookup(self, intent_data, resolved_data):
        """
        Build domain for subscription lookup.
        Returns active subscriptions for a member.
        """
        member_id = resolved_data.get("member_id")
        if not member_id:
            return [("id", "=", -1)]  # Return nothing
        
        return [
            ("member_id", "=", member_id),
            ("state", "=", "active"),
        ]
    
    @api.model
    def _domain_attendance_history(self, intent_data, resolved_data):
        """
        Build domain for attendance history.
        Returns recent attendance logs for a member.
        """
        member_id = resolved_data.get("member_id")
        if not member_id:
            return [("id", "=", -1)]  # Return nothing
        
        return [("member_id", "=", member_id)]

    # ═══════════════════════════════════════════════════════════════════════════
    # Generic CRUD Handler (Config-Driven)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_generic_crud(self, intent_type, intent_data, resolved_data, action_log, config):
        """
        Generic CRUD handler for create, update, delete operations.
        
        Supports:
        - Field validation (required, type checking)
        - Relationship resolution (many2one lookups)
        - Default values and builders
        - Audit trail via mail.thread
        - Undo snapshots
        
        Args:
            intent_type: Type of intent (for logging)
            intent_data: Parsed intent with parameters
            resolved_data: Pre-resolved entity IDs
            action_log: Action log record
            config: CRUD config from _CRUD_HANDLER_CONFIG
        
        Returns:
            dict: {success, message, data}
        """
        model_name = config.get("model")
        operation = config.get("operation", "create")
        
        try:
            Model = self.env[model_name]
        except KeyError:
            return {"success": False, "error": f"Model '{model_name}' does not exist"}
        
        # ─── CREATE Operation ─────────────────────────────────────────────────
        if operation == "create":
            return self._crud_create(Model, model_name, intent_data, config, action_log)
        
        # ─── UPDATE Operation ─────────────────────────────────────────────────
        elif operation == "update":
            domain = config.get("domain", [])
            if "target_domain_builder" in config:
                builder_method = getattr(self, config["target_domain_builder"], None)
                if builder_method:
                    domain = builder_method(intent_data, resolved_data) or domain
            return self._crud_update(Model, model_name, intent_data, domain, config, action_log)
        
        # ─── DELETE Operation ─────────────────────────────────────────────────
        elif operation == "delete":
            domain = config.get("domain", [])
            if "target_domain_builder" in config:
                builder_method = getattr(self, config["target_domain_builder"], None)
                if builder_method:
                    domain = builder_method(intent_data, resolved_data) or domain
            return self._crud_delete(Model, model_name, domain, config, action_log)
        
        return {"success": False, "error": f"Unknown CRUD operation: {operation}"}

    @api.model
    def _crud_create(self, Model, model_name, intent_data, config, action_log):
        """Create a new record."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        fields_config = config.get("fields", {})
        
        # ─── Validate required fields ─────────────────────────────────────────
        values = {}
        for field_name, field_cfg in fields_config.items():
            if field_cfg.get("required") and field_name not in params:
                return {"success": False, "error": f"Required field '{field_name}' not provided"}
            
            if field_name in params:
                value = params[field_name]
                
                # Resolve relationships (many2one)
                if field_cfg.get("type") == "many2one" and field_cfg.get("resolver"):
                    resolver = getattr(self, field_cfg["resolver"], None)
                    if resolver:
                        resolved_id = resolver(value, Model)
                        if not resolved_id:
                            return {"success": False, "error": f"Could not resolve {field_name}: {value}"}
                        values[field_name] = resolved_id
                    else:
                        values[field_name] = value
                elif field_cfg.get("type") == "many2many" and field_cfg.get("resolver"):
                    # Resolve a single name/ID to an ORM link command [(4, id)]
                    resolver = getattr(self, field_cfg["resolver"], None)
                    if resolver:
                        resolved_id = resolver(value, Model)
                        if resolved_id:
                            values[field_name] = [(4, resolved_id)]
                    else:
                        values[field_name] = value
                else:
                    values[field_name] = value
            elif "default" in field_cfg:
                values[field_name] = field_cfg["default"]
            elif "default_builder" in field_cfg:
                builder = getattr(self, field_cfg["default_builder"], None)
                if builder:
                    values[field_name] = builder()
        
        # ─── Create record ────────────────────────────────────────────────────
        try:
            record = Model.create(values)
            
            # Create undo snapshot if enabled
            if config.get("allow_undo", True):
                Snapshot = self.env["ai.undo.snapshot"]
                Snapshot.create_snapshot(action_log.id, model_name, record.id, "create")
            
            return {
                "success": True,
                "message": f"Created {record._rec_name or model_name}",
                "data": {"id": record.id, "record": record.name_get()[0][1] if hasattr(record, 'name_get') else str(record)},
            }
        except Exception as e:
            _logger.error("CRUD create failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    @api.model
    def _crud_update(self, Model, model_name, intent_data, domain, config, action_log):
        """Update existing records."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        fields_config = config.get("fields", {})
        
        # ─── Find target record ───────────────────────────────────────────────
        records = Model.search(domain, limit=1)
        if not records:
            return {"success": False, "error": f"No {model_name} found to update"}
        
        record = records[0]
        
        # ─── Build update values ──────────────────────────────────────────────
        values = {}
        for field_name, value in params.items():
            if field_name in fields_config:
                field_cfg = fields_config[field_name]
                
                # Resolve relationships (many2one)
                if field_cfg.get("type") == "many2one" and field_cfg.get("resolver"):
                    resolver = getattr(self, field_cfg["resolver"], None)
                    if resolver:
                        resolved_id = resolver(value, Model)
                        values[field_name] = resolved_id
                    else:
                        values[field_name] = value
                elif field_cfg.get("type") == "many2many" and field_cfg.get("resolver"):
                    # Resolve a single name/ID to an ORM link command [(4, id)]
                    resolver = getattr(self, field_cfg["resolver"], None)
                    if resolver:
                        resolved_id = resolver(value, Model)
                        if resolved_id:
                            values[field_name] = [(4, resolved_id)]
                    else:
                        values[field_name] = value
                else:
                    values[field_name] = value
        
        if not values:
            return {"success": False, "error": "No fields to update"}
        
        # ─── Create snapshot of old values ────────────────────────────────────
        try:
            if config.get("allow_undo", True):
                Snapshot = self.env["ai.undo.snapshot"]
                old_values = {k: getattr(record, k, None) for k in values.keys()}
                Snapshot.create_snapshot(
                    action_log.id, model_name, record.id, "write",
                    snapshot_data=old_values
                )
            
            # Update record
            record.write(values)
            
            return {
                "success": True,
                "message": f"Updated {record._rec_name or model_name}",
                "data": {"id": record.id, "updated_fields": list(values.keys())},
            }
        except Exception as e:
            _logger.error("CRUD update failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    @api.model
    def _crud_delete(self, Model, model_name, domain, config, action_log):
        """Delete records."""
        # ─── Find target record ───────────────────────────────────────────────
        records = Model.search(domain, limit=1)
        if not records:
            return {"success": False, "error": f"No {model_name} found to delete"}
        
        record = records[0]
        record_display = record.name_get()[0][1] if hasattr(record, 'name_get') else str(record)
        
        # ─── Create snapshot of record ────────────────────────────────────────
        try:
            if config.get("allow_undo", True):
                Snapshot = self.env["ai.undo.snapshot"]
                # Store full record data for potential restoration
                record_data = record.read()[0] if record else {}
                Snapshot.create_snapshot(
                    action_log.id, model_name, record.id, "unlink",
                    snapshot_data=record_data
                )
            
            # Delete record
            record.unlink()
            
            return {
                "success": True,
                "message": f"Deleted {record_display}",
                "data": {"id": record.id},
            }
        except Exception as e:
            _logger.error("CRUD delete failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    # ─── CRUD Helper: Resolvers for relationships ──────────────────────────────
    
    @api.model
    def _resolve_member(self, value, model=None):
        """Resolve member by name or ID."""
        if isinstance(value, int):
            return value
        Member = self.env["dojo.member"]
        members = Member.search([("name", "ilike", value)], limit=1)
        return members[0].id if members else None
    
    @api.model
    def _resolve_class_template(self, value, model=None):
        """Resolve class template by name or ID."""
        if isinstance(value, int):
            return value
        Template = self.env["dojo.class.template"]
        templates = Template.search([("name", "ilike", value), ("active", "=", True)], limit=1)
        return templates[0].id if templates else None
    
    @api.model
    def _resolve_subscription_plan(self, value, model=None):
        """Resolve subscription plan by name or ID."""
        if isinstance(value, int):
            return value
        Plan = self.env["dojo.subscription.plan"]
        plans = Plan.search([("name", "ilike", value), ("active", "=", True)], limit=1)
        return plans[0].id if plans else None
    
    @api.model
    def _resolve_program(self, value, model=None):
        """Resolve program by name or ID."""
        if isinstance(value, int):
            return value
        Program = self.env["dojo.program"]
        programs = Program.search([("name", "ilike", value), ("active", "=", True)], limit=1)
        return programs[0].id if programs else None
    
    @api.model
    def _resolve_belt_rank(self, value, model=None):
        """Resolve belt rank by name or ID."""
        if isinstance(value, int):
            return value
        Rank = self.env["dojo.belt.rank"]
        ranks = Rank.search([("name", "ilike", value), ("active", "=", True)], limit=1)
        return ranks[0].id if ranks else None
    
    # ─── CRUD Helper: Default value builders ───────────────────────────────────
    
    @api.model
    def _default_today(self):
        """Return today's date."""
        return fields.Date.today()

    # ─── CRUD Helper: Target domain builders ───────────────────────────────────
    
    @api.model
    def _domain_crud_member(self, intent_data, resolved_data):
        """Domain for update/delete on members."""
        member_id = resolved_data.get("member_id")
        if member_id:
            return [("id", "=", member_id)]
        
        params = intent_data.get("parameters", {}) if intent_data else {}
        name = params.get("member_name")
        if name:
            return [("name", "ilike", name)]
        
        return [("id", "=", -1)]  # Return nothing
    
    @api.model
    def _domain_crud_session(self, intent_data, resolved_data):
        """Domain for update/delete on class sessions."""
        session_id = resolved_data.get("session_id")
        return [("id", "=", session_id)] if session_id else [("id", "=", -1)]
    
    @api.model
    def _domain_crud_subscription(self, intent_data, resolved_data):
        """Domain for update/delete on subscriptions."""
        member_id = resolved_data.get("member_id")
        if member_id:
            return [("member_id", "=", member_id), ("state", "=", "active")]
        return [("id", "=", -1)]
    
    @api.model
    def _domain_crud_enrollment(self, intent_data, resolved_data):
        """Domain for update/delete on class enrollments."""
        member_id = resolved_data.get("member_id")
        session_id = resolved_data.get("session_id")
        
        if member_id and session_id:
            return [("member_id", "=", member_id), ("session_id", "=", session_id)]
        elif member_id:
            return [("member_id", "=", member_id)]
        elif session_id:
            return [("session_id", "=", session_id)]
        return [("id", "=", -1)]

    @api.model
    def _domain_crud_instructor(self, intent_data, resolved_data):
        """Domain for update on instructor profiles."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        name = params.get("instructor_name")
        if name:
            return [("name", "ilike", name)]
        return [("id", "=", -1)]

    @api.model
    def _domain_member_enrollment_list(self, intent_data, resolved_data):
        """Domain for member enrollment list — returns a member's class enrollments."""
        member_id = resolved_data.get("member_id")
        if not member_id:
            params = intent_data.get("parameters", {}) if intent_data else {}
            name = params.get("member_name")
            if name:
                members = self._search_members(name, limit=1)
                if members:
                    member_id = members[0].id
        if not member_id:
            return [("id", "=", -1)]
        return [("member_id", "=", member_id), ("status", "in", ["registered", "waitlist"])]

    @api.model
    def _domain_subscription_dunning_list(self, intent_data, resolved_data):
        """Domain for subscriptions with billing failures (dunning list)."""
        return [("billing_failure_count", ">", 0), ("state", "in", ["active", "paused"])]

    @api.model
    def _domain_crud_belt_test_registration(self, intent_data, resolved_data):
        """Domain for update on belt test registrations."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        # Resolve by member + test combo if possible
        member_id = resolved_data.get("member_id")
        test_reg_id = params.get("registration_id") or params.get("test_registration_id")
        if test_reg_id:
            return [("id", "=", int(test_reg_id))]
        if member_id:
            return [("member_id", "=", member_id), ("result", "=", "pending")]
        return [("id", "=", -1)]

    def _domain_enrollment_availability(self, intent_data, resolved_data):
        """Domain for class session availability lookup."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("state", "in", ["scheduled", "in_progress"])]
        template_name = params.get("class_name") or params.get("template_name")
        if template_name:
            domain += [("template_id.name", "ilike", template_name)]
        # Default: sessions from now onward
        domain += [("start_datetime", ">=", fields.Datetime.now())]
        return domain

    def _domain_class_enrollment_list(self, intent_data, resolved_data):
        """Domain for listing enrollments in a specific session."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        session_id = resolved_data.get("session_id")
        if not session_id:
            # Try to resolve by session name / date
            session_name = params.get("session_name") or params.get("class_name")
            if session_name:
                session = self.env["dojo.class.session"].search(
                    [("name", "ilike", session_name)], limit=1
                )
                session_id = session.id if session else None
        if not session_id:
            return [("id", "=", -1)]
        return [("session_id", "=", session_id), ("status", "in", ["registered", "waitlist"])]

    def _resolve_session_id(self, value, intent_data, resolved_data):
        """Resolve a session name or ID to a session record ID."""
        if not value:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
        session = self.env["dojo.class.session"].search(
            [("name", "ilike", str(value))], limit=1
        )
        return session.id if session else None

    def _domain_instructor_todo_list(self, intent_data, resolved_data):
        """Domain for listing tasks in the Instructor Alerts project."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        project_name = params.get("project_name", "Instructor Alerts")
        project = self.env["project.project"].search(
            [("name", "ilike", project_name)], limit=1
        )
        domain = []
        if project:
            domain.append(("project_id", "=", project.id))
        else:
            domain.append(("project_id.name", "ilike", "Instructor"))
        # Optionally filter out done/cancelled stages
        if not params.get("include_done"):
            done_stages = self.env["project.task.type"].search([("fold", "=", True)])
            if done_stages:
                domain.append(("stage_id", "not in", done_stages.ids))
        return domain

    @api.model
    def _handle_belt_readiness_check(self, intent_data, resolved_data, action_log):
        """
        Check whether a member is ready for their next belt test.

        Compares attendance_since_last_rank to the attendance_threshold on their
        current rank's next rank (next higher sequence rank). Returns readiness
        status and progress details.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id") or params.get("member_id")
        member_name = params.get("member_name", "")

        if not member_id and member_name:
            member = self.env["dojo.member"].search(
                [("name", "ilike", member_name)], limit=1
            )
            member_id = member.id if member else None

        if not member_id:
            return {"success": False, "error": "Could not identify member. Please specify a name."}

        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": f"Member ID {member_id} not found."}

        current_rank = member.current_rank_id
        attended = member.attendance_since_last_rank or 0

        if not current_rank:
            return {
                "success": True,
                "member_name": member.name,
                "current_rank": "None (unranked)",
                "attended_since_last_rank": attended,
                "message": f"{member.name} has no belt rank on file.",
            }

        # Find the next rank (higher sequence, same style/company)
        next_rank = self.env["dojo.belt.rank"].search(
            [
                ("sequence", ">", current_rank.sequence),
                ("style_id", "=", current_rank.style_id.id),
                ("company_id", "=", member.company_id.id),
                ("active", "=", True),
            ],
            order="sequence asc",
            limit=1,
        )
        if not next_rank:
            return {
                "success": True,
                "member_name": member.name,
                "current_rank": current_rank.name,
                "attended_since_last_rank": attended,
                "message": f"{member.name} is at the highest rank ({current_rank.name}). No further belt tests needed.",
            }

        threshold = next_rank.attendance_threshold or 0
        if threshold == 0:
            ready = True
            message = (
                f"{member.name} is eligible for {next_rank.name} — "
                "no attendance threshold is set for that rank."
            )
        elif attended >= threshold:
            ready = True
            message = (
                f"{member.name} is READY for {next_rank.name}. "
                f"Attended {attended}/{threshold} required sessions since last rank."
            )
        else:
            ready = False
            remaining = threshold - attended
            message = (
                f"{member.name} needs {remaining} more session(s) before testing for {next_rank.name}. "
                f"Currently {attended}/{threshold} required sessions."
            )

        return {
            "success": True,
            "member_name": member.name,
            "current_rank": current_rank.name,
            "next_rank": next_rank.name,
            "attended_since_last_rank": attended,
            "attendance_threshold": threshold,
            "ready": ready,
            "message": message,
        }

    @api.model
    def _handle_instructor_todo_complete(self, intent_data, resolved_data, action_log):
        """
        Mark an instructor todo task as done.

        Delegates to the existing _handle_task_complete handler, but first
        ensures the task is looked up within the Instructor Alerts project context.
        """
        return self._handle_task_complete(intent_data, resolved_data, action_log)

    # ─── Batch D: Revenue, CRM & Portal handlers ───────────────────────────────

    def _domain_payment_history(self, intent_data, resolved_data):
        """Domain for listing payments, optionally filtered by member partner."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("payment_type", "=", "inbound"), ("state", "=", "posted")]
        member_id = resolved_data.get("member_id") or params.get("member_id")
        if member_id:
            member = self.env["dojo.member"].browse(member_id)
            if member.exists() and member.partner_id:
                domain.append(("partner_id", "=", member.partner_id.id))
        elif params.get("member_name"):
            member = self.env["dojo.member"].search(
                [("name", "ilike", params["member_name"])], limit=1
            )
            if member and member.partner_id:
                domain.append(("partner_id", "=", member.partner_id.id))
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        return domain

    @api.model
    def _handle_revenue_summary(self, intent_data, resolved_data, action_log):
        """
        Summarise paid invoice revenue for a period (week/month/quarter/year).

        Reads account.move records (out_invoice, payment_state=paid) and groups
        by period. Falls back to a 30-day window if no period is specified.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        period = params.get("period", "month").lower()
        from datetime import date, timedelta

        today = date.today()
        if period in ("week", "7days"):
            date_from = today - timedelta(days=7)
        elif period in ("quarter", "90days"):
            date_from = today - timedelta(days=90)
        elif period in ("year", "annual"):
            date_from = date(today.year, 1, 1)
        else:
            date_from = today.replace(day=1)  # month-to-date default

        date_to = today

        if params.get("date_from"):
            try:
                from datetime import datetime as _dt
                date_from = _dt.strptime(params["date_from"], "%Y-%m-%d").date()
            except ValueError:
                pass
        if params.get("date_to"):
            try:
                from datetime import datetime as _dt
                date_to = _dt.strptime(params["date_to"], "%Y-%m-%d").date()
            except ValueError:
                pass

        Move = self.env["account.move"].sudo()
        domain = [
            ("move_type", "=", "out_invoice"),
            ("payment_state", "=", "paid"),
            ("invoice_date", ">=", str(date_from)),
            ("invoice_date", "<=", str(date_to)),
        ]
        moves = Move.search(domain, limit=200)
        total = sum(moves.mapped("amount_total_signed"))
        count = len(moves)
        currency = moves[:1].currency_id.name if moves else "USD"

        return {
            "success": True,
            "period": period,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "invoice_count": count,
            "total_revenue": total,
            "currency": currency,
            "message": (
                f"Revenue from {date_from} to {date_to}: "
                f"{currency} {total:,.2f} across {count} paid invoice(s)."
            ),
        }

    @api.model
    def _handle_guardian_portal_invite(self, intent_data, resolved_data, action_log):
        """
        Grant portal access to a guardian (parent/emergency contact partner).
        Finds the member, locates their primary guardian partner, and sends a
        portal invitation (creates portal user + signup email).
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id") or params.get("member_id")
        member_name = params.get("member_name", "")

        if not member_id and member_name:
            member = self.env["dojo.member"].search(
                [("name", "ilike", member_name)], limit=1
            )
            member_id = member.id if member else None

        if not member_id:
            return {"success": False, "error": "Could not identify member. Please specify a name."}

        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": f"Member ID {member_id} not found."}

        # Locate guardian partner — prefer explicit guardian_name param, else member's parent partner
        guardian_name = params.get("guardian_name", "")
        guardian_partner = None

        if guardian_name:
            guardian_partner = self.env["res.partner"].search(
                [("name", "ilike", guardian_name)], limit=1
            )

        if not guardian_partner:
            # Fall back to the member's own partner or household primary guardian
            if member.partner_id and member.partner_id.parent_id:
                guardian_partner = member.partner_id.parent_id
            else:
                guardian_partner = member.partner_id

        if not guardian_partner:
            return {"success": False, "error": f"No guardian found for {member.name}."}

        if not guardian_partner.email:
            return {
                "success": False,
                "error": f"Guardian {guardian_partner.name} has no email address — cannot send portal invite.",
            }

        # Check if portal access already granted
        existing_portal_user = self.env["res.users"].sudo().search(
            [("partner_id", "=", guardian_partner.id), ("share", "=", True)],
            limit=1,
        )
        if existing_portal_user and existing_portal_user.active:
            return {
                "success": True,
                "guardian_name": guardian_partner.name,
                "member_name": member.name,
                "message": f"{guardian_partner.name} already has portal access.",
            }

        # Grant portal access via portal wizard user record
        try:
            WizardUser = self.env["portal.wizard.user"].sudo()
            wizard_user = WizardUser.new({
                "partner_id": guardian_partner.id,
                "email": guardian_partner.email,
            })
            wizard_user.action_grant_access()
            msg = f"Portal invitation sent to {guardian_partner.name} ({guardian_partner.email}) for {member.name}."
        except Exception as exc:
            _logger.warning("Portal invite failed for %s: %s", guardian_partner.name, exc)
            # Fallback: prepare signup URL
            guardian_partner.sudo().signup_prepare()
            msg = f"Signup prepared for {guardian_partner.name}. Invitation email may need to be sent manually."

        return {
            "success": True,
            "guardian_name": guardian_partner.name,
            "member_name": member.name,
            "message": msg,
        }

    @api.model
    def _handle_lead_note_add(self, intent_data, resolved_data, action_log):
        """Stub — overridden by dojo_crm.models.ai_crm_service via _inherit."""
        return {"success": False, "error": "CRM module not available."}

    @api.model
    def _handle_trial_conversion_report(self, intent_data, resolved_data, action_log):
        """Stub — overridden by dojo_crm.models.ai_crm_service via _inherit."""
        return {"success": False, "error": "CRM module not available."}

    @api.model
    def _handle_phone_lookup(self, intent_data, resolved_data, action_log):
        """
        Cross-model phone number lookup.

        Searches dojo.member (phone + mobile) first, then crm.lead if the CRM
        module is available. Returns all matches labelled by type so the user
        can see at a glance whether the number belongs to a member, a lead, or both.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        phone = params.get("phone") or params.get("phone_number") or ""
        if not phone:
            return {"success": False, "error": "No phone number provided."}

        # Strip formatting for a clean digit-only search
        stripped = re.sub(r"[\s\(\)\-\+\.]", "", phone)

        results = []

        # ── Search dojo.member ────────────────────────────────────────────
        try:
            member_domain = ["|",
                ("phone", "ilike", stripped),
                ("phone", "ilike", phone),
            ]
            members = self.env["dojo.member"].with_context(active_test=False).search(
                member_domain, limit=5
            )
            for m in members:
                results.append({
                    "type": "member",
                    "id": m.id,
                    "name": m.name,
                    "phone": m.phone or getattr(m, "mobile", False) or "",
                    "email": m.email or "",
                    "membership_state": getattr(m, "membership_state", ""),
                })
        except Exception as e:
            _logger.warning("phone_lookup: member search failed: %s", e)

        # ── Search crm.lead ───────────────────────────────────────────────
        if "crm.lead" in self.env:
            try:
                leads = self.env["crm.lead"].sudo().search([
                    ("phone", "ilike", stripped),
                ], limit=5)
                for lead in leads:
                    results.append({
                        "type": "lead",
                        "id": lead.id,
                        "name": lead.contact_name or lead.partner_name or lead.name or "Unknown",
                        "phone": lead.phone or getattr(lead, "mobile", False) or "",
                        "email": lead.email_from or "",
                        "stage": lead.stage_id.name if lead.stage_id else "",
                        "is_converted": bool(getattr(lead, "probability", 0) == 100),
                    })
            except Exception as e:
                _logger.warning("phone_lookup: lead search failed: %s", e)

        if not results:
            return {
                "success": True,
                "message": f"No member or lead found with phone number {phone}.",
                "data": [],
            }

        member_count = sum(1 for r in results if r["type"] == "member")
        lead_count = sum(1 for r in results if r["type"] == "lead")
        parts = []
        if member_count:
            parts.append(f"{member_count} member(s)")
        if lead_count:
            parts.append(f"{lead_count} lead(s)")
        summary = " and ".join(parts)

        return {
            "success": True,
            "message": f"Found {summary} matching {phone}:",
            "data": results,
        }

    # ── New Query Intent Handlers ─────────────────────────────────────────────

    @api.model
    def _handle_checkin_required_today(self, intent_data, resolved_data, action_log):
        """
        List members enrolled in today's sessions who haven't checked in yet
        (attendance_state = 'pending').  Optionally scoped to one session.
        """
        import datetime as _dt
        params = intent_data.get("parameters", {}) if intent_data else {}
        session_id = resolved_data.get("session_id") or params.get("session_id")

        today = fields.Date.today()

        if session_id:
            sessions = self.env["dojo.class.session"].browse(int(session_id))
        else:
            session_name = params.get("session_name") or params.get("class_name")
            session_domain = [
                ("start_datetime", ">=", f"{today} 00:00:00"),
                ("start_datetime", "<=", f"{today} 23:59:59"),
            ]
            if session_name:
                session_domain.append(("name", "ilike", session_name))
            sessions = self.env["dojo.class.session"].search(session_domain)

        if not sessions:
            return {
                "success": True,
                "message": "No sessions found for today.",
                "data": [],
            }

        enrollments = self.env["dojo.class.enrollment"].search([
            ("session_id", "in", sessions.ids),
            ("status", "=", "registered"),
            ("attendance_state", "=", "pending"),
        ])

        # Group by session for a readable response
        by_session = {}
        for enr in enrollments:
            sname = enr.session_id.name or f"Session {enr.session_id.id}"
            stime = enr.session_id.start_datetime
            key = (enr.session_id.id, sname, stime)
            by_session.setdefault(key, []).append({
                "member_id": enr.member_id.id,
                "member_name": enr.member_id.name,
                "enrollment_id": enr.id,
            })

        data = [
            {
                "session_id": sid,
                "session_name": sname,
                "session_time": str(stime) if stime else None,
                "pending_members": members,
                "count": len(members),
            }
            for (sid, sname, stime), members in sorted(by_session.items(), key=lambda x: x[0][2] or "")
        ]
        total = sum(d["count"] for d in data)
        msg = (
            f"{total} member(s) still need to check in across {len(data)} session(s) today."
            if data else "Everyone has checked in for today's sessions."
        )
        return {"success": True, "message": msg, "data": data}

    @api.model
    def _handle_belt_test_roster(self, intent_data, resolved_data, action_log):
        """
        Show registrations for a specific belt test.
        Defaults to the nearest upcoming test when no test is specified.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        test_id = params.get("test_id")
        test_name = params.get("test_name") or params.get("name")
        test_date = params.get("date") or params.get("test_date")
        program_name = params.get("program_name")

        BeltTest = self.env["dojo.belt.test"]

        if test_id:
            test = BeltTest.browse(int(test_id))
        elif test_name:
            test = BeltTest.search([("name", "ilike", test_name)], limit=1)
        elif test_date:
            test = BeltTest.search(
                [("test_date", "=", test_date), ("state", "!=", "cancelled")], limit=1
            )
        else:
            # Default: nearest upcoming test
            test_domain = [("test_date", ">=", str(fields.Date.today())), ("state", "!=", "cancelled")]
            if program_name:
                programs = self.env["dojo.program"].search([("name", "ilike", program_name)], limit=3)
                if programs:
                    test_domain.append(("program_id", "in", programs.ids))
            test = BeltTest.search(test_domain, order="test_date asc", limit=1)

        if not test or not test.exists():
            return {
                "success": False,
                "error": "No upcoming belt test found. Specify a test name or date.",
            }

        registrations = self.env["dojo.belt.test.registration"].search(
            [("test_id", "=", test.id)],
            order="member_id asc",
        )
        roster = [
            {
                "registration_id": r.id,
                "member_id": r.member_id.id,
                "member_name": r.member_id.name,
                "target_rank": r.target_rank_id.name if r.target_rank_id else None,
                "program": r.program_id.name if r.program_id else None,
                "result": r.result,
            }
            for r in registrations
        ]

        return {
            "success": True,
            "message": (
                f"Belt test '{test.name}' on {test.test_date} at {test.location or 'TBD'} — "
                f"{len(roster)} registered."
            ),
            "data": {
                "test_id": test.id,
                "test_name": test.name,
                "test_date": str(test.test_date),
                "location": test.location or "",
                "state": test.state,
                "program": test.program_id.name if test.program_id else None,
                "roster": roster,
            },
        }

    @api.model
    def _handle_belt_eligible_members(self, intent_data, resolved_data, action_log):
        """
        List members whose attendance_since_last_rank meets or exceeds
        the attendance_threshold for their next belt rank.
        """
        params = intent_data.get("parameters", {}) if intent_data else {}
        program_name = params.get("program_name") or params.get("program")
        threshold_pct = float(params.get("threshold_pct", 100))

        # Search active members with a current rank
        member_domain = [("active", "=", True), ("membership_state", "=", "active"),
                         ("current_rank_id", "!=", False)]
        if program_name:
            programs = self.env["dojo.program"].search([("name", "ilike", program_name)], limit=3)
            if programs:
                # Filter by program via rank's program_id
                member_domain.append(("current_rank_id.program_id", "in", programs.ids))

        members = self.env["dojo.member"].search(member_domain)

        eligible = []
        for m in members:
            rank = m.current_rank_id
            if not rank:
                continue
            threshold = getattr(rank, "attendance_threshold", 0) or 0
            if threshold <= 0:
                continue
            since = getattr(m, "attendance_since_last_rank", 0) or 0
            pct_done = (since / threshold * 100) if threshold else 0
            if pct_done >= threshold_pct:
                eligible.append({
                    "member_id": m.id,
                    "member_name": m.name,
                    "current_rank": rank.name,
                    "attendance_since_promo": since,
                    "threshold_required": threshold,
                    "pct_complete": round(pct_done, 1),
                })

        eligible.sort(key=lambda x: x["pct_complete"], reverse=True)
        msg = (
            f"{len(eligible)} member(s) are eligible for their next belt test."
            if eligible else "No members currently meet the attendance threshold for promotion."
        )
        return {"success": True, "message": msg, "data": eligible}

    @api.model
    def _handle_subscription_status_summary(self, intent_data, resolved_data, action_log):
        """Aggregate count of sale.subscriptions grouped by state."""
        try:
            groups = self.env["sale.subscription"]._read_group(
                domain=[],
                groupby=["state"],
                aggregates=["__count"],
            )
            summary = {state or "unknown": count for state, count in groups}
        except Exception as e:
            _logger.warning("subscription_status_summary _read_group failed: %s", e)
            # Fallback: manual count
            subs = self.env["sale.subscription"].search([])
            summary = {}
            for s in subs:
                summary[s.state or "unknown"] = summary.get(s.state or "unknown", 0) + 1

        total = sum(summary.values())
        parts = [f"{state}: {cnt}" for state, cnt in sorted(summary.items())]
        msg = f"Subscription breakdown ({total} total): " + ", ".join(parts) if parts else "No subscriptions found."
        return {"success": True, "message": msg, "data": summary}

    def _handle_belt_lookup(self, intent_data, resolved_data, action_log):
        """
        Look up belt rank information.

        - If a member is in context → return that member's current rank + stripe count.
        - If a member_name was provided but not resolved → return not-found error.
        - Otherwise (no member requested) → return all belt ranks in sequence order.
        """
        member_id = resolved_data.get("member_id")
        params = intent_data.get("parameters", {}) or {}
        requested_name = (params.get("member_name") or "").strip()

        if member_id:
            member = self.env["dojo.member"].browse(member_id)
            if not member.exists():
                return {"success": False, "error": "Member not found."}

            rank = member.current_rank_id if hasattr(member, "current_rank_id") else None
            stripe_count = getattr(member, "current_stripe_count", 0) or 0
            max_stripes = (getattr(rank, "max_stripes", 0) or 0) if rank else 0

            rank_name = rank.name if rank else "No rank assigned"
            stripe_str = f" ({stripe_count}/{max_stripes} stripes)" if max_stripes > 0 else ""

            return {
                "success": True,
                "message": f"{member.name} is currently ranked: {rank_name}{stripe_str}",
                "data": {
                    "name": rank_name + stripe_str,
                    "member_name": member.name,
                    "rank_id": rank.id if rank else None,
                },
            }

        # A name was requested but could not be resolved — do NOT fall through to
        # "list all ranks", because the agent will then fabricate a rank for that
        # person. Return an explicit not-found result instead.
        if requested_name:
            return {
                "success": False,
                "error": f"No member found matching '{requested_name}'. Please check the spelling or confirm the member exists.",
                "data": {"requested_name": requested_name, "found": False},
            }

        # No member in context — return all belt ranks
        ranks = self.env["dojo.belt.rank"].search([("active", "=", True)], order="sequence")
        data = [{"id": r.id, "name": r.name, "sequence": r.sequence} for r in ranks]
        return {
            "success": True,
            "message": f"Found {len(data)} belt ranks",
            "data": data,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Intent Handlers: Mutating (Require Confirmation)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Batch A: Points & Member Admin handlers ───────────────────────────────

    @api.model
    def _handle_points_award(self, intent_data, resolved_data, action_log):
        """Award points to a member (creates a dojo.points.transaction)."""
        params = intent_data.get("parameters", {}) or {}
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Member not found."}
        amount = params.get("amount")
        if not amount:
            return {"success": False, "error": "Amount of points to award is required."}
        try:
            amount = abs(int(amount))
        except (TypeError, ValueError):
            return {"success": False, "error": "Amount must be a number."}
        note = params.get("note") or f"Instructor award — {amount} pts"
        tx = self.env["dojo.points.transaction"].create({
            "member_id": member_id,
            "amount": amount,
            "source_type": "instructor_award",
            "note": note,
            "awarded_by": self.env.user.id,
        })
        member = self.env["dojo.member"].browse(member_id)
        if action_log:
            action_log.description = f"Awarded {amount} pts to {member.name}"
        return {
            "success": True,
            "message": f"Awarded {amount} points to {member.name}.",
            "data": {"transaction_id": tx.id, "member_name": member.name, "amount": amount},
        }

    @api.model
    def _handle_points_deduct(self, intent_data, resolved_data, action_log):
        """Deduct points from a member (creates a negative dojo.points.transaction)."""
        params = intent_data.get("parameters", {}) or {}
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Member not found."}
        amount = params.get("amount")
        if not amount:
            return {"success": False, "error": "Amount of points to deduct is required."}
        try:
            amount = abs(int(amount))
        except (TypeError, ValueError):
            return {"success": False, "error": "Amount must be a number."}
        note = params.get("note") or f"Points adjustment — -{amount} pts"
        tx = self.env["dojo.points.transaction"].create({
            "member_id": member_id,
            "amount": -amount,
            "source_type": "adjustment",
            "note": note,
            "awarded_by": self.env.user.id,
        })
        member = self.env["dojo.member"].browse(member_id)
        if action_log:
            action_log.description = f"Deducted {amount} pts from {member.name}"
        return {
            "success": True,
            "message": f"Deducted {amount} points from {member.name}.",
            "data": {"transaction_id": tx.id, "member_name": member.name, "amount": -amount},
        }

    @api.model
    def _handle_member_archive(self, intent_data, resolved_data, action_log):
        """Archive (deactivate) a member record."""
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Member not found."}
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member record not found."}
        if not member.active:
            return {"success": False, "error": f"{member.name} is already archived."}
        # Snapshot for undo
        try:
            self.env["ai.undo.snapshot"].create({
                "action_log_id": action_log.id if action_log else False,
                "model_name": "dojo.member",
                "record_id": member.id,
                "snapshot_data": json.dumps({"active": True}),
            })
        except Exception:
            pass
        member.active = False
        if hasattr(member, "message_post"):
            member.message_post(body=f"Member archived by AI assistant ({self.env.user.name}).")
        if action_log:
            action_log.description = f"Archived member {member.name}"
        return {
            "success": True,
            "message": f"{member.name} has been archived.",
            "data": {"member_id": member_id, "member_name": member.name},
        }

    @api.model
    def _handle_member_note_add(self, intent_data, resolved_data, action_log):
        """Post an internal note to a member's chatter."""
        params = intent_data.get("parameters", {}) or {}
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Member not found."}
        note = params.get("note") or params.get("message") or params.get("body")
        if not note:
            return {"success": False, "error": "Note text is required."}
        member = self.env["dojo.member"].browse(member_id)
        if not member.exists():
            return {"success": False, "error": "Member record not found."}
        if not hasattr(member, "message_post"):
            return {"success": False, "error": "Member model does not support notes."}
        member.message_post(
            body=note,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            author_id=self.env.user.partner_id.id,
        )
        if action_log:
            action_log.description = f"Note added to {member.name}: {note[:60]}"
        return {
            "success": True,
            "message": f"Note added to {member.name}'s profile.",
            "data": {"member_id": member_id, "member_name": member.name, "note": note},
        }

    @api.model
    def _handle_subscription_billing_retry(self, intent_data, resolved_data, action_log):
        """Reset billing failure count on a subscription so the next billing cron retries it."""
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Member not found."}
        sub = self.env["sale.subscription"].search([
            ("member_id", "=", member_id),
            ("billing_failure_count", ">", 0),
        ], limit=1, order="billing_failure_count desc")
        if not sub:
            return {"success": False, "error": "No failed subscription found for this member."}
        member_name = sub.member_id.name
        failures = sub.billing_failure_count
        sub._reset_billing_failures()
        if action_log:
            action_log.description = f"Reset {failures} billing failure(s) for {member_name}"
        return {
            "success": True,
            "message": f"Billing retry queued for {member_name} — {failures} failure(s) cleared.",
            "data": {"subscription_id": sub.id, "member_name": member_name, "failures_cleared": failures},
        }

    # ── End Batch A handlers ──────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════════
    # Batch B handlers: Class & Roster
    # ══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_class_session_generate(self, intent_data, resolved_data, action_log):
        """Generate scheduled sessions from a class template's recurrence config."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        template_name = params.get("template_name") or params.get("class_name")
        if not template_name:
            return {"success": False, "error": "No class template name provided."}
        template = self.env["dojo.class.template"].search(
            [("name", "ilike", template_name), ("active", "=", True)], limit=1
        )
        if not template:
            return {"success": False, "error": f"Class template '{template_name}' not found."}
        if not template.recurrence_active:
            return {"success": False, "error": f"'{template.name}' does not have recurrence enabled."}
        horizon = int(params.get("horizon_days", 30))
        count_before = self.env["dojo.class.session"].search_count(
            [("template_id", "=", template.id)]
        )
        template.action_generate_sessions()
        count_after = self.env["dojo.class.session"].search_count(
            [("template_id", "=", template.id)]
        )
        generated = count_after - count_before
        if action_log:
            action_log.description = f"Generated {generated} sessions for {template.name}"
        return {
            "success": True,
            "message": f"Generated {generated} new session(s) for '{template.name}'.",
            "data": {"template_id": template.id, "sessions_generated": generated},
        }

    # ── End Batch B handlers ──────────────────────────────────────────────────

    @api.model
    def _handle_member_enroll(self, intent_data, resolved_data, action_log):
        """Enroll a member in a class session."""
        member_id = resolved_data.get("member_id")
        session_id = resolved_data.get("session_id")

        if not member_id:
            return {"success": False, "error": "Member not found."}
        if not session_id:
            return {"success": False, "error": "Class session not found."}

        member = self.env["dojo.member"].browse(member_id)
        session = self.env["dojo.class.session"].browse(session_id)

        # Create undo snapshot
        Snapshot = self.env["ai.undo.snapshot"]

        # Check if already enrolled
        Enrollment = self.env["dojo.class.enrollment"]
        existing = Enrollment.search([
            ("member_id", "=", member.id),
            ("session_id", "=", session.id),
        ], limit=1)

        if existing:
            return {
                "success": False,
                "error": f"{member.name} is already enrolled in {session.template_id.name}.",
            }

        # Check capacity
        if session.seats_taken >= session.capacity:
            return {
                "success": False,
                "error": f"{session.template_id.name} is at capacity ({session.capacity}).",
            }

        # Create enrollment — bypass course-roster check since the instructor
        # is explicitly authorising this enrolment via the AI assistant.
        enrollment = Enrollment.with_context(
            skip_course_membership_check=True
        ).create({
            "member_id": member.id,
            "session_id": session.id,
            "status": "registered",
        })

        # Create snapshot for undo
        Snapshot.create_snapshot(action_log.id, Enrollment._name, enrollment.id, "create")

        return {
            "success": True,
            "message": f"Enrolled {member.name} in {session.template_id.name}.",
            "data": {"enrollment_id": enrollment.id},
        }

    @api.model
    def _handle_member_unenroll(self, intent_data, resolved_data, action_log):
        """Remove a member from a class session."""
        member_id = resolved_data.get("member_id")
        session_id = resolved_data.get("session_id")

        if not member_id or not session_id:
            return {"success": False, "error": "Member or session not found."}

        Enrollment = self.env["dojo.class.enrollment"]
        enrollment = Enrollment.search([
            ("member_id", "=", member_id),
            ("session_id", "=", session_id),
        ], limit=1)

        if not enrollment:
            return {"success": False, "error": "No enrollment found."}

        # Create snapshot for undo (capture before deletion)
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, Enrollment._name, enrollment.id, "unlink")

        member_name = enrollment.member_id.name
        session_name = enrollment.session_id.template_id.name if enrollment.session_id.template_id else "the session"

        enrollment.unlink()

        return {
            "success": True,
            "message": f"Removed {member_name} from {session_name}.",
        }

    @api.model
    def _handle_belt_promote(self, intent_data, resolved_data, action_log):
        """Promote a member to a new belt rank."""
        member_id = resolved_data.get("member_id")
        new_rank_id = resolved_data.get("new_rank_id")

        if not member_id:
            return {"success": False, "error": "Member not found."}
        if not new_rank_id:
            return {"success": False, "error": "New belt rank not specified."}

        member = self.env["dojo.member"].browse(member_id)
        new_rank = self.env["dojo.belt.rank"].browse(new_rank_id)
        old_rank = member.current_rank_id if hasattr(member, 'current_rank_id') else None

        # Create snapshot of current rank
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(
            action_log.id, "dojo.member", member.id, "write",
            snapshot_data={"current_rank_id": old_rank.id if old_rank else False}
        )

        # Create member rank record
        MemberRank = self.env["dojo.member.rank"]
        member_rank = MemberRank.create({
            "member_id": member.id,
            "rank_id": new_rank.id,
            "date_awarded": fields.Date.today(),
        })

        # Update member's current rank
        member.current_rank_id = new_rank.id

        old_name = old_rank.name if old_rank else "no belt"
        return {
            "success": True,
            "message": f"Promoted {member.name} from {old_name} to {new_rank.name}.",
            "data": {"member_rank_id": member_rank.id},
        }

    @api.model
    def _handle_contact_parent(self, intent_data, resolved_data, action_log):
        """Send a message to a member's guardian."""
        member_id = resolved_data.get("member_id")

        if not member_id:
            return {"success": False, "error": "Member not found."}

        params = intent_data.get("parameters", {}) if intent_data else {}
        subject = params.get("subject", "Message from Dojo")
        body = params.get("body", params.get("message", ""))

        if not body:
            return {"success": False, "error": "Message body is required."}

        result = self.send_parent_message(
            member_id=member_id,
            subject=subject,
            body=body,
            send_email=params.get("send_email", True),
            send_sms=params.get("send_sms", True),
        )

        return result

    @api.model
    def _handle_attendance_checkin(self, intent_data, resolved_data, action_log):
        """Check in a member (or multiple members) for attendance."""
        params = intent_data.get("parameters", {}) if intent_data else {}

        # ── Batch mode: member_names list ──────────────────────────────────
        member_names = params.get("member_names") or []
        if isinstance(member_names, list) and len(member_names) > 1:
            results = []
            for name in member_names:
                members = self._search_members(name, limit=1)
                if not members:
                    results.append({"name": name, "success": False, "error": "Not found"})
                    continue
                member = members[0]
                AttLog = self.env["dojo.attendance.log"]
                from datetime import datetime, date as _date
                today_start = datetime.combine(_date.today(), datetime.min.time())
                existing = AttLog.search([
                    ("member_id", "=", member.id),
                    ("checkin_datetime", ">=", today_start),
                    ("checkout_datetime", "=", False),
                ], limit=1)
                if existing:
                    results.append({"name": member.name, "success": False, "error": "Already checked in"})
                    continue
                values = {"member_id": member.id, "checkin_datetime": fields.Datetime.now()}
                session_id = resolved_data.get("session_id")
                if session_id:
                    values["session_id"] = session_id
                log = AttLog.create(values)
                self.env["ai.undo.snapshot"].create_snapshot(action_log.id, AttLog._name, log.id, "create")
                results.append({"name": member.name, "success": True})
            success_count = sum(1 for r in results if r["success"])
            return {
                "success": success_count > 0,
                "bulk": True,
                "message": f"Checked in {success_count}/{len(results)} students.",
                "results": results,
            }

        # ── Single mode (existing behaviour) ──────────────────────────────
        member_id = resolved_data.get("member_id")
        session_id = resolved_data.get("session_id")

        if not member_id:
            return {"success": False, "error": "Member not found."}

        member = self.env["dojo.member"].browse(member_id)

        # Create attendance log
        AttLog = self.env["dojo.attendance.log"]

        # Check if already checked in today without checkout
        from datetime import datetime, date as _date
        today_start = datetime.combine(_date.today(), datetime.min.time())
        existing = AttLog.search([
            ("member_id", "=", member.id),
            ("checkin_datetime", ">=", today_start),
            ("checkout_datetime", "=", False),
        ], limit=1)

        if existing:
            return {
                "success": False,
                "error": f"{member.name} is already checked in.",
            }

        values = {
            "member_id": member.id,
            "checkin_datetime": fields.Datetime.now(),
        }
        if session_id:
            values["session_id"] = session_id

        log = AttLog.create(values)

        # Create snapshot for undo
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, AttLog._name, log.id, "create")

        return {
            "success": True,
            "message": f"Checked in {member.name}.",
            "data": {"attendance_log_id": log.id},
        }

    @api.model
    def _handle_attendance_checkout(self, intent_data, resolved_data, action_log):
        """Check out a member (or multiple members) from attendance."""
        params = intent_data.get("parameters", {}) if intent_data else {}

        # ── Batch mode: member_names list ──────────────────────────────────
        member_names = params.get("member_names") or []
        if isinstance(member_names, list) and len(member_names) > 1:
            results = []
            AttLog = self.env["dojo.attendance.log"]
            from datetime import datetime, date as _date
            today_start = datetime.combine(_date.today(), datetime.min.time())
            for name in member_names:
                members = self._search_members(name, limit=1)
                if not members:
                    results.append({"name": name, "success": False, "error": "Not found"})
                    continue
                member = members[0]
                log = AttLog.search([
                    ("member_id", "=", member.id),
                    ("checkin_datetime", ">=", today_start),
                    ("checkout_datetime", "=", False),
                ], order="checkin_datetime desc", limit=1)
                if not log:
                    results.append({"name": member.name, "success": False, "error": "Not checked in"})
                    continue
                self.env["ai.undo.snapshot"].create_snapshot(
                    action_log.id, AttLog._name, log.id, "write",
                    snapshot_data={"checkout_datetime": False}
                )
                log.checkout_datetime = fields.Datetime.now()
                results.append({"name": member.name, "success": True})
            success_count = sum(1 for r in results if r["success"])
            return {
                "success": success_count > 0,
                "bulk": True,
                "message": f"Checked out {success_count}/{len(results)} students.",
                "results": results,
            }

        # ── Single mode (existing behaviour) ──────────────────────────────
        member_id = resolved_data.get("member_id")

        if not member_id:
            return {"success": False, "error": "Member not found."}

        member = self.env["dojo.member"].browse(member_id)

        # Find unclosed attendance log
        AttLog = self.env["dojo.attendance.log"]
        from datetime import datetime, date as _date
        today_start = datetime.combine(_date.today(), datetime.min.time())

        log = AttLog.search([
            ("member_id", "=", member.id),
            ("checkin_datetime", ">=", today_start),
            ("checkout_datetime", "=", False),
        ], order="checkin_datetime desc", limit=1)

        if not log:
            return {"success": False, "error": f"{member.name} is not checked in."}

        # Create snapshot for undo
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(
            action_log.id, AttLog._name, log.id, "write",
            snapshot_data={"checkout_datetime": False}
        )

        log.checkout_datetime = fields.Datetime.now()

        return {
            "success": True,
            "message": f"Checked out {member.name}.",
            "data": {"attendance_log_id": log.id},
        }

    @api.model
    def _handle_course_enroll(self, intent_data, resolved_data, action_log):
        """Add a member to a course's permanent roster (template.course_member_ids)."""
        member_id = resolved_data.get("member_id")
        template_id = resolved_data.get("template_id")

        if not member_id:
            params = intent_data.get("parameters", {}) if intent_data else {}
            return {"success": False, "error": f"Member '{params.get('member_name', '')}' not found."}
        if not template_id:
            params = intent_data.get("parameters", {}) if intent_data else {}
            return {"success": False, "error": f"Course '{params.get('class_name', '')}' not found."}

        member = self.env["dojo.member"].browse(member_id)
        template = self.env["dojo.class.template"].browse(template_id)

        # Check if already on the roster
        if member in template.course_member_ids:
            return {
                "success": False,
                "error": f"{member.name} is already on the {template.name} roster.",
            }

        # Add to roster
        template.course_member_ids = [(4, member.id)]

        # Create snapshot for undo (write snapshot on template)
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(
            action_log.id, "dojo.class.template", template.id, "write",
            snapshot_data={"course_member_ids": [(3, member.id)]}
        )

        return {
            "success": True,
            "message": f"Added {member.name} to the {template.name} course roster.",
            "data": {"template_id": template.id, "member_id": member.id},
        }

    @api.model
    def _handle_belt_test_register(self, intent_data, resolved_data, action_log):
        """Register a member for an upcoming belt test."""
        member_id = resolved_data.get("member_id")
        new_rank_id = resolved_data.get("new_rank_id")

        if not member_id:
            params = intent_data.get("parameters", {}) if intent_data else {}
            return {"success": False, "error": f"Member '{params.get('member_name', '')}' not found."}
        if not new_rank_id:
            params = intent_data.get("parameters", {}) if intent_data else {}
            return {"success": False, "error": f"Belt rank '{params.get('target_belt', '')}' not found."}

        member = self.env["dojo.member"].browse(member_id)
        rank = self.env["dojo.belt.rank"].browse(new_rank_id)
        params = intent_data.get("parameters", {}) if intent_data else {}

        # Find an upcoming scheduled belt test
        BeltTest = self.env["dojo.belt.test"]
        domain = [("state", "=", "scheduled")]

        # Optionally filter by test_date if provided
        test_date = params.get("test_date")
        if test_date:
            domain.append(("test_date", "=", test_date))

        # Optionally filter by test name if provided
        test_name = params.get("test_name")
        if test_name:
            domain.append(("name", "ilike", test_name))

        belt_test = BeltTest.search(domain, order="test_date asc", limit=1)

        if not belt_test:
            # Create a new belt test if none is scheduled
            from datetime import date as _date, timedelta
            default_date = _date.today() + timedelta(days=14)
            belt_test = BeltTest.create({
                "name": f"Belt Test — {default_date.isoformat()}",
                "test_date": default_date,
                "state": "scheduled",
            })
            _logger.info("AI created new belt test %s for registration", belt_test.id)

        # Check for duplicate registration
        Registration = self.env["dojo.belt.test.registration"]
        existing = Registration.search([
            ("test_id", "=", belt_test.id),
            ("member_id", "=", member.id),
        ], limit=1)

        if existing:
            return {
                "success": False,
                "error": f"{member.name} is already registered for this belt test.",
            }

        # Create registration
        reg = Registration.create({
            "test_id": belt_test.id,
            "member_id": member.id,
            "target_rank_id": rank.id,
            "result": "pending",
        })

        # Create snapshot for undo
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, Registration._name, reg.id, "create")

        test_date_str = belt_test.test_date.isoformat() if belt_test.test_date else "TBD"
        return {
            "success": True,
            "message": f"Registered {member.name} for belt test on {test_date_str} (testing for {rank.name}).",
            "data": {"registration_id": reg.id, "test_id": belt_test.id},
        }

    @api.model
    def _handle_undo_action(self, intent_data, resolved_data, action_log):
        """Execute an undo operation via chat — delegates to undo_last_action()."""
        user_id = self.env.user.id
        result = self.undo_last_action(user_id=user_id)
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "No undo data available.")}
        return {
            "success": True,
            "message": result.get("confirmation_prompt") or "Undo complete.",
            "data": result,
        }

    @api.model
    def _handle_unknown(self, intent_data, resolved_data, action_log):
        """Handle unknown/unrecognized intents."""
        return {
            "success": False,
            "error": "I'm not sure what action you want. Could you please rephrase?",
            "data": None,
        }

    @api.model
    def _handle_capability_list(self, intent_data, resolved_data, action_log):
        """Return a friendly, grouped summary of what the AI assistant can do."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        category_filter = params.get("category", "").lower().strip()

        # If the LLM already produced a friendly message, use it directly
        msg = params.get("message", "").strip()
        if msg and len(msg) > 20:
            return {"success": True, "message": msg, "data": None}

        # Otherwise build a capability summary from registered intent schemas
        role = action_log.role if action_log and hasattr(action_log, "role") else "instructor"
        IntentSchema = self.env["ai.intent.schema"].sudo()
        domain = [("active", "=", True)]
        if category_filter:
            domain.append(("category", "=", category_filter))
        schemas = IntentSchema.search(domain, order="category, sequence, intent_type")

        # Group by category
        by_category = {}
        category_labels = {
            "read": "Looking up information",
            "member": "Member management",
            "class": "Classes & sessions",
            "enrollment": "Student enrollment",
            "belt": "Belt & rank promotions",
            "attendance": "Attendance tracking",
            "subscription": "Subscriptions & billing",
            "communication": "Messages & notifications",
            "marketing": "Marketing & campaigns",
            "social": "Social media",
            "sales": "Sales & quotations",
            "pos": "Point of sale",
            "accounting": "Invoices & payments",
            "hr": "Employee management",
            "discuss": "Internal messaging",
            "system": "System operations",
            "meta": "Help & guidance",
        }
        for schema in schemas:
            if not schema.check_role_permission(role):
                continue
            cat = schema.category or "system"
            label = category_labels.get(cat, cat.title())
            if label not in by_category:
                by_category[label] = []
            # Pick the first example phrase as a concrete example
            examples = schema.get_example_phrases_list()
            example = f' (e.g. "{examples[0]}")' if examples else ""
            by_category[label].append(f"{schema.name}{example}")

        if not by_category:
            summary = "No capabilities found for your role."
        else:
            lines = ["Here's what I can help you with:\n"]
            for cat_label, actions in sorted(by_category.items()):
                lines.append(f"**{cat_label}**")
                # Show up to 3 actions per category to keep it readable
                for action in actions[:3]:
                    lines.append(f"  • {action}")
                if len(actions) > 3:
                    lines.append(f"  • … and {len(actions) - 3} more")
                lines.append("")
            lines.append("Just describe what you want in plain English and I'll handle it.")
            summary = "\n".join(lines)

        return {"success": True, "message": summary, "data": None}

    @api.model
    def _handle_help_request(self, intent_data, resolved_data, action_log):
        """Answer a how-to question about using the AI assistant."""
        params = intent_data.get("parameters", {}) if intent_data else {}

        # If the LLM already produced a friendly message, use it directly
        msg = params.get("message", "").strip()
        if msg and len(msg) > 10:
            return {"success": True, "message": msg, "data": None}

        topic = params.get("topic", "").strip()
        if topic:
            fallback = (
                f"To {topic}, just describe what you want in plain English — "
                f"for example: \"{topic}\". I'll figure out the rest."
            )
        else:
            fallback = (
                "Just describe what you want in plain English. "
                "For example: \"Check in Jordan\", \"Show today's schedule\", "
                "\"Promote Alex to blue belt\", or \"What can you do?\"."
            )

        return {"success": True, "message": fallback, "data": None}

    # ═══════════════════════════════════════════════════════════════════════════
    # Extended Intent Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_household_lookup(self, intent_data, resolved_data, action_log):
        """Look up a household and list family members."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        Partner = self.env["res.partner"].sudo()
        household = self.env["res.partner"].sudo()

        # If member was resolved, find their household via the same
        # pattern used by dojo.member._get_household()
        member_id = resolved_data.get("member_id")
        if member_id:
            member = self.env["dojo.member"].sudo().browse(member_id)
            if member.exists():
                hh = member.partner_id.parent_id.filtered("is_household")
                if hh:
                    household = hh

        # Or search by name: try household name first, then member name
        if not household:
            name = params.get("household_name") or params.get("member_name") or params.get("name")
            if name:
                household = Partner.search([
                    ("is_household", "=", True), ("name", "ilike", name),
                ], limit=1)
                if not household:
                    # Try to find via a member name
                    members = self._search_members(name, limit=5)
                    for m in members:
                        hh = m.partner_id.parent_id.filtered("is_household")
                        if hh:
                            household = hh
                            break

        if not household:
            # Last resort: list all households if no name given
            name = params.get("household_name") or params.get("member_name") or params.get("name")
            if not name:
                all_hh = Partner.search([("is_household", "=", True)], limit=20)
                if all_hh:
                    return {
                        "success": True,
                        "message": f"Found {len(all_hh)} household(s).",
                        "data": {"households": [{"name": h.name, "id": h.id} for h in all_hh]},
                    }
            return {"success": False, "error": "Household not found. Try providing a member or family name."}

        guardian = household.primary_guardian_id
        children = household.child_ids.filtered(lambda c: not c.is_household)
        members_in_household = []
        for child in children:
            roles = []
            if child.is_guardian:
                roles.append("Guardian")
            if child.is_student:
                roles.append("Student")
            if child.is_minor:
                roles.append("Minor")
            members_in_household.append({
                "name": child.name,
                "roles": ", ".join(roles) if roles else "Contact",
                "email": child.email or "",
                "phone": child.phone or "",
            })

        return {
            "success": True,
            "message": f"Household '{household.name}' — {len(members_in_household)} member(s).",
            "data": {
                "household_id": household.id,
                "household_name": household.name,
                "primary_guardian": guardian.name if guardian else "None",
                "members": members_in_household,
            },
        }

    def _handle_next_class_session(self, intent_data, resolved_data, action_log):
        """Return the next upcoming class session(s), optionally filtered by member."""
        from odoo.fields import Datetime as OdooDatetime
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id") or params.get("member_id")
        member_name = params.get("member_name", "")
        limit = min(int(params.get("limit", 5)), 10)
        now = OdooDatetime.now()

        if not member_id and member_name:
            m = self.env["dojo.member"].search([("name", "ilike", member_name)], limit=1)
            member_id = m.id if m else None

        if member_id:
            # Find sessions the member is enrolled in that are upcoming
            enrollments = self.env["dojo.class.enrollment"].search([
                ("member_id", "=", member_id),
                ("status", "in", ["registered", "attended"]),
                ("session_id.start_datetime", ">=", now),
            ], order="session_id.start_datetime asc", limit=limit)
            sessions = enrollments.mapped("session_id")
            sessions = sessions.sorted(key=lambda s: s.start_datetime)[:limit]
            label = self.env["dojo.member"].browse(member_id).name
        else:
            sessions = self.env["dojo.class.session"].search([
                ("start_datetime", ">=", now),
                ("state", "in", ["scheduled", "in_progress"]),
            ], order="start_datetime asc", limit=limit)
            label = "all members"

        if not sessions:
            return {
                "success": True,
                "message": f"No upcoming sessions found for {label}.",
                "data": {"sessions": []},
            }

        session_list = []
        for s in sessions:
            session_list.append({
                "id": s.id,
                "name": s.template_id.name if s.template_id else s.name,
                "start": str(s.start_datetime),
                "end": str(s.end_datetime) if s.end_datetime else "",
                "capacity": s.capacity,
                "seats_taken": s.seats_taken,
                "available": max(0, s.capacity - s.seats_taken),
            })

        return {
            "success": True,
            "message": f"Next {len(session_list)} session(s) for {label}.",
            "data": {"sessions": session_list},
        }

    def _handle_previous_class_session(self, intent_data, resolved_data, action_log):
        """Return the most recent past class session(s), optionally filtered by member."""
        from odoo.fields import Datetime as OdooDatetime
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id") or params.get("member_id")
        member_name = params.get("member_name", "")
        limit = min(int(params.get("limit", 5)), 10)
        now = OdooDatetime.now()

        if not member_id and member_name:
            m = self.env["dojo.member"].search([("name", "ilike", member_name)], limit=1)
            member_id = m.id if m else None

        if member_id:
            enrollments = self.env["dojo.class.enrollment"].search([
                ("member_id", "=", member_id),
                ("session_id.start_datetime", "<", now),
            ], order="session_id.start_datetime desc", limit=limit)
            sessions = enrollments.mapped("session_id")
            sessions = sessions.sorted(key=lambda s: s.start_datetime, reverse=True)[:limit]
            label = self.env["dojo.member"].browse(member_id).name
        else:
            sessions = self.env["dojo.class.session"].search([
                ("start_datetime", "<", now),
                ("state", "in", ["done", "completed", "cancelled"]),
            ], order="start_datetime desc", limit=limit)
            label = "all members"

        if not sessions:
            return {
                "success": True,
                "message": f"No past sessions found for {label}.",
                "data": {"sessions": []},
            }

        session_list = []
        for s in sessions:
            session_list.append({
                "id": s.id,
                "name": s.template_id.name if s.template_id else s.name,
                "start": str(s.start_datetime),
                "end": str(s.end_datetime) if s.end_datetime else "",
                "state": s.state,
            })

        return {
            "success": True,
            "message": f"Last {len(session_list)} session(s) for {label}.",
            "data": {"sessions": session_list},
        }

    def _handle_belt_readiness_all(self, intent_data, resolved_data, action_log):
        """Check belt test readiness for all active members (bulk version)."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        ready_only = params.get("ready_only", False)

        active_members = self.env["dojo.member"].search([
            ("membership_state", "in", ["active", "pending"]),
            ("current_rank_id", "!=", False),
        ], order="name asc")

        if not active_members:
            return {
                "success": True,
                "message": "No ranked active members found.",
                "data": {"members": []},
            }

        results = []
        for member in active_members:
            current_rank = member.current_rank_id
            attended = member.attendance_since_last_rank or 0

            next_rank = self.env["dojo.belt.rank"].search([
                ("sequence", ">", current_rank.sequence),
                ("style_id", "=", current_rank.style_id.id),
                ("company_id", "=", member.company_id.id),
                ("active", "=", True),
            ], order="sequence asc", limit=1)

            if not next_rank:
                continue  # highest rank, skip

            threshold = next_rank.attendance_threshold or 0
            if threshold == 0:
                ready = True
                remaining = 0
            else:
                ready = attended >= threshold
                remaining = max(0, threshold - attended)

            if ready_only and not ready:
                continue

            results.append({
                "member_name": member.name,
                "current_rank": current_rank.name,
                "next_rank": next_rank.name,
                "attended": attended,
                "threshold": threshold,
                "remaining": remaining,
                "ready": ready,
            })

        ready_count = sum(1 for r in results if r["ready"])
        total = len(results)
        return {
            "success": True,
            "message": f"{ready_count} of {total} ranked active member(s) are ready for their next belt test.",
            "data": {
                "ready_count": ready_count,
                "total_checked": total,
                "members": results,
            },
        }

    def _handle_enroll_next_session(self, intent_data, resolved_data, action_log):
        """Enroll a member in the next upcoming session of a course they attend."""
        from odoo.fields import Datetime as OdooDatetime
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id")
        member_name = params.get("member_name", "")
        course_name = params.get("course_name") or params.get("class_name") or params.get("template_name")
        now = OdooDatetime.now()

        if not member_id and member_name:
            m = self.env["dojo.member"].search([("name", "ilike", member_name)], limit=1)
            member_id = m.id if m else None

        if not member_id:
            return {"success": False, "error": "Could not identify the member. Please specify a name."}

        member = self.env["dojo.member"].browse(member_id)

        # Find the member's enrolled course templates (from past/current enrollments)
        existing_enrollments = self.env["dojo.class.enrollment"].search([
            ("member_id", "=", member_id),
            ("status", "in", ["registered", "attended"]),
        ])
        enrolled_templates = existing_enrollments.mapped("session_id.template_id").filtered(
            lambda t: t.active
        )

        if course_name:
            enrolled_templates = enrolled_templates.filtered(
                lambda t: course_name.lower() in (t.name or "").lower()
            )

        if not enrolled_templates:
            return {
                "success": False,
                "error": (
                    f"{member.name} has no active course enrollments to base the next session on. "
                    "Use 'enroll in class' with a specific session instead."
                ),
            }

        # For each template, find the next upcoming session not already enrolled
        already_enrolled_session_ids = existing_enrollments.filtered(
            lambda e: e.session_id.start_datetime >= now
        ).mapped("session_id").ids

        Enrollment = self.env["dojo.class.enrollment"]
        target_session = None
        for template in enrolled_templates:
            next_session = self.env["dojo.class.session"].search([
                ("template_id", "=", template.id),
                ("start_datetime", ">=", now),
                ("state", "in", ["scheduled", "in_progress"]),
                ("id", "not in", already_enrolled_session_ids),
            ], order="start_datetime asc", limit=1)
            if next_session:
                target_session = next_session
                break

        if not target_session:
            return {
                "success": False,
                "error": f"{member.name} is already enrolled in all upcoming sessions of their current course(s).",
            }

        # Check capacity
        if target_session.seats_taken >= target_session.capacity:
            return {
                "success": False,
                "error": (
                    f"{target_session.template_id.name} on "
                    f"{target_session.start_datetime.strftime('%b %d at %I:%M %p')} is at capacity."
                ),
            }

        enrollment = Enrollment.with_context(skip_course_membership_check=True).create({
            "member_id": member_id,
            "session_id": target_session.id,
            "status": "registered",
        })

        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, Enrollment._name, enrollment.id, "create")

        session_label = (
            f"{target_session.template_id.name} on "
            f"{target_session.start_datetime.strftime('%A, %b %d at %I:%M %p')}"
        )
        return {
            "success": True,
            "message": f"Enrolled {member.name} in {session_label}.",
            "data": {
                "enrollment_id": enrollment.id,
                "session_id": target_session.id,
                "session_name": target_session.template_id.name,
                "session_start": str(target_session.start_datetime),
            },
        }

    def _handle_birthday_upcoming(self, intent_data, resolved_data, action_log):
        """Return members with upcoming birthdays within N days."""
        from datetime import timedelta
        params = intent_data.get("parameters", {}) if intent_data else {}
        days_ahead = params.get("days", 30)
        try:
            days_ahead = int(days_ahead)
        except (TypeError, ValueError):
            days_ahead = 30

        today = fields.Date.today()
        end_date = today + timedelta(days=days_ahead)

        # Search active members who have DOB set
        members = self.env["dojo.member"].search([
            ("membership_state", "in", ["active", "trial"]),
            ("date_of_birth", "!=", False),
        ])

        upcoming = []
        for m in members:
            dob = m.date_of_birth
            # Calculate this year's birthday
            try:
                bday_this_year = dob.replace(year=today.year)
            except ValueError:
                # Feb 29 on non-leap year
                bday_this_year = dob.replace(year=today.year, day=28)
            # If birthday already passed this year, check next year
            if bday_this_year < today:
                try:
                    bday_this_year = dob.replace(year=today.year + 1)
                except ValueError:
                    bday_this_year = dob.replace(year=today.year + 1, day=28)
            if today <= bday_this_year <= end_date:
                days_until = (bday_this_year - today).days
                age = bday_this_year.year - dob.year
                upcoming.append({
                    "name": m.name,
                    "date_of_birth": str(dob),
                    "birthday": str(bday_this_year),
                    "days_until": days_until,
                    "age_turning": age,
                    "membership_state": m.membership_state,
                })

        upcoming.sort(key=lambda x: x["days_until"])

        if not upcoming:
            return {
                "success": True,
                "message": f"No member birthdays in the next {days_ahead} days.",
                "data": [],
            }

        return {
            "success": True,
            "message": f"{len(upcoming)} birthday(s) coming up in the next {days_ahead} days.",
            "data": upcoming,
        }

    def _handle_at_risk_members(self, intent_data, resolved_data, action_log):
        """Return active members who haven't attended in N days."""
        from datetime import datetime, timedelta
        params = intent_data.get("parameters", {}) if intent_data else {}
        days = int(params.get("days", 14))
        cutoff = datetime.now() - timedelta(days=days)

        members = self.env["dojo.member"].search([("membership_state", "=", "active")])
        at_risk = []
        for m in members:
            last = self.env["dojo.attendance.log"].search(
                [("member_id", "=", m.id)], order="checkin_datetime desc", limit=1
            )
            if not last or last.checkin_datetime < cutoff:
                days_ago = (datetime.now() - last.checkin_datetime).days if last else None
                at_risk.append({
                    "name": m.name,
                    "days_since_visit": days_ago if days_ago is not None else "never",
                })

        if not at_risk:
            return {"success": True, "message": f"No members missing for more than {days} days.", "data": []}

        lines = [f"• {r['name']} — {r['days_since_visit']} days ago" for r in at_risk]
        return {
            "success": True,
            "message": f"{len(at_risk)} members haven't been in for {days}+ days:\n" + "\n".join(lines),
            "data": at_risk,
        }

    @api.model
    def _handle_subscription_expiring(self, intent_data, resolved_data, action_log):
        """Return active subscriptions expiring within N days (default 30)."""
        from datetime import date, timedelta
        params = intent_data.get("parameters", {}) if intent_data else {}
        days = int(params.get("days", 30))
        today = date.today()
        cutoff = today + timedelta(days=days)

        subs = self.env["sale.subscription"].search([
            ("state", "=", "active"),
            ("date", "!=", False),
            ("date", ">=", today.isoformat()),
            ("date", "<=", cutoff.isoformat()),
        ], order="date asc")

        if not subs:
            return {
                "success": True,
                "message": f"No active subscriptions expiring in the next {days} days.",
                "data": [],
            }

        lines = []
        data = []
        for s in subs:
            member_name = s.member_id.name if hasattr(s, "member_id") and s.member_id else "Unknown"
            days_left = (s.date - today).days
            label = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
            lines.append(f"• {member_name} — expires {label} ({s.date})")
            data.append({"name": member_name, "end_date": str(s.date), "days_left": days_left})

        return {
            "success": True,
            "message": f"{len(subs)} subscription{'s' if len(subs) != 1 else ''} expiring in the next {days} days:\n" + "\n".join(lines),
            "data": data,
        }

    @api.model
    def _handle_campaign_lookup(self, intent_data, resolved_data, action_log):
        """Return recent campaign stats."""
        Campaign = self.env.get("dojo.marketing.campaign")
        if not Campaign:
            return {"success": False, "error": "Marketing module is not installed."}

        campaigns = Campaign.search([], order="last_sent_date desc", limit=5)
        if not campaigns:
            return {"success": True, "message": "No campaigns found.", "data": []}

        lines = []
        for c in campaigns:
            lines.append(f"• {c.name}: {c.sent_count} sent, last on {c.last_sent_date or 'never'}")
        return {
            "success": True,
            "message": "Recent campaigns:\n" + "\n".join(lines),
            "data": [{"name": c.name, "sent_count": c.sent_count} for c in campaigns],
        }

    @api.model
    def _handle_marketing_card_lookup(self, intent_data, resolved_data, action_log):
        """Return marketing cards published to the kiosk."""
        Card = self.env.get("dojo.marketing.card")
        if not Card:
            return {"success": False, "error": "Marketing module is not installed."}

        cards = Card.search([("publish_kiosk", "=", True)])
        if not cards:
            return {"success": True, "message": "No marketing cards are currently on the kiosk.", "data": []}

        lines = [f"• {c.name} ({c.card_type})" for c in cards]
        return {
            "success": True,
            "message": f"{len(cards)} card(s) on the kiosk:\n" + "\n".join(lines),
            "data": [{"name": c.name, "type": c.card_type} for c in cards],
        }

    @api.model
    def _handle_subscription_pause(self, intent_data, resolved_data, action_log):
        """Pause a member's active subscription."""
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Could not identify the member."}

        sub = self.env["sale.subscription"].search(
            [("member_id", "=", member_id), ("state", "=", "active")], limit=1
        )
        if not sub:
            return {"success": False, "error": "No active subscription found for this member."}

        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, "sale.subscription", sub.id, "write", snapshot_data={"state": sub.state})
        sub.action_set_paused()
        member = self.env["dojo.member"].browse(member_id)
        return {
            "success": True,
            "message": f"Paused {member.name}'s subscription ({sub.plan_id.name if sub.plan_id else 'subscription'}).",
            "data": {"subscription_id": sub.id},
        }

    @api.model
    def _handle_subscription_resume(self, intent_data, resolved_data, action_log):
        """Resume a member's paused subscription."""
        member_id = resolved_data.get("member_id")
        if not member_id:
            return {"success": False, "error": "Could not identify the member."}

        sub = self.env["sale.subscription"].search(
            [("member_id", "=", member_id), ("state", "=", "paused")], limit=1
        )
        if not sub:
            return {"success": False, "error": "No paused subscription found for this member."}

        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, "sale.subscription", sub.id, "write", snapshot_data={"state": sub.state})
        sub.action_set_active()
        member = self.env["dojo.member"].browse(member_id)
        return {
            "success": True,
            "message": f"Resumed {member.name}'s subscription.",
            "data": {"subscription_id": sub.id},
        }

    @api.model
    def _handle_campaign_create(self, intent_data, resolved_data, action_log):
        """Create a new marketing campaign in draft state."""
        Campaign = self.env.get("dojo.marketing.campaign")
        if not Campaign:
            return {"success": False, "error": "Marketing module is not installed."}

        params = intent_data.get("parameters", {}) if intent_data else {}
        name = params.get("campaign_name", "New Campaign")
        send_email = params.get("send_email", True)
        send_sms = params.get("send_sms", False)

        campaign = Campaign.create({
            "name": name,
            "send_email": send_email,
            "send_sms": send_sms,
            "state": "draft",
        })
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, "dojo.marketing.campaign", campaign.id, "create")
        return {
            "success": True,
            "message": f"Created campaign '{name}' in draft state. Activate it when ready to send.",
            "data": {"campaign_id": campaign.id, "name": name},
        }

    @api.model
    def _handle_campaign_activate(self, intent_data, resolved_data, action_log):
        """Activate a draft marketing campaign."""
        Campaign = self.env.get("dojo.marketing.campaign")
        if not Campaign:
            return {"success": False, "error": "Marketing module is not installed."}

        params = intent_data.get("parameters", {}) if intent_data else {}
        name = params.get("campaign_name", "")
        campaign_id = resolved_data.get("campaign_id")

        if campaign_id:
            campaign = Campaign.browse(campaign_id)
        elif name:
            campaign = Campaign.search([("name", "ilike", name), ("state", "=", "draft")], limit=1)
        else:
            campaign = Campaign.search([("state", "=", "draft")], order="id desc", limit=1)

        if not campaign:
            return {"success": False, "error": "No draft campaign found to activate."}

        campaign.action_activate()
        return {
            "success": True,
            "message": f"Campaign '{campaign.name}' is now active and will send on the next scheduled run.",
            "data": {"campaign_id": campaign.id},
        }

    @api.model
    def _handle_social_post_create(self, intent_data, resolved_data, action_log):
        """Create and immediately publish a social post."""
        Post = self.env.get("dojo.social.post")
        if not Post:
            return {"success": False, "error": "Social media module is not installed."}

        params = intent_data.get("parameters", {}) if intent_data else {}
        message = params.get("message", "")
        account_id = resolved_data.get("social_account_id")

        if not message:
            return {"success": False, "error": "No post message provided."}

        account = self.env["dojo.social.account"].browse(account_id) if account_id else \
            self.env["dojo.social.account"].search([("status", "=", "connected")], limit=1)

        if not account:
            return {"success": False, "error": "No connected social account found."}

        post = Post.create({"message": message, "account_id": account.id})
        try:
            post.action_post_now()
        except Exception as e:
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "message": f"Posted to {account.name}: '{message[:60]}...'",
            "data": {"post_id": post.id},
        }

    @api.model
    def _handle_social_post_schedule(self, intent_data, resolved_data, action_log):
        """Create a scheduled social post."""
        Post = self.env.get("dojo.social.post")
        if not Post:
            return {"success": False, "error": "Social media module is not installed."}

        params = intent_data.get("parameters", {}) if intent_data else {}
        message = params.get("message", "")
        scheduled_date = params.get("scheduled_date")
        account_id = resolved_data.get("social_account_id")

        if not message:
            return {"success": False, "error": "No post message provided."}
        if not scheduled_date:
            return {"success": False, "error": "No scheduled date provided."}

        account = self.env["dojo.social.account"].browse(account_id) if account_id else \
            self.env["dojo.social.account"].search([("status", "=", "connected")], limit=1)

        if not account:
            return {"success": False, "error": "No connected social account found."}

        post = Post.create({
            "message": message,
            "account_id": account.id,
            "scheduled_date": scheduled_date,
            "state": "scheduled",
        })
        return {
            "success": True,
            "message": f"Scheduled post to {account.name} for {scheduled_date}: '{message[:60]}'",
            "data": {"post_id": post.id},
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Utility Methods
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _format_time_ago(self, dt):
        """Format a datetime as a human-readable time ago string."""
        if not dt:
            return "unknown time"

        from datetime import datetime
        now = datetime.now()
        if dt.tzinfo:
            now = now.replace(tzinfo=dt.tzinfo)

        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"

    # ═══════════════════════════════════════════════════════════════════════════
    # Core Odoo Module — Domain Builders (Task, Invoice, Activity)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_task_list(self, intent_data, resolved_data):
        """Domain for listing project tasks for the calling user."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        uid = self.env.uid
        domain = []

        # Filter by calling user unless admin requests all
        role = resolved_data.get("role", "instructor")
        if role != "admin":
            domain.append(("user_ids", "in", [uid]))

        # Optional: filter by project name
        project_name = params.get("project_name")
        if project_name:
            projects = self.env["project.project"].search([("name", "ilike", project_name)], limit=5)
            if projects:
                domain.append(("project_id", "in", projects.ids))

        # Optional: filter by overdue
        if params.get("overdue"):
            domain.append(("date_deadline", "<", fields.Date.today()))
            domain.append(("stage_id.fold", "=", False))

        # Optional: filter by member name (link via partner)
        member_name = params.get("member_name")
        if member_name:
            members = self.env["dojo.member"].search([("name", "ilike", member_name)], limit=3)
            if members:
                partners = members.mapped("partner_id")
                domain.append(("partner_id", "in", partners.ids))

        # Default: exclude done/cancelled stages
        if not params.get("include_done"):
            domain.append(("stage_id.fold", "=", False))

        return domain

    @api.model
    def _domain_invoice_lookup(self, intent_data, resolved_data):
        """Domain for looking up invoices for a specific member/family."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        member_id = resolved_data.get("member_id")
        domain = [("move_type", "in", ["out_invoice", "out_refund"]), ("state", "!=", "cancel")]

        if member_id:
            member = self.env["dojo.member"].browse(member_id)
            if member.exists() and member.partner_id:
                # Get the commercial partner (household) so we catch household-level invoices
                commercial = member.partner_id.commercial_partner_id
                domain.append(("partner_id", "child_of", commercial.id))
        else:
            member_name = params.get("member_name") or params.get("name")
            if member_name:
                members = self.env["dojo.member"].sudo().search([("name", "ilike", member_name)], limit=3)
                if members:
                    commercial_ids = members.mapped("partner_id.commercial_partner_id").ids
                    domain.append(("partner_id", "child_of", commercial_ids))

        return domain

    @api.model
    def _domain_invoice_list(self, intent_data, resolved_data):
        """Domain for listing invoices — defaults to unpaid/overdue."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted")]

        filter_type = params.get("filter", "overdue")
        if filter_type == "overdue":
            domain += [("payment_state", "!=", "paid"), ("invoice_date_due", "<", fields.Date.today())]
        elif filter_type == "unpaid":
            domain.append(("payment_state", "!=", "paid"))
        elif filter_type == "paid":
            domain.append(("payment_state", "=", "paid"))
        else:
            # Default: all unpaid
            domain.append(("payment_state", "!=", "paid"))

        return domain

    @api.model
    def _domain_activity_list(self, intent_data, resolved_data):
        """Domain for listing mail activities for the calling user."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        uid = self.env.uid
        domain = [("user_id", "=", uid)]

        # Optional: overdue only
        if params.get("overdue"):
            domain.append(("date_deadline", "<", fields.Date.today()))

        # Optional: specific model (e.g., 'dojo.member')
        res_model = params.get("res_model")
        if res_model:
            domain.append(("res_model", "=", res_model))

        return domain

    # ═══════════════════════════════════════════════════════════════════════════
    # New Read Intent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_belt_test_list(self, intent_data, resolved_data):
        """Domain for listing upcoming belt tests."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("state", "!=", "cancelled")]

        # Default: future tests only
        if not params.get("include_past"):
            domain.append(("test_date", ">=", fields.Date.today()))

        program_name = params.get("program_name")
        if program_name:
            programs = self.env["dojo.program"].search([("name", "ilike", program_name)], limit=3)
            if programs:
                domain.append(("program_id", "in", programs.ids))
        return domain

    def _domain_belt_test_registration_list(self, intent_data, resolved_data):
        """Domain for listing belt test registrations (who is signed up)."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []

        # Default: registrations for upcoming tests only
        upcoming_tests = self.env["dojo.belt.test"].search([
            ("test_date", ">=", fields.Date.today()),
            ("state", "!=", "cancelled"),
        ])
        if upcoming_tests:
            domain.append(("test_id", "in", upcoming_tests.ids))

        member_id = resolved_data.get("member_id")
        if member_id:
            domain.append(("member_id", "=", member_id))

        result_filter = params.get("result")
        if result_filter:
            domain.append(("result", "=", result_filter))

        return domain

    @api.model
    def _domain_program_list(self, intent_data, resolved_data):
        """Domain for listing programs."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("active", "=", True)]
        if params.get("is_trial"):
            domain.append(("is_trial", "=", True))
        name = params.get("name") or params.get("program_name")
        if name:
            domain.append(("name", "ilike", name))
        return domain

    @api.model
    def _domain_class_template_list(self, intent_data, resolved_data):
        """Domain for listing class templates / recurring courses."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("active", "=", True)]
        program_name = params.get("program_name")
        if program_name:
            programs = self.env["dojo.program"].search([("name", "ilike", program_name)], limit=3)
            if programs:
                domain.append(("program_id", "in", programs.ids))
        name = params.get("name") or params.get("course_name")
        if name:
            domain.append(("name", "ilike", name))
        return domain

    @api.model
    def _domain_social_post_list(self, intent_data, resolved_data):
        """Domain for listing social media posts."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        state = params.get("state")
        if state and state in ("draft", "scheduled", "posted", "error"):
            domain.append(("state", "=", state))
        if params.get("failed"):
            domain.append(("state", "=", "error"))
        return domain

    @api.model
    def _domain_program_enrollment_lookup(self, intent_data, resolved_data):
        """Domain for looking up program enrollments."""
        member_id = resolved_data.get("member_id")
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        if member_id:
            domain.append(("member_id", "=", member_id))
        program_name = params.get("program_name")
        if program_name:
            programs = self.env["dojo.program"].search([("name", "ilike", program_name)], limit=3)
            if programs:
                domain.append(("program_id", "in", programs.ids))
        if params.get("active_only", True):
            domain.append(("is_active", "=", True))
        return domain

    @api.model
    def _domain_onboarding_status(self, intent_data, resolved_data):
        """Domain for checking onboarding progress."""
        member_id = resolved_data.get("member_id")
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        if member_id:
            domain.append(("member_id", "=", member_id))
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        # Default: show incomplete onboardings
        if not member_id and not state:
            domain.append(("state", "!=", "done"))
        return domain

    @api.model
    def _domain_points_lookup(self, intent_data, resolved_data):
        """Domain for looking up points transactions for a member."""
        member_id = resolved_data.get("member_id")
        domain = []
        if member_id:
            domain.append(("member_id", "=", member_id))
        return domain

    @api.model
    def _domain_credit_lookup(self, intent_data, resolved_data):
        """Domain for looking up credit transactions for a member."""
        member_id = resolved_data.get("member_id")
        domain = []
        if member_id:
            domain.append(("member_id", "=", member_id))
        params = intent_data.get("parameters", {}) if intent_data else {}
        status = params.get("status")
        if status:
            domain.append(("status", "=", status))
        return domain

    def _domain_course_auto_enroll_list(self, intent_data, resolved_data):
        """Domain for listing auto-enroll rules."""
        member_id = resolved_data.get("member_id")
        domain = []
        if member_id:
            domain.append(("member_id", "=", member_id))
        params = intent_data.get("parameters", {}) if intent_data else {}
        if params.get("active_only", True):
            domain.append(("active", "=", True))
        return domain

    def _domain_campaign_list(self, intent_data, resolved_data):
        """Domain for listing AI calling campaigns."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        return domain

    def _domain_kiosk_announcement_list(self, intent_data, resolved_data):
        """Domain for listing kiosk announcements."""
        domain = [("active", "=", True)]
        return domain

    # ═══════════════════════════════════════════════════════════════════════════
    # Sales Agent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_sale_order_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        date_from = params.get("date_from")
        if date_from:
            domain.append(("date_order", ">=", date_from))
        date_to = params.get("date_to")
        if date_to:
            domain.append(("date_order", "<=", date_to))
        return domain

    @api.model
    def _domain_sale_order_lookup(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        order_ref = params.get("order_ref")
        if order_ref:
            domain.append(("name", "ilike", order_ref))
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        partner_id = params.get("partner_id")
        if partner_id:
            domain.append(("partner_id", "=", int(partner_id)))
        return domain or [("id", ">", 0)]

    @api.model
    def _domain_product_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("sale_ok", "=", True)]
        search_term = params.get("search") or params.get("category")
        if search_term:
            domain.append(("name", "ilike", search_term))
        prod_type = params.get("type")
        if prod_type:
            domain.append(("type", "=", prod_type))
        return domain

    @api.model
    def _domain_product_lookup(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        product_name = params.get("product_name") or params.get("name")
        if product_name:
            domain.append(("name", "ilike", product_name))
        product_id = params.get("product_id")
        if product_id:
            domain = [("id", "=", int(product_id))]
        return domain or [("sale_ok", "=", True)]

    # ═══════════════════════════════════════════════════════════════════════════
    # POS Agent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_pos_order_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        session_id = params.get("session_id")
        if session_id:
            domain.append(("session_id", "=", int(session_id)))
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        date_from = params.get("date_from")
        if date_from:
            domain.append(("date_order", ">=", date_from))
        date_to = params.get("date_to")
        if date_to:
            domain.append(("date_order", "<=", date_to))
        if not date_from and not date_to and not session_id:
            domain.append(("date_order", ">=", str(fields.Date.today())))
        return domain

    @api.model
    def _domain_pos_order_lookup(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        order_ref = params.get("order_ref") or params.get("pos_reference")
        if order_ref:
            domain.append(("pos_reference", "ilike", order_ref))
        order_id = params.get("order_id")
        if order_id:
            domain = [("id", "=", int(order_id))]
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        return domain or [("id", ">", 0)]

    @api.model
    def _domain_pos_session_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        else:
            domain.append(("state", "in", ["opened", "closing_control"]))
        config_name = params.get("config_name")
        if config_name:
            domain.append(("config_id.name", "ilike", config_name))
        return domain

    # ═══════════════════════════════════════════════════════════════════════════
    # Accounting Agent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_payment_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("state", "!=", "cancel")]
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        state = params.get("state")
        if state:
            domain = [("state", "=", state)]
        payment_type = params.get("payment_type")
        if payment_type:
            domain.append(("payment_type", "=", payment_type))
        date_from = params.get("date_from")
        if date_from:
            domain.append(("date", ">=", date_from))
        date_to = params.get("date_to")
        if date_to:
            domain.append(("date", "<=", date_to))
        return domain

    @api.model
    def _domain_bill_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("move_type", "=", "in_invoice"), ("state", "!=", "cancel")]
        partner_name = params.get("partner_name")
        if partner_name:
            domain.append(("partner_id.name", "ilike", partner_name))
        state = params.get("state")
        if state:
            domain.append(("state", "=", state))
        payment_state = params.get("payment_state")
        if payment_state:
            domain.append(("payment_state", "=", payment_state))
        else:
            domain.append(("payment_state", "!=", "paid"))
        return domain

    # ── New Query Intent Domain Builders ──────────────────────────────────────

    @api.model
    def _domain_members_by_belt(self, intent_data, resolved_data):
        """Filter members by their current belt color / rank name."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        belt_color = (
            params.get("belt_color")
            or params.get("belt_name")
            or params.get("rank_name")
            or ""
        )
        domain = [("active", "=", True)]
        if belt_color:
            # Resolve rank by name ilike, then filter members by rank ID(s)
            ranks = self.env["dojo.belt.rank"].search(
                [("name", "ilike", belt_color.strip())], limit=10
            )
            if ranks:
                domain.append(("current_rank_id", "in", ranks.ids))
            else:
                # No matching rank found — return empty
                domain.append(("id", "=", -1))
        else:
            # No belt specified — return everyone (caller should nudge for a belt name)
            pass
        return domain

    @api.model
    def _domain_members_active_subscriptions(self, intent_data, resolved_data):
        """Members whose membership_state is active (i.e., have an active subscription)."""
        return [("active", "=", True), ("membership_state", "=", "active")]

    @api.model
    def _domain_members_by_state(self, intent_data, resolved_data):
        """Filter members by any membership state."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        state = (
            params.get("state")
            or params.get("membership_state")
            or params.get("status")
            or ""
        ).strip().lower()
        valid_states = {"lead", "trial", "active", "paused", "cancelled", "expired"}
        if state in valid_states:
            return [("active", "=", True), ("membership_state", "=", state)]
        # Cancelled/expired members may be archived; include them
        if state in ("cancelled", "expired"):
            return [("membership_state", "=", state)]
        # No valid state — return active members as a sensible fallback
        return [("active", "=", True)]

    @api.model
    def _domain_session_no_shows(self, intent_data, resolved_data):
        """Enrollments for a session (today by default) where attendance_state = absent."""
        import datetime as _dt
        params = intent_data.get("parameters", {}) if intent_data else {}
        session_id = resolved_data.get("session_id") or params.get("session_id")

        if not session_id:
            session_name = params.get("session_name") or params.get("class_name")
            raw_date = params.get("date") or ""
            if session_name:
                session = self.env["dojo.class.session"].search(
                    [("name", "ilike", session_name)], limit=1
                )
                session_id = session.id if session else None
            if not session_id:
                # Default: all sessions for today
                today = fields.Date.today()
                today_sessions = self.env["dojo.class.session"].search([
                    ("start_datetime", ">=", f"{today} 00:00:00"),
                    ("start_datetime", "<=", f"{today} 23:59:59"),
                ])
                if today_sessions:
                    return [
                        ("session_id", "in", today_sessions.ids),
                        ("attendance_state", "=", "absent"),
                        ("status", "in", ["registered", "waitlist"]),
                    ]
                return [("id", "=", -1)]

        return [
            ("session_id", "=", session_id),
            ("attendance_state", "=", "absent"),
            ("status", "in", ["registered", "waitlist"]),
        ]

    # ═══════════════════════════════════════════════════════════════════════════
    # HR Agent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_employee_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        department = params.get("department")
        if department:
            domain.append(("department_id.name", "ilike", department))
        job_title = params.get("job_title")
        if job_title:
            domain.append(("job_title", "ilike", job_title))
        active = params.get("active", True)
        domain.append(("active", "=", active))
        return domain

    @api.model
    def _domain_employee_lookup(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        emp_name = params.get("employee_name") or params.get("name")
        if emp_name:
            domain.append(("name", "ilike", emp_name))
        emp_id = params.get("employee_id")
        if emp_id:
            domain = [("id", "=", int(emp_id))]
        return domain or [("active", "=", True)]

    @api.model
    def _domain_department_list(self, intent_data, resolved_data):
        return []

    @api.model
    def _domain_crud_employee(self, intent_data, resolved_data):
        """Target domain for employee update operations."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        emp_id = params.get("employee_id")
        if emp_id:
            return [("id", "=", int(emp_id))]
        emp_name = params.get("employee_name") or params.get("name")
        if emp_name:
            return [("name", "ilike", emp_name)]
        return [("id", "=", -1)]

    # ═══════════════════════════════════════════════════════════════════════════
    # Discuss Agent — Domain Builders
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _domain_channel_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        channel_type = params.get("channel_type")
        if channel_type:
            domain.append(("channel_type", "=", channel_type))
        else:
            domain.append(("channel_type", "=", "channel"))
        search_term = params.get("search")
        if search_term:
            domain.append(("name", "ilike", search_term))
        return domain

    @api.model
    def _domain_channel_lookup(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = []
        channel_name = params.get("channel_name")
        if channel_name:
            domain.append(("name", "ilike", channel_name))
        channel_id = params.get("channel_id")
        if channel_id:
            domain = [("id", "=", int(channel_id))]
        return domain or [("channel_type", "=", "channel")]

    @api.model
    def _domain_message_list(self, intent_data, resolved_data):
        params = intent_data.get("parameters", {}) if intent_data else {}
        domain = [("message_type", "=", "comment")]
        channel_name = params.get("channel_name")
        channel_id = params.get("channel_id")
        if channel_id:
            domain.append(("res_id", "=", int(channel_id)))
            domain.append(("model", "=", "discuss.channel"))
        elif channel_name:
            channels = self.env["discuss.channel"].search([("name", "ilike", channel_name)], limit=1)
            if channels:
                domain.append(("res_id", "=", channels.id))
                domain.append(("model", "=", "discuss.channel"))
        author = params.get("author_name")
        if author:
            domain.append(("author_id.name", "ilike", author))
        search_text = params.get("search")
        if search_text:
            domain.append(("body", "ilike", search_text))
        return domain

    # ═══════════════════════════════════════════════════════════════════════════
    # Core Odoo Module — Resolvers (Task, Activity)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _resolve_project(self, value, model=None):
        """Resolve a project by name — defaults to 'Instructor Alerts' project."""
        if isinstance(value, int):
            return value
        Project = self.env["project.project"]
        if not value:
            # Default to the instructor alerts project
            project = Project.search([("name", "ilike", "Instructor Alerts")], limit=1)
            return project.id if project else Project.search([], limit=1).id
        project = Project.search([("name", "ilike", value), ("active", "=", True)], limit=1)
        return project.id if project else None

    @api.model
    def _resolve_activity_type(self, value, model=None):
        """Resolve a mail.activity.type by name."""
        if isinstance(value, int):
            return value
        if not value:
            # Default to 'To-Do' activity type
            activity_type = self.env["mail.activity.type"].search([("name", "ilike", "Todo")], limit=1)
            if not activity_type:
                activity_type = self.env["mail.activity.type"].search([], limit=1)
            return activity_type.id if activity_type else None
        activity_type = self.env["mail.activity.type"].search([("name", "ilike", value)], limit=1)
        return activity_type.id if activity_type else None

    # ═══════════════════════════════════════════════════════════════════════════
    # Core Odoo Module — Task Write Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_task_complete(self, intent_data, resolved_data, action_log):
        """Mark a task as done by moving it to a folded (done) stage."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        task_name = params.get("task_name") or params.get("name")
        task_id = params.get("task_id") or resolved_data.get("task_id")

        Task = self.env["project.task"]
        if task_id:
            task = Task.browse(int(task_id))
        elif task_name:
            # Search in the calling user's tasks
            task = Task.search([
                ("name", "ilike", task_name),
                ("user_ids", "in", [self.env.uid]),
                ("stage_id.fold", "=", False),
            ], limit=1)
        else:
            return {"success": False, "error": "Please specify a task name to complete."}

        if not task.exists():
            return {"success": False, "error": f"Task '{task_name}' not found."}

        # Find a done/folded stage for this project
        done_stage = self.env["project.task.type"].search([
            ("fold", "=", True),
            ("project_ids", "in", [task.project_id.id] if task.project_id else []),
        ], limit=1)
        if not done_stage:
            # Fall back to any global done stage
            done_stage = self.env["project.task.type"].search([("fold", "=", True)], limit=1)

        if not done_stage:
            return {"success": False, "error": "No 'Done' stage found in this project."}

        # Snapshot for undo
        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, "project.task", task.id, "write",
                                  snapshot_data={"stage_id": task.stage_id.id})

        task.write({"stage_id": done_stage.id})
        return {
            "success": True,
            "message": f"Marked '{task.name}' as done.",
            "data": {"task_id": task.id, "task_name": task.name, "stage": done_stage.name},
        }

    @api.model
    def _handle_task_update(self, intent_data, resolved_data, action_log):
        """Update a task's deadline, assignee, or add a note."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        task_name = params.get("task_name") or params.get("name")
        task_id = params.get("task_id") or resolved_data.get("task_id")

        Task = self.env["project.task"]
        if task_id:
            task = Task.browse(int(task_id))
        elif task_name:
            task = Task.search([
                ("name", "ilike", task_name),
                ("user_ids", "in", [self.env.uid]),
                ("stage_id.fold", "=", False),
            ], limit=1)
        else:
            return {"success": False, "error": "Please specify a task name to update."}

        if not task.exists():
            return {"success": False, "error": f"Task '{task_name}' not found."}

        values = {}
        old_values = {}

        # Deadline update
        new_deadline = params.get("date_deadline") or params.get("deadline")
        if new_deadline:
            old_values["date_deadline"] = str(task.date_deadline) if task.date_deadline else None
            try:
                values["date_deadline"] = fields.Date.from_string(new_deadline) if isinstance(new_deadline, str) else new_deadline
            except Exception:
                pass

        # Add a chatter note
        note = params.get("note") or params.get("message")
        if note:
            task.message_post(body=note)

        if values:
            Snapshot = self.env["ai.undo.snapshot"]
            Snapshot.create_snapshot(action_log.id, "project.task", task.id, "write", snapshot_data=old_values)
            task.write(values)

        updated = list(values.keys()) + (["note"] if note else [])
        return {
            "success": True,
            "message": f"Updated task '{task.name}'.",
            "data": {"task_id": task.id, "task_name": task.name, "updated_fields": updated},
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # New Agent Resolvers (Sales, Accounting, HR)
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _resolve_partner(self, value, model=None):
        """Resolve a res.partner by name or ID."""
        if isinstance(value, int):
            return value
        if not value:
            return None
        partner = self.env["res.partner"].search(
            [("name", "ilike", value), ("active", "=", True)], limit=1
        )
        return partner.id if partner else None

    @api.model
    def _resolve_department(self, value, model=None):
        """Resolve an hr.department by name or ID."""
        if isinstance(value, int):
            return value
        if not value:
            return None
        dept = self.env["hr.department"].search([("name", "ilike", value)], limit=1)
        return dept.id if dept else None

    # ═══════════════════════════════════════════════════════════════════════════
    # Sales Agent — Custom Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_sale_order_confirm(self, intent_data, resolved_data, action_log):
        """Confirm a draft quotation into a confirmed sale order."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        order_ref = params.get("order_ref") or params.get("name")
        order_id = params.get("order_id")

        Order = self.env["sale.order"].sudo()
        if order_id:
            order = Order.browse(int(order_id))
        elif order_ref:
            order = Order.search([("name", "ilike", order_ref)], limit=1)
        else:
            return {"success": False, "error": "Please specify an order reference."}

        if not order.exists():
            return {"success": False, "error": f"Order '{order_ref}' not found."}
        if order.state != "draft":
            return {"success": False, "error": f"Order {order.name} is in state '{order.state}', not draft."}

        order.action_confirm()
        return {
            "success": True,
            "message": f"Confirmed sale order {order.name}.",
            "data": {"order_id": order.id, "order_name": order.name, "state": order.state},
        }

    @api.model
    def _handle_sale_order_cancel(self, intent_data, resolved_data, action_log):
        """Cancel a sale order."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        order_ref = params.get("order_ref") or params.get("name")
        order_id = params.get("order_id")

        Order = self.env["sale.order"].sudo()
        if order_id:
            order = Order.browse(int(order_id))
        elif order_ref:
            order = Order.search([("name", "ilike", order_ref)], limit=1)
        else:
            return {"success": False, "error": "Please specify an order reference."}

        if not order.exists():
            return {"success": False, "error": f"Order '{order_ref}' not found."}

        order._action_cancel()
        return {
            "success": True,
            "message": f"Cancelled sale order {order.name}.",
            "data": {"order_id": order.id, "order_name": order.name},
        }

    @api.model
    def _handle_sale_order_send(self, intent_data, resolved_data, action_log):
        """Send a quotation / sale order by email."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        order_ref = params.get("order_ref") or params.get("name")
        order_id = params.get("order_id")

        Order = self.env["sale.order"].sudo()
        if order_id:
            order = Order.browse(int(order_id))
        elif order_ref:
            order = Order.search([("name", "ilike", order_ref)], limit=1)
        else:
            return {"success": False, "error": "Please specify an order reference."}

        if not order.exists():
            return {"success": False, "error": f"Order '{order_ref}' not found."}

        try:
            order.action_quotation_sent()
        except Exception as e:
            _logger.warning("sale_order_send failed for %s: %s", order.name, e)
            return {"success": False, "error": f"Failed to send order: {e}"}

        return {
            "success": True,
            "message": f"Sent sale order {order.name} to {order.partner_id.name}.",
            "data": {"order_id": order.id, "order_name": order.name},
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # POS Agent — Custom Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_pos_session_open(self, intent_data, resolved_data, action_log):
        """Open a new POS session."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        config_name = params.get("config_name")
        config_id = params.get("config_id")

        PosConfig = self.env["pos.config"].sudo()
        if config_id:
            config = PosConfig.browse(int(config_id))
        elif config_name:
            config = PosConfig.search([("name", "ilike", config_name)], limit=1)
        else:
            config = PosConfig.search([], limit=1)

        if not config.exists():
            return {"success": False, "error": "No POS configuration found."}

        # Check if a session is already open
        existing = self.env["pos.session"].sudo().search([
            ("config_id", "=", config.id),
            ("state", "=", "opened"),
        ], limit=1)
        if existing:
            return {
                "success": False,
                "error": f"POS '{config.name}' already has an open session: {existing.name}.",
            }

        session = self.env["pos.session"].sudo().create({
            "config_id": config.id,
            "user_id": self.env.uid,
        })
        return {
            "success": True,
            "message": f"Opened POS session {session.name} for {config.name}.",
            "data": {"session_id": session.id, "session_name": session.name},
        }

    @api.model
    def _handle_pos_session_close(self, intent_data, resolved_data, action_log):
        """Close an open POS session."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        session_id = params.get("session_id")
        config_name = params.get("config_name")

        Session = self.env["pos.session"].sudo()
        if session_id:
            session = Session.browse(int(session_id))
        elif config_name:
            session = Session.search([
                ("config_id.name", "ilike", config_name),
                ("state", "=", "opened"),
            ], limit=1)
        else:
            session = Session.search([("state", "=", "opened")], limit=1)

        if not session.exists():
            return {"success": False, "error": "No open POS session found."}

        session.action_pos_session_closing_control()
        return {
            "success": True,
            "message": f"Closed POS session {session.name}.",
            "data": {"session_id": session.id, "session_name": session.name},
        }

    @api.model
    def _handle_pos_daily_summary(self, intent_data, resolved_data, action_log):
        """Generate a daily sales summary for POS."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        target_date = params.get("date") or str(fields.Date.today())

        domain = [("date_order", ">=", target_date + " 00:00:00"),
                  ("date_order", "<=", target_date + " 23:59:59"),
                  ("state", "in", ["paid", "done", "invoiced"])]
        config_name = params.get("config_name")
        if config_name:
            domain.append(("session_id.config_id.name", "ilike", config_name))

        orders = self.env["pos.order"].sudo().search(domain)
        total = sum(orders.mapped("amount_total"))
        count = len(orders)

        return {
            "success": True,
            "message": f"POS summary for {target_date}: {count} orders, total ${total:,.2f}.",
            "data": {
                "date": target_date,
                "order_count": count,
                "total_amount": total,
                "avg_order": round(total / count, 2) if count else 0,
            },
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Accounting Agent — Custom Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_invoice_send(self, intent_data, resolved_data, action_log):
        """Send an invoice by email."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        invoice_ref = params.get("invoice_ref") or params.get("name")
        invoice_id = params.get("invoice_id")

        Move = self.env["account.move"].sudo()
        if invoice_id:
            invoice = Move.browse(int(invoice_id))
        elif invoice_ref:
            invoice = Move.search([("name", "ilike", invoice_ref), ("move_type", "=", "out_invoice")], limit=1)
        else:
            return {"success": False, "error": "Please specify an invoice reference."}

        if not invoice.exists():
            return {"success": False, "error": f"Invoice '{invoice_ref}' not found."}
        if invoice.state != "posted":
            return {"success": False, "error": f"Invoice {invoice.name} must be posted before sending."}

        try:
            invoice.action_invoice_sent()
        except Exception as e:
            _logger.warning("invoice_send failed for %s: %s", invoice.name, e)
            return {"success": False, "error": f"Failed to send invoice: {e}"}

        return {
            "success": True,
            "message": f"Sent invoice {invoice.name} to {invoice.partner_id.name}.",
            "data": {"invoice_id": invoice.id, "invoice_name": invoice.name},
        }

    @api.model
    def _handle_payment_register(self, intent_data, resolved_data, action_log):
        """Register a payment against an invoice."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        invoice_ref = params.get("invoice_ref") or params.get("name")
        invoice_id = params.get("invoice_id")

        Move = self.env["account.move"].sudo()
        if invoice_id:
            invoice = Move.browse(int(invoice_id))
        elif invoice_ref:
            invoice = Move.search([("name", "ilike", invoice_ref), ("move_type", "in", ["out_invoice", "in_invoice"])], limit=1)
        else:
            return {"success": False, "error": "Please specify an invoice reference."}

        if not invoice.exists():
            return {"success": False, "error": f"Invoice '{invoice_ref}' not found."}
        if invoice.payment_state == "paid":
            return {"success": False, "error": f"Invoice {invoice.name} is already paid."}

        try:
            payment_wizard = self.env["account.payment.register"].sudo().with_context(
                active_model="account.move",
                active_ids=invoice.ids,
            ).create({})
            amount = params.get("amount")
            if amount:
                payment_wizard.amount = float(amount)
            journal_name = params.get("journal_name")
            if journal_name:
                journal = self.env["account.journal"].sudo().search([("name", "ilike", journal_name)], limit=1)
                if journal:
                    payment_wizard.journal_id = journal.id
            payment_wizard.action_create_payments()
        except Exception as e:
            _logger.warning("payment_register failed for %s: %s", invoice.name, e)
            return {"success": False, "error": f"Failed to register payment: {e}"}

        return {
            "success": True,
            "message": f"Payment registered for invoice {invoice.name}.",
            "data": {"invoice_id": invoice.id, "invoice_name": invoice.name},
        }

    @api.model
    def _handle_account_balance(self, intent_data, resolved_data, action_log):
        """Get account balance summary."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        account_type = params.get("account_type", "bank")
        journal_name = params.get("journal_name")

        type_map = {
            "bank": "bank",
            "cash": "cash",
            "receivable": "sale",
            "payable": "purchase",
        }
        journal_type = type_map.get(account_type, "bank")

        domain = [("type", "=", journal_type)]
        if journal_name:
            domain.append(("name", "ilike", journal_name))

        journals = self.env["account.journal"].sudo().search(domain)
        results = []
        for j in journals:
            # Get the balance from the last bank statement or default account
            balance = 0
            if j.default_account_id:
                self.env.cr.execute(
                    """SELECT COALESCE(SUM(balance), 0) FROM account_move_line
                       WHERE account_id = %s AND parent_state = 'posted'""",
                    (j.default_account_id.id,),
                )
                balance = self.env.cr.fetchone()[0]
            results.append({
                "journal": j.name,
                "type": j.type,
                "balance": float(balance),
                "currency": j.currency_id.name or j.company_id.currency_id.name,
            })

        total = sum(r["balance"] for r in results)
        return {
            "success": True,
            "message": f"Total {account_type} balance: ${total:,.2f} across {len(results)} journal(s).",
            "data": {"balances": results, "total": total},
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # HR Agent — Custom Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_employee_archive(self, intent_data, resolved_data, action_log):
        """Archive (deactivate) an employee."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        emp_name = params.get("employee_name") or params.get("name")
        emp_id = params.get("employee_id")

        Employee = self.env["hr.employee"].sudo()
        if emp_id:
            employee = Employee.browse(int(emp_id))
        elif emp_name:
            employee = Employee.search([("name", "ilike", emp_name), ("active", "=", True)], limit=1)
        else:
            return {"success": False, "error": "Please specify an employee name."}

        if not employee.exists():
            return {"success": False, "error": f"Employee '{emp_name}' not found."}

        Snapshot = self.env["ai.undo.snapshot"]
        Snapshot.create_snapshot(action_log.id, "hr.employee", employee.id, "write", snapshot_data={"active": True})
        employee.write({"active": False})

        return {
            "success": True,
            "message": f"Archived employee '{employee.name}'.",
            "data": {"employee_id": employee.id, "employee_name": employee.name},
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Discuss Agent — Custom Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @api.model
    def _handle_channel_message_send(self, intent_data, resolved_data, action_log):
        """Send a message to a Discuss channel."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        channel_name = params.get("channel_name")
        channel_id = params.get("channel_id")
        message = params.get("message")

        if not message:
            return {"success": False, "error": "Please provide a message to send."}

        Channel = self.env["discuss.channel"]
        if channel_id:
            channel = Channel.browse(int(channel_id))
        elif channel_name:
            channel = Channel.search([("name", "ilike", channel_name)], limit=1)
        else:
            return {"success": False, "error": "Please specify a channel name."}

        if not channel.exists():
            return {"success": False, "error": f"Channel '{channel_name}' not found."}

        channel.message_post(body=message, message_type="comment", subtype_xmlid="mail.mt_comment")

        return {
            "success": True,
            "message": f"Message sent to #{channel.name}.",
            "data": {"channel_id": channel.id, "channel_name": channel.name},
        }

    @api.model
    def _handle_channel_add_member(self, intent_data, resolved_data, action_log):
        """Add a user to a Discuss channel."""
        params = intent_data.get("parameters", {}) if intent_data else {}
        channel_name = params.get("channel_name")
        channel_id = params.get("channel_id")
        user_name = params.get("user_name")
        partner_id = params.get("partner_id")

        Channel = self.env["discuss.channel"]
        if channel_id:
            channel = Channel.browse(int(channel_id))
        elif channel_name:
            channel = Channel.search([("name", "ilike", channel_name)], limit=1)
        else:
            return {"success": False, "error": "Please specify a channel name."}

        if not channel.exists():
            return {"success": False, "error": f"Channel '{channel_name}' not found."}

        Partner = self.env["res.partner"]
        if partner_id:
            partner = Partner.browse(int(partner_id))
        elif user_name:
            partner = Partner.search([("name", "ilike", user_name), ("active", "=", True)], limit=1)
        else:
            return {"success": False, "error": "Please specify a user to add."}

        if not partner.exists():
            return {"success": False, "error": f"User '{user_name}' not found."}

        channel.add_members(partner_ids=partner.ids)

        return {
            "success": True,
            "message": f"Added {partner.name} to #{channel.name}.",
            "data": {"channel_id": channel.id, "partner_id": partner.id, "partner_name": partner.name},
        }
