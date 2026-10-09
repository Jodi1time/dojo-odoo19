# -*- coding: utf-8 -*-
"""
AI Processor Intent Extension — Adds structured intent parsing capabilities.

Extends the base ai.processor from elevenlabs_connector to support:
- Structured output parsing (JSON mode)
- Intent extraction with confidence scoring
- Function calling-like behavior for intents
- Fallback regex parsing when JSON mode unavailable
"""

import json
import logging
import re

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ─── Intent Parsing Tokens ────────────────────────────────────────────────────
_INTENT_START = "##INTENT##"
_INTENT_END = "##END_INTENT##"

# ─── System Prompt for Intent Parsing ─────────────────────────────────────────
_INTENT_SYSTEM_PROMPT = """\
You are an AI assistant that parses natural language commands into structured intents.

Your task is to analyze the user's input and extract:
1. The intent type (from the list of available intents)
2. Parameters for that intent
3. Your confidence level (0.0 to 1.0)

Available Intents:
{intent_definitions}

Database Context (use this to resolve entity references):
{db_context}

IMPORTANT RULES:
- Return ONLY a JSON object in your response, nothing else
- **intent_type MUST be one of the exact string values listed in "Available Intents" above.**
  🚫 NEVER invent, combine, abbreviate, or paraphrase intent type names.
  🚫 NEVER use values like "create_lead", "schedule_class", "lead_delete", "list_leads", "check_in" —
     these are INVALID. Use ONLY the exact name shown after "##" in each intent block.
  ✅ If no listed intent fits, use intent_type "unknown" with confidence 0.0.
- Extract parameters based on the intent's parameter schema
- Set confidence based on how certain you are about the intent (0.7+ is confident)
- If you cannot determine the intent, use intent_type "unknown" with confidence 0.0
- Resolve entity names to IDs when possible using the database context
- For member lookups, try to match names, member numbers, or partial names
- AGGREGATE / STATS QUERY MAPPING — always use these mappings for count/summary questions:
    "how many active students", "total members", "active student count" → member_lookup (parameters: {{"membership_state": "active"}})
    "subscriptions ending", "contracts expiring", "how many contracts are ending", "expiring memberships", "who has a contract ending", "up for renewal" → subscription_expiring (parameters: {{}})
    "members at risk", "who hasn't come in", "inactive members", "who hasn't attended" → at_risk_members (parameters: {{}})
    "students enrolled today", "today's attendance", "who checked in today", "show today's classes" → schedule_today (parameters: {{}})
    "show tomorrow's schedule", "schedule for tomorrow", "what's on tomorrow", "classes tomorrow", "schedule for [date]" → schedule_today (parameters: {{"date": "tomorrow"}}) or {{"date": "YYYY-MM-DD"}}
    "show all members", "list all students" → member_lookup (parameters: {{}})
- CRM / LEAD PIPELINE MAPPING:
    "show leads", "who are our prospects", "find lead [name]", "look up [name] lead" → lead_lookup (parameters: {{"lead_name": "..."}})
    "pipeline summary", "how many leads", "leads by stage", "show pipeline" → pipeline_summary (parameters: {{}})
    "upcoming trials", "who has a trial", "trial schedule", "trials this week" → trial_schedule (parameters: {{}})
    "qualify lead [name]", "move [name] to qualified", "[name] is a good prospect" → lead_qualify (parameters: {{"lead_name": "..."}})
    "mark trial attended", "[name] came in", "[name] showed up for trial", "trial attended for [name]" → lead_mark_attended (parameters: {{"lead_name": "..."}})
    "convert [name] to member", "make [name] a member", "[name] signed up" → lead_convert (parameters: {{"lead_name": "..."}})
    "add new prospect [name]", "new lead for [name]", "create lead [name]", "walk-in [name]" → lead_create (parameters: {{"contact_name": "...", "phone": "...", "email": "..."}})
    "mark [name] as lost", "[name] not interested", "[name] dropped out of pipeline" → lead_mark_lost (parameters: {{"lead_name": "..."}})
    "mark [name] as won", "[name] officially joined", "[name] is a new member now" → lead_mark_won (parameters: {{"lead_name": "..."}})
- TASK / TODO MAPPING:
    "my tasks", "what's on my list", "show my todos", "what do I have to do" → task_list (parameters: {{}})
    "overdue tasks", "what tasks are late" → task_list (parameters: {{"overdue": true}})
    "create a task", "add a to-do", "remind me to", "make a task" → task_create (parameters: {{"name": "..."}})
    "mark task done", "complete task", "finished the task", "check off task" → task_complete (parameters: {{"task_name": "..."}})
    "update task deadline", "move task due date", "add note to task" → task_update (parameters: {{"task_name": "..."}})
- CALENDAR MAPPING:
    "what's on my calendar", "show calendar events", "my schedule today" → calendar_event_list (parameters: {{"date_filter": "today"}})
    "calendar this week", "events this week" → calendar_event_list (parameters: {{"date_filter": "this_week"}})
    "create calendar event", "add event", "schedule a meeting" → calendar_event_create (parameters: {{"name": "...", "start": "..."}})
    "cancel event", "remove calendar event", "delete event" → calendar_event_cancel (parameters: {{"event_name": "..."}})
- INVOICE / BILLING MAPPING:
    "has [name] paid", "check [name]'s invoice", "is [name] up to date", "payment status for [name]" → invoice_lookup (parameters: {{"member_name": "..."}})
    "show overdue invoices", "who hasn't paid", "unpaid invoices", "who is behind on payments", "members with overdue payments", "list members with overdue payments", "who owes money", "outstanding balances" → invoice_list (parameters: {{"filter": "overdue"}})
    "show unpaid invoices" → invoice_list (parameters: {{"filter": "unpaid"}})
    "show paid invoices" → invoice_list (parameters: {{"filter": "paid"}})
- COMMUNICATION MAPPING:
    "email [name]: [message]", "send email to [name]", "write an email to [name]" → send_email (parameters: {{"member_name": "...", "subject": "...", "body": "..."}})
    "text [name]: [message]", "send SMS to [name]", "SMS [name]" → send_sms (parameters: {{"member_name": "...", "body": "..."}})
    "email all members about", "blast email", "send email to everyone" → email_blast (parameters: {{"subject": "...", "body": "..."}}) (admin only)
    "text all members", "SMS blast", "send SMS to everyone" → sms_blast (parameters: {{"body": "..."}}) (admin only)
- ACTIVITY MAPPING:
    "schedule a follow-up", "my follow-ups", "what activities do I have", "pending reminders" → activity_list (parameters: {{}})
    "schedule a follow-up call with [name]", "add an activity for [name]", "set a reminder to" → activity_create (parameters: {{"summary": "...", "date_deadline": "..."}})
- PHONE NUMBER LOOKUP — IMPORTANT: a bare phone number (e.g. "555-1234", "+1 (408) 867-5309", "4085559876")
  WITHOUT the words "lead", "prospect", or "pipeline" in the same message → ALWAYS phone_lookup:
    "555-1234" → phone_lookup (parameters: {{"phone": "555-1234"}})
    "+1 (408) 867-5309" → phone_lookup (parameters: {{"phone": "+1 (408) 867-5309"}})
    "look up 4085551234" → phone_lookup (parameters: {{"phone": "4085551234"}})
    "what student has number 555-0112" → phone_lookup (parameters: {{"phone": "555-0112"}})
  phone_lookup searches BOTH members AND leads and returns all matches.
  NEVER map a bare phone number to lead_lookup unless the user explicitly says "lead", "prospect", or "pipeline".
- CRITICAL: For parameter VALUES, always use the ACTUAL values from the user's input, NEVER use template placeholders like {{member_name}}, {{target_belt}}, {{class_name}} etc.
  Example of CORRECT parameters: {{"member_name": "Mary Smith", "target_belt": "Blue Belt"}}
  Example of WRONG parameters: {{"member_name": "{{member_name}}", "target_belt": "{{target_belt}}"}}

Response format:
{{
  "intent_type": "<intent type>",
  "parameters": {{...}},
  "confidence": 0.0-1.0,
  "resolved_entities": {{
    "member_id": <id or null>,
    "member_name": "<resolved name>",
    "session_id": <id or null>,
    "class_name": "<resolved name>",
    ...
  }},
  "reasoning": "<brief explanation of why you chose this intent>"
}}

COMPOUND COMMANDS: If the user clearly requests multiple sequential actions in one message,
return an "intents" array instead of a single intent object:

{{
  "intents": [
    {{"intent_type": "<type>", "parameters": {{...}}, "confidence": 0.0-1.0, "resolved_entities": {{}}}},
    ...
  ],
  "reasoning": "<why you split into multiple intents>"
}}

Only use the "intents" array when the user's message contains two or more clearly DIFFERENT
action types (e.g. "check in John AND create a lead for Sarah"). Never split a
single action with multiple parameters into compound intents. If in doubt, return a single intent.

BULK OPERATIONS (same action, multiple targets):
When the user wants the SAME action for multiple people, use a single intent with array parameters
instead of compound intents. This is faster and handled as one confirmed batch:

  "check in John, Mary, and Bob"
      → {{"intent_type": "attendance_checkin", "parameters": {{"member_names": ["John", "Mary", "Bob"]}}, "confidence": 0.95}}

  "check out Maria and Carlos"
      → {{"intent_type": "attendance_checkout", "parameters": {{"member_names": ["Maria", "Carlos"]}}, "confidence": 0.95}}

  "create leads for John Smith and Sarah Jones"
      → {{"intent_type": "lead_create", "parameters": {{"contacts": [{{"contact_name": "John Smith"}}, {{"contact_name": "Sarah Jones"}}]}}, "confidence": 0.90}}

  "create leads for John (555-1234) and Sarah (555-5678)"
      → {{"intent_type": "lead_create", "parameters": {{"contacts": [{{"contact_name": "John", "phone": "555-1234"}}, {{"contact_name": "Sarah", "phone": "555-5678"}}]}}, "confidence": 0.90}}

Use compound intents ONLY when the actions are different (e.g. "check in John AND text his guardian").
Use bulk array params when the actions are the same (e.g. "check in John, Mary, Bob").

User input: {user_input}
"""

_CONVERSATION_SYSTEM_PROMPT = """\
You are a helpful AI assistant for a martial arts dojo. You help instructors and staff with:
- Looking up student/member information
- Managing class enrollments and attendance
- Tracking belt ranks and promotions
- Managing subscriptions and contracts
- Communicating with parents/guardians

{db_context}

Always be helpful, concise, and accurate. Use the database context to provide real information.
When you need to perform an action (not just answer a question), output a structured intent block.

═══════════════════════════════════════════════════════════
AVAILABLE INTENTS — descriptions, parameters, and examples
═══════════════════════════════════════════════════════════
{intent_definitions}

═══════════════════════════════════════════════════════════
CRITICAL INTENT DISAMBIGUATION — read carefully
═══════════════════════════════════════════════════════════
  • member_enroll      = reserve a spot in ONE specific class SESSION (e.g. "enroll in today's 6pm class")
  • course_enroll      = add to the PERMANENT ROSTER of a course template (e.g. "add to roster", "add to the course")
  • belt_lookup        = look up what belt rank someone currently HAS (e.g. "what belt is John?", "show belt ranks")
  • belt_test_register = REGISTER a member to TAKE a belt promotion TEST (e.g. "register for a belt test", "sign up for blue belt test")
  • subscription_pause   = temporarily pause an EXISTING active subscription
  • subscription_resume  = resume a paused subscription
  • subscription_cancel  = permanently cancel a subscription

KEYWORD RULES (override any other reasoning):
  - "roster", "course roster", "permanent roster", "add to the course" → ALWAYS course_enroll
  - "today's class", "this session", "session at Xpm", "book for class today" → member_enroll
  - "register for a belt test", "sign up for belt test", "schedule belt test", "belt test" + register/sign up → ALWAYS belt_test_register (NOT belt_lookup)
  - "what belt is", "what rank", "belt rank" without register/test action → belt_lookup
  - "pause", "put on hold", "hold subscription" → subscription_pause
  - "resume", "reactivate subscription" → subscription_resume
  - phone number (digits with dashes, dots, or parentheses) WITHOUT "lead"/"prospect"/"pipeline" in message
    → ALWAYS phone_lookup with {{"phone": "<number>"}} — searches both members AND leads
    Examples: "555-1234", "+1 (408) 867-5309", "4085551234", "555-0112" → phone_lookup

═══════════════════════════════════════════════════════════
OUTPUT FORMAT FOR ACTIONS
═══════════════════════════════════════════════════════════
For actions, include at the very end of your response (ALWAYS use REAL values from the user's message,
NEVER template placeholders like {{{{member_name}}}}, {{{{class_name}}}} etc.):
{intent_start}
{{"intent_type": "attendance_checkin", "parameters": {{"member_name": "john smith"}}, "confidence": 0.95}}
{intent_end}

Confidence guide:
  0.95+ = exact keyword match or very clear phrasing
  0.85  = clear but slightly ambiguous
  0.70  = probable but uncertain — still emit the intent block

INTENT BLOCK RULES:
  • Emit an intent block when you can confidently match a specific intent (confidence ≥ 0.70).
  • For lookups and read-only queries, always prefer emitting an intent block so the system can
    fetch LIVE data — do not answer from your training data.
  • If no intent matches confidently, answer the question directly from the database context
    provided above. Do NOT emit an intent block for unknown or unrecognised queries.

AGGREGATE / STATS QUERY MAPPING — use these intents for count/summary questions:
  "how many active students", "total active members", "student count"
      → {{"intent_type": "member_lookup", "parameters": {{"membership_state": "active"}}, "confidence": 0.90}}
  "subscriptions ending", "contracts expiring", "how many contracts are ending",
  "who has a contract ending", "expiring memberships", "up for renewal", "members renewing"
      → {{"intent_type": "subscription_expiring", "parameters": {{}}, "confidence": 0.95}}
  "members at risk", "who hasn't come in", "inactive members", "who hasn't attended"
      → {{"intent_type": "at_risk_members", "parameters": {{}}, "confidence": 0.92}}
  "students enrolled today", "who checked in", "today's attendance", "show today's class list"
      → {{"intent_type": "schedule_today", "parameters": {{}}, "confidence": 0.92}}
  "all members", "list all students", "show everyone"
      → {{"intent_type": "member_lookup", "parameters": {{}}, "confidence": 0.88}}

BIRTHDAY MAPPING:
  "whose birthday is coming up", "upcoming birthdays", "birthdays this month"
      → {{"intent_type": "birthday_upcoming", "parameters": {{"days": 30}}, "confidence": 0.92}}
  "birthdays this week", "any birthdays soon"
      → {{"intent_type": "birthday_upcoming", "parameters": {{"days": 7}}, "confidence": 0.92}}
  "when is [name]'s birthday", "[name]'s birthday", "how old is [name]"
      → {{"intent_type": "member_lookup", "parameters": {{"member_name": "..."}}, "confidence": 0.90}}

CRM / LEAD PIPELINE MAPPING:
  "show leads", "who are our prospects", "find lead [name]", "look up [name] lead"
      → {{"intent_type": "lead_lookup", "parameters": {{"lead_name": "..."}}, "confidence": 0.90}}
  "pipeline summary", "how many leads", "leads by stage", "show pipeline"
      → {{"intent_type": "pipeline_summary", "parameters": {{}}, "confidence": 0.92}}
  "upcoming trials", "who has a trial", "trial schedule", "trials this week"
      → {{"intent_type": "trial_schedule", "parameters": {{}}, "confidence": 0.92}}
  "qualify lead [name]", "move [name] to qualified"
      → {{"intent_type": "lead_qualify", "parameters": {{"lead_name": "..."}}, "confidence": 0.90}}
  "mark trial attended", "[name] came in for trial", "[name] showed up"
      → {{"intent_type": "lead_mark_attended", "parameters": {{"lead_name": "..."}}, "confidence": 0.90}}
  "convert [name] to member", "make [name] a member"
      → {{"intent_type": "lead_convert", "parameters": {{"lead_name": "..."}}, "confidence": 0.92}}
  "add new prospect [name]", "new lead for [name]", "create lead [name]", "walk-in [name]"
      → {{"intent_type": "lead_create", "parameters": {{"contact_name": "...", "phone": "...", "email": "..."}}, "confidence": 0.90}}
  "mark [name] as lost", "[name] not interested", "[name] dropped out"
      → {{"intent_type": "lead_mark_lost", "parameters": {{"lead_name": "..."}}, "confidence": 0.90}}
  "mark [name] as won", "[name] officially joined", "[name] is now a member"
      → {{"intent_type": "lead_mark_won", "parameters": {{"lead_name": "..."}}, "confidence": 0.90}}

TASK / TODO MAPPING:
  "my tasks", "what's on my list", "show todos", "what do I need to do"
      → {{"intent_type": "task_list", "parameters": {{}}, "confidence": 0.92}}
  "overdue tasks", "late tasks"
      → {{"intent_type": "task_list", "parameters": {{"overdue": true}}, "confidence": 0.92}}
  "create a task", "add a reminder", "make a to-do", "remind me to"
      → {{"intent_type": "task_create", "parameters": {{"name": "..."}}, "confidence": 0.90}}
  "mark task done", "complete the task", "finished the task", "check off"
      → {{"intent_type": "task_complete", "parameters": {{"task_name": "..."}}, "confidence": 0.90}}
  "update task deadline", "move due date", "add note to task"
      → {{"intent_type": "task_update", "parameters": {{"task_name": "..."}}, "confidence": 0.88}}

CALENDAR MAPPING:
  "what's on my calendar today", "my schedule", "show events today"
      → {{"intent_type": "calendar_event_list", "parameters": {{"date_filter": "today"}}, "confidence": 0.92}}
  "calendar this week", "events this week", "what's happening this week"
      → {{"intent_type": "calendar_event_list", "parameters": {{"date_filter": "this_week"}}, "confidence": 0.92}}
  "create a calendar event", "add event to calendar", "schedule a meeting"
      → {{"intent_type": "calendar_event_create", "parameters": {{"name": "...", "start": "..."}}, "confidence": 0.90}}
  "cancel event", "delete calendar event", "remove the meeting"
      → {{"intent_type": "calendar_event_cancel", "parameters": {{"event_name": "..."}}, "confidence": 0.90}}

INVOICE / BILLING MAPPING:
  "has [name] paid", "check [name]'s account", "is [name] up to date", "payment status"
      → {{"intent_type": "invoice_lookup", "parameters": {{"member_name": "..."}}, "confidence": 0.90}}
  "show overdue invoices", "who hasn't paid", "who's behind on payments"
      → {{"intent_type": "invoice_list", "parameters": {{"filter": "overdue"}}, "confidence": 0.92}}
  "unpaid invoices", "all unpaid accounts"
      → {{"intent_type": "invoice_list", "parameters": {{"filter": "unpaid"}}, "confidence": 0.90}}

COMMUNICATION MAPPING:
  "email [name]: [message]", "send email to [name] about", "write email to"
      → {{"intent_type": "send_email", "parameters": {{"member_name": "...", "subject": "...", "body": "..."}}, "confidence": 0.92}}
  "text [name]: [message]", "SMS [name]", "send a text to [name]"
      → {{"intent_type": "send_sms", "parameters": {{"member_name": "...", "body": "..."}}, "confidence": 0.92}}
  "email all members", "blast email", "send announcement email" (admin only)
      → {{"intent_type": "email_blast", "parameters": {{"subject": "...", "body": "..."}}, "confidence": 0.90}}
  "text all members", "SMS blast", "mass text" (admin only)
      → {{"intent_type": "sms_blast", "parameters": {{"body": "..."}}, "confidence": 0.90}}

ACTIVITY MAPPING:
  "what follow-ups do I have", "show activities", "pending reminders"
      → {{"intent_type": "activity_list", "parameters": {{}}, "confidence": 0.90}}
  "schedule a follow-up with [name]", "add activity for", "remind me to call", "set a reminder"
      → {{"intent_type": "activity_create", "parameters": {{"summary": "...", "date_deadline": "..."}}, "confidence": 0.88}}

PROGRAMS & COURSES MAPPING:
  "what programs do we offer", "list programs", "show all programs"
      → {{"intent_type": "program_list", "parameters": {{}}, "confidence": 0.92}}
  "show courses", "list classes", "what courses are available", "class types"
      → {{"intent_type": "class_template_list", "parameters": {{}}, "confidence": 0.90}}
  "courses in [program]", "BJJ courses", "karate classes"
      → {{"intent_type": "class_template_list", "parameters": {{"program_name": "..."}}, "confidence": 0.90}}

BELT TEST MAPPING:
  "when is the next belt test", "upcoming belt tests", "show belt tests"
      → {{"intent_type": "belt_test_list", "parameters": {{}}, "confidence": 0.92}}
  "belt tests for [program]", "TKD belt test schedule"
      → {{"intent_type": "belt_test_list", "parameters": {{"program_name": "..."}}, "confidence": 0.90}}
  "who is registered for the belt test", "who signed up for belt testing", "belt test registrations"
      → {{"intent_type": "belt_test_registration_list", "parameters": {{}}, "confidence": 0.92}}
  "is [name] registered for the belt test", "who's testing next"
      → {{"intent_type": "belt_test_registration_list", "parameters": {{}}, "confidence": 0.90}}
  IMPORTANT: "who is registered" or "who signed up" = belt_test_registration_list (READ).
             "register [name] for belt test" = belt_test_register (WRITE). Do NOT confuse them.

ENROLLMENT & ONBOARDING MAPPING:
  "who is enrolled in [program]", "[name]'s enrollment", "show enrollments"
      → {{"intent_type": "program_enrollment_lookup", "parameters": {{"program_name": "..."}}, "confidence": 0.90}}
  "[name]'s onboarding status", "who hasn't finished onboarding", "onboarding progress"
      → {{"intent_type": "onboarding_status", "parameters": {{}}, "confidence": 0.90}}
  "is [name] onboarded", "where is [name] in onboarding"
      → {{"intent_type": "onboarding_status", "parameters": {{"member_name": "..."}}, "confidence": 0.90}}

POINTS & CREDITS MAPPING:
  "how many points does [name] have", "[name]'s points", "points balance"
      → {{"intent_type": "points_lookup", "parameters": {{"member_name": "..."}}, "confidence": 0.92}}
  "[name]'s credits", "credit balance for [name]", "how many credits"
      → {{"intent_type": "credit_lookup", "parameters": {{"member_name": "..."}}, "confidence": 0.92}}

HOUSEHOLD MAPPING:
  "[name]'s family", "who's in [name]'s household", "show household for [name]"
      → {{"intent_type": "household_lookup", "parameters": {{"member_name": "..."}}, "confidence": 0.90}}

SOCIAL MEDIA MAPPING:
  "show recent posts", "social media posts", "what have we posted"
      → {{"intent_type": "social_post_list", "parameters": {{}}, "confidence": 0.90}}
  "any failed posts", "posts that failed", "social errors"
      → {{"intent_type": "social_post_list", "parameters": {{"state": "error"}}, "confidence": 0.90}}

AUTO-ENROLL RULES MAPPING:
  "show auto-enroll rules", "who has auto-enroll", "list auto-enrollments"
      → {{"intent_type": "course_auto_enroll_list", "parameters": {{}}, "confidence": 0.90}}
  "[name]'s auto-enroll rules", "what is [name] auto-enrolled in"
      → {{"intent_type": "course_auto_enroll_list", "parameters": {{"member_name": "..."}}, "confidence": 0.90}}

AI CALLING CAMPAIGN MAPPING:
  "show campaigns", "list campaigns", "what campaigns are running"
      → {{"intent_type": "campaign_list", "parameters": {{}}, "confidence": 0.90}}
  "active campaigns", "running campaigns"
      → {{"intent_type": "campaign_list", "parameters": {{"state": "running"}}, "confidence": 0.90}}

KIOSK ANNOUNCEMENT MAPPING:
  "show kiosk announcements", "list announcements", "what's on the kiosk"
      → {{"intent_type": "kiosk_announcement_list", "parameters": {{}}, "confidence": 0.90}}

Example intent blocks for common queries:
  "What belt is Maria?"         → {{"intent_type": "belt_lookup", "parameters": {{"member_name": "Maria"}}, "confidence": 0.95}}
  "Register Maria for belt test" → {{"intent_type": "belt_test_register", "parameters": {{"member_name": "Maria"}}, "confidence": 0.95}}
  "Add John to Thursday's class" → {{"intent_type": "member_enroll", "parameters": {{"member_name": "John", "date": "thursday"}}, "confidence": 0.90}}
  "Add John to the kids roster"  → {{"intent_type": "course_enroll", "parameters": {{"member_name": "John"}}, "confidence": 0.90}}
  "What's today's schedule?"    → {{"intent_type": "schedule_today", "parameters": {{}}, "confidence": 0.98}}
"""


class AIProcessorIntentExt(models.AbstractModel):
    """Extends ai.processor with structured intent parsing."""

    _inherit = "ai.processor"

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Intent Parsing
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def process_intent_query(self, text, role="instructor", db_context=None):
        """
        Parse natural language text into a structured intent.
        
        Args:
            text: User's natural language input
            role: User role for filtering available intents (kiosk/instructor/admin)
            db_context: Pre-built database context string (optional)
        
        Returns:
            dict: {
                "intent_type": str,
                "parameters": dict,
                "confidence": float,
                "resolved_entities": dict,
                "reasoning": str,
                "raw_response": str (optional, for debugging)
            }
        """
        text = (text or "").strip()
        if not text:
            return self._empty_intent("No input provided")

        # Get intent definitions for this role
        IntentSchema = self.env["ai.intent.schema"]
        intent_defs = IntentSchema.get_intent_definitions_for_llm(role)

        if not intent_defs:
            return self._empty_intent("No intents configured for this role")

        # Build database context if not provided
        if db_context is None:
            db_context = self._build_dojo_db_context(text)

        # ── Vector pre-filter: narrow intents via similarity search ────────
        vector_suggestions = []
        identified_domain = None
        intent_defs, vector_suggestions, identified_domain = self._apply_vector_filter(
            text, intent_defs, role
        )

        # ── Agent-scoped system prompt (Phase 2) ─────────────────────────────
        # If the vector filter identified a domain, use that agent's focused
        # system prompt instead of the generic one.
        agent_obj = None
        if identified_domain and "ai.agent" in self.env:
            try:
                agent_obj = self.env["ai.agent"].get_agent_for_domain(identified_domain)
            except Exception:
                agent_obj = None

        # Format intent definitions for the prompt
        intent_defs_str = self._format_intent_definitions(intent_defs)

        # Build system prompt — use agent template if available
        if agent_obj and agent_obj.system_prompt_template:
            try:
                system_prompt = agent_obj.system_prompt_template.format(
                    intent_definitions=intent_defs_str,
                    db_context=db_context or "No specific context available.",
                    user_input=text,
                )
                _logger.info(
                    "Using agent system prompt for domain '%s' (%s)",
                    identified_domain,
                    agent_obj.name,
                )
            except Exception as e:
                _logger.warning("Agent prompt formatting failed, using default: %s", e)
                system_prompt = _INTENT_SYSTEM_PROMPT.format(
                    intent_definitions=intent_defs_str,
                    db_context=db_context or "No specific context available.",
                    user_input=text,
                )
        else:
            system_prompt = _INTENT_SYSTEM_PROMPT.format(
                intent_definitions=intent_defs_str,
                db_context=db_context or "No specific context available.",
                user_input=text,
            )

        # Try to get structured response
        try:
            provider = self._get_provider()

            if provider in ("openai", "odoo_native"):
                raw_response = self._process_intent_openai(text, system_prompt)
            elif provider == "gemini":
                raw_response = self._process_intent_gemini(text, system_prompt)
            else:
                # Fallback to regular processing with intent block extraction
                raw_response = self.process_query(text, {"system_prompt": system_prompt})

            # Parse the structured response
            intent = self._parse_intent_response(raw_response)
            intent["raw_response"] = raw_response

            # Attach vector routing metadata
            if vector_suggestions:
                intent["vector_suggestions"] = vector_suggestions
            if identified_domain:
                intent["domain_agent"] = identified_domain
            if agent_obj:
                intent["agent_name"] = agent_obj.name

            return intent

        except UserError:
            raise
        except Exception as e:
            _logger.error("Intent parsing failed: %s", e, exc_info=True)
            return self._empty_intent(f"Parsing error: {e}")

    @api.model
    def process_conversational_query(self, text, role="instructor", db_context=None):
        """
        Process a conversational query that may or may not contain an action intent.
        
        Returns both the AI response text and any extracted intent.
        
        Args:
            text: User's natural language input
            role: User role
            db_context: Pre-built database context string (optional)
        
        Returns:
            dict: {
                "response": str,
                "intent": dict | None,
                "has_intent": bool
            }
        """
        text = (text or "").strip()
        if not text:
            return {"response": "Please say or type something.", "intent": None, "has_intent": False}

        # Build database context if not provided
        if db_context is None:
            db_context = self._build_dojo_db_context(text)

        # Fetch intent definitions from DB (same source as process_intent_query)
        IntentSchema = self.env["ai.intent.schema"]
        intent_defs = IntentSchema.get_intent_definitions_for_llm(role)

        # ── Vector pre-filter: narrow intents via similarity search ────────
        if intent_defs:
            intent_defs, _, _ = self._apply_vector_filter(text, intent_defs, role)

        intent_defs_str = self._format_intent_definitions(intent_defs) if intent_defs else "(none configured)"

        # Build system prompt for conversational mode
        system_prompt = _CONVERSATION_SYSTEM_PROMPT.format(
            db_context=db_context or "No specific context available.",
            intent_definitions=intent_defs_str,
            intent_start=_INTENT_START,
            intent_end=_INTENT_END,
        )

        try:
            # Get AI response — call provider directly so our system_prompt is used,
            # bypassing the base process_query which ignores context["system_prompt"].
            provider = self._get_provider()
            if provider in ("openai", "odoo_native"):
                raw_response = self._process_conversational_openai(text, system_prompt)
            elif provider == "gemini":
                raw_response = self._process_conversational_gemini(text, system_prompt)
            else:
                raw_response = self.process_query(text, {"system_prompt": system_prompt})

            # Extract intent block if present
            response_text, intent = self._extract_intent_block(raw_response)

            return {
                "response": response_text.strip(),
                "intent": intent,
                "has_intent": intent is not None,
            }

        except UserError:
            raise
        except Exception as e:
            _logger.error("Conversational query failed: %s", e, exc_info=True)
            return {
                "response": f"Sorry, I encountered an error: {e}",
                "intent": None,
                "has_intent": False,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Conversational (free-form text) API calls
    # ─────────────────────────────────────────────────────────────────────────

    def _process_conversational_openai(self, text, system_prompt):
        """Call OpenAI in normal (non-JSON) mode for a conversational reply."""
        api_key = self.env["ir.config_parameter"].sudo().get_str("openai.api_key") or \
                  self.env["ir.config_parameter"].sudo().get_str("elevenlabs_connector.openai_api_key")

        if not api_key:
            raise UserError("OpenAI API key not configured.")

        url = "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.7,
            "max_tokens": 1000,
            # No response_format — free-form text output
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = requests.post(url, headers=headers, data=json_data, timeout=30)

            if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = "utf-8"

            response.raise_for_status()
            result = json.loads(response.text)

            if "choices" in result and len(result["choices"]) > 0:
                return self._sanitize_text(result["choices"][0]["message"]["content"])
            else:
                raise UserError("Unexpected response format from OpenAI API")

        except requests.exceptions.RequestException as e:
            _logger.error("OpenAI conversational API request failed: %s", e, exc_info=True)
            raise UserError(f"OpenAI API error: {e}")

    def _process_conversational_gemini(self, text, system_prompt):
        """Call Gemini in normal (free-form text) mode for a conversational reply."""
        api_key = self.env["ir.config_parameter"].sudo().get_str("gemini.api_key") or \
                  self.env["ir.config_parameter"].sudo().get_str("elevenlabs_connector.gemini_api_key")

        if not api_key:
            raise UserError("Gemini API key not configured.")

        model = "gemini-flash-latest"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        full_prompt = f"{system_prompt}\n\nUser: {text}"

        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
        }

        headers = {"Content-Type": "application/json; charset=utf-8"}

        try:
            json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = requests.post(url, headers=headers, data=json_data,
                                     params={"key": api_key}, timeout=30)

            if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = "utf-8"

            response.raise_for_status()
            result = json.loads(response.text)

            if "candidates" in result and len(result["candidates"]) > 0:
                content = result["candidates"][0]["content"]["parts"][0].get("text", "")
                return self._sanitize_text(content)
            else:
                raise UserError("No response from Gemini API")

        except requests.exceptions.RequestException as e:
            _logger.error("Gemini conversational API request failed: %s", e, exc_info=True)
            raise UserError(f"Gemini API error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # OpenAI Intent Parsing (JSON Mode)
    # ─────────────────────────────────────────────────────────────────────────

    def _process_intent_openai(self, text, system_prompt):
        """Process intent using OpenAI with JSON mode for structured output."""
        api_key = self.env["ir.config_parameter"].sudo().get_str("openai.api_key") or \
                  self.env["ir.config_parameter"].sudo().get_str("elevenlabs_connector.openai_api_key")

        if not api_key:
            raise UserError("OpenAI API key not configured.")

        url = "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": "gpt-4o-mini",  # Use a model that supports JSON mode
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,  # Lower temperature for more consistent parsing
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},  # JSON mode
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            response = requests.post(url, headers=headers, data=json_data, timeout=30)

            if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = "utf-8"

            response.raise_for_status()
            result = json.loads(response.text)

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                return self._sanitize_text(content)
            else:
                raise UserError("Unexpected response format from OpenAI API")

        except requests.exceptions.RequestException as e:
            _logger.error("OpenAI intent API request failed: %s", e, exc_info=True)
            raise UserError(f"OpenAI API error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Gemini Intent Parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _process_intent_gemini(self, text, system_prompt):
        """Process intent using Gemini."""
        api_key = self.env["ir.config_parameter"].sudo().get_str("gemini.api_key") or \
                  self.env["ir.config_parameter"].sudo().get_str("elevenlabs_connector.gemini_api_key")

        if not api_key:
            raise UserError("Gemini API key not configured.")

        # Use a stable model
        model = "gemini-flash-latest"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        # Gemini doesn't have native JSON mode, so we instruct it firmly
        full_prompt = f"{system_prompt}\n\nIMPORTANT: Respond with ONLY valid JSON, no markdown or explanation."

        payload = {
            "contents": [{
                "parts": [{"text": f"{full_prompt}\n\nUser: {text}"}]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
            }
        }

        headers = {"Content-Type": "application/json; charset=utf-8"}

        try:
            json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            response = requests.post(
                url, headers=headers, data=json_data,
                params={"key": api_key}, timeout=30
            )

            if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = "utf-8"

            response.raise_for_status()
            result = json.loads(response.text)

            if "candidates" in result and len(result["candidates"]) > 0:
                content = result["candidates"][0]["content"]["parts"][0].get("text", "")
                return self._sanitize_text(content)
            else:
                raise UserError("No response from Gemini API")

        except requests.exceptions.RequestException as e:
            _logger.error("Gemini intent API request failed: %s", e, exc_info=True)
            raise UserError(f"Gemini API error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Response Parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_intent_response(self, raw_response):
        """Parse the AI response into a structured intent dict."""
        if not raw_response:
            return self._empty_intent("Empty response")

        # Try to parse as JSON directly
        try:
            # Clean up potential markdown code blocks
            cleaned = raw_response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            intent = json.loads(cleaned)

            # Compound response — return raw dict with "intents" key intact
            # All validation (confidence, role permissions, max chain) happens in
            # handle_compound_command() on the service side.
            if "intents" in intent:
                return intent

            # Validate required fields
            if "intent_type" not in intent:
                intent["intent_type"] = "unknown"
            if "parameters" not in intent:
                intent["parameters"] = {}
            if "confidence" not in intent:
                intent["confidence"] = 0.5
            if "resolved_entities" not in intent:
                intent["resolved_entities"] = {}
            if "reasoning" not in intent:
                intent["reasoning"] = ""

            return intent

        except json.JSONDecodeError:
            # Try to extract JSON from the response
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw_response, re.DOTALL)
            if json_match:
                try:
                    intent = json.loads(json_match.group())
                    # Compound response — return raw dict with "intents" key intact
                    if "intents" in intent:
                        return intent
                    intent.setdefault("intent_type", "unknown")
                    intent.setdefault("parameters", {})
                    intent.setdefault("confidence", 0.5)
                    intent.setdefault("resolved_entities", {})
                    intent.setdefault("reasoning", "")
                    return intent
                except json.JSONDecodeError:
                    pass

            return self._empty_intent("Could not parse response as JSON")

    def _extract_intent_block(self, raw_response):
        """
        Extract intent block from conversational response.
        
        Returns:
            tuple: (response_text, intent_dict or None)
        """
        if not raw_response:
            return "", None

        # Look for intent block markers
        start_idx = raw_response.find(_INTENT_START)
        end_idx = raw_response.find(_INTENT_END)

        if start_idx == -1:
            # No intent block found
            return raw_response, None
        if end_idx == -1 or end_idx <= start_idx:
            end_idx = len(raw_response)

        # Extract text before and after the intent block
        response_text = raw_response[:start_idx].strip()

        # Extract and parse the intent JSON
        intent_json = raw_response[start_idx + len(_INTENT_START):end_idx].strip()

        try:
            intent = json.loads(intent_json)
            intent.setdefault("intent_type", "unknown")
            intent.setdefault("parameters", {})
            intent.setdefault("confidence", 0.5)
            return response_text, intent
        except json.JSONDecodeError:
            _c = intent_json.strip()
            _s = _c.find("{")
            _e = _c.rfind("}")
            if _s != -1 and _e > _s:
                try:
                    intent = json.loads(_c[_s:_e + 1])
                    intent.setdefault("intent_type", "unknown")
                    intent.setdefault("parameters", {})
                    intent.setdefault("confidence", 0.5)
                    return response_text, intent
                except json.JSONDecodeError:
                    pass
            _logger.warning("Could not parse intent block: %s", intent_json[:150])
            return raw_response, None

    def _empty_intent(self, reason=""):
        """Return an empty/unknown intent."""
        return {
            "intent_type": "unknown",
            "parameters": {},
            "confidence": 0.0,
            "resolved_entities": {},
            "reasoning": reason,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Vector Pre-Filter
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _apply_vector_filter(self, query_text, intent_defs, role=None):
        """
        Use the vector store (if available) to narrow intent definitions.

        When ``ai_vector`` is installed and has embeddings, this performs
        a cosine similarity search and returns only the top-K matching
        intents, dramatically reducing prompt size.

        Falls back gracefully to the full intent list when:
        - ``ai_vector`` is not installed
        - No embeddings have been built yet
        - The vector lookup returns no confident matches

        Args:
            query_text: User's natural language query.
            intent_defs: Full list of intent definition dicts.
            role: User role for logging.

        Returns:
            tuple: (filtered_intent_defs, suggestions)
                - filtered_intent_defs: list of intent dicts (filtered or original)
                - suggestions: list of dicts with ``intent_type`` + ``similarity``
                  for "did you mean?" UI hints (empty if not applicable)
        """
        suggestions = []
        identified_domain = None

        # Check if ai_vector module is installed
        if "ai.vector.cache" not in self.env:
            return intent_defs, suggestions, identified_domain

        try:
            VectorCache = self.env["ai.vector.cache"]
            results = VectorCache.find_similar_cached(
                query_text, top_k=8, threshold=0.35
            )

            if not results:
                # No confident matches — fall back to full intent list
                _logger.debug(
                    "Vector filter: no matches above threshold for '%s' — using full list",
                    query_text[:60],
                )
                return intent_defs, suggestions, identified_domain

            # Extract top matches
            top_types = {r["intent_type"] for r in results}
            top_score = results[0]["similarity"] if results else 0.0

            # Identify the dominant domain agent from top results.
            # When the top hit is high-confidence (>= 0.55), use its domain
            # directly so that a clear winner isn't outvoted by many weaker
            # hits from a neighbouring domain (e.g. enrollment intents beating
            # attendance when the user asks about class schedules).
            # Otherwise fall back to score-weighted voting.
            top_result_domain = results[0].get("domain_agent") if results else None
            if top_result_domain and top_score >= 0.55:
                identified_domain = top_result_domain
            else:
                domain_scores = {}
                for r in results:
                    d = r.get("domain_agent")
                    if d:
                        domain_scores[d] = domain_scores.get(d, 0.0) + r["similarity"]
                if domain_scores:
                    identified_domain = max(domain_scores, key=domain_scores.get)

            _logger.info(
                "Vector filter: top=%s (%.3f), domain=%s, matched %d/%d intents for role=%s",
                results[0]["intent_type"],
                top_score,
                identified_domain,
                len(top_types),
                len(intent_defs),
                role,
            )

            # Build "did you mean?" suggestions for mid-confidence matches
            for r in results:
                if 0.40 <= r["similarity"] < 0.70:
                    suggestions.append({
                        "intent_type": r["intent_type"],
                        "similarity": r["similarity"],
                        "domain_agent": r.get("domain_agent"),
                    })

            # Person-name queries must always have member_lookup available.
            # Without it, "find Riley Lee" can vectorize to a CRM intent and
            # member_lookup gets excluded, causing "not found" on real members.
            _name_tokens = self._extract_name_tokens(query_text)
            _has_name = bool(_name_tokens)

            # If top score is very high (>= 0.65), we can be very aggressive
            # and send only the top 3 intents
            if top_score >= 0.65:
                top_types_strict = {r["intent_type"] for r in results[:3]}
                # Always include 'unknown' as an escape hatch
                top_types_strict.add("unknown")
                # Always include member_lookup when query contains a person name
                if _has_name:
                    top_types_strict.add("member_lookup")
                filtered = [d for d in intent_defs if d["intent_type"] in top_types_strict]
                if filtered:
                    _logger.info(
                        "Vector filter (high confidence): reduced %d → %d intents",
                        len(intent_defs),
                        len(filtered),
                    )
                    return filtered, suggestions, identified_domain

            # Normal filtering: send all matches above threshold
            # Always include 'unknown' as an escape hatch
            top_types.add("unknown")
            # Always include member_lookup when query contains a person name
            if _has_name:
                top_types.add("member_lookup")
            filtered = [d for d in intent_defs if d["intent_type"] in top_types]
            if filtered:
                _logger.info(
                    "Vector filter: reduced %d → %d intents",
                    len(intent_defs),
                    len(filtered),
                )
                return filtered, suggestions, identified_domain

        except Exception as e:
            _logger.warning(
                "Vector pre-filter failed, falling back to full list: %s", e
            )

        return intent_defs, suggestions, identified_domain

    # ─────────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ─────────────────────────────────────────────────────────────────────────

    def _format_intent_definitions(self, intent_defs):
        """Format intent definitions for the system prompt.

        Any curly braces in DB-sourced content are escaped so that the
        resulting string can be safely interpolated into a .format() template
        without causing spurious KeyErrors (e.g. '{phone}' in a description).
        """
        def _esc(s):
            """Escape curly braces that are not already doubled."""
            return str(s).replace("{", "{{").replace("}", "}}") if s else s

        lines = []
        for intent in intent_defs:
            lines.append(f"\n## {intent['intent_type']}: {intent['name']}")
            if intent.get("description"):
                lines.append(f"   Description: {_esc(intent['description'])}")
            if intent.get("parameters"):
                params = intent["parameters"]
                if isinstance(params, dict) and "properties" in params:
                    props = params["properties"]
                    required = params.get("required", [])
                    param_strs = []
                    for name, schema in props.items():
                        req = "(required)" if name in required else "(optional)"
                        desc = _esc(schema.get("description", ""))
                        param_strs.append(f"      - {name} {req}: {desc}")
                    if param_strs:
                        lines.append("   Parameters:")
                        lines.extend(param_strs)
            if intent.get("examples"):
                lines.append("   Examples:")
                for ex in intent["examples"][:6]:  # Up to 6 examples for better coverage
                    lines.append(f"      - \"{_esc(ex)}\"")

        return "\n".join(lines)

    def _build_dojo_db_context(self, query_text):
        """
        Build database context specific to dojo operations.
        
        Searches for members, classes, etc. based on query keywords.
        """
        context_parts = []
        query_lower = query_text.lower()

        try:
            # ── Member Search ─────────────────────────────────────────────────
            # Extract potential name tokens
            name_tokens = self._extract_name_tokens(query_text)
            if name_tokens:
                members = self._search_dojo_members(name_tokens)
                if members:
                    context_parts.append("=== Matching Members ===")
                    for m in members[:5]:
                        context_parts.append(self._format_member_context(m))

            # ── Today's Classes ───────────────────────────────────────────────
            if any(word in query_lower for word in ["class", "session", "today", "schedule", "tomorrow"]):
                sessions = self._get_upcoming_sessions()
                if sessions:
                    context_parts.append("\n=== Upcoming Class Sessions ===")
                    for s in sessions[:8]:
                        context_parts.append(self._format_session_context(s))

            # ── Belt Ranks ────────────────────────────────────────────────────
            if any(word in query_lower for word in ["belt", "rank", "promote", "promotion", "stripe"]):
                try:
                    ranks = self.env["dojo.belt.rank"].search([("active", "=", True)], order="sequence")
                    if ranks:
                        context_parts.append("\n=== Belt Ranks ===")
                        for r in ranks:
                            threshold = getattr(r, 'attendance_threshold', 0)
                            max_stripes = getattr(r, 'max_stripes', 0) or 0
                            stripe_info = f", max_stripes: {max_stripes}" if max_stripes > 0 else ""
                            context_parts.append(
                                f"- {r.name} (seq: {r.sequence}, threshold: {threshold}{stripe_info})"
                            )
                except Exception:
                    pass

            # ── Subscription Plans ────────────────────────────────────────────
            if any(word in query_lower for word in ["subscription", "plan", "contract", "membership"]):
                try:
                    plans = self.env["dojo.subscription.plan"].search([("active", "=", True)])
                    if plans:
                        context_parts.append("\n=== Subscription Plans ===")
                        for p in plans:
                            context_parts.append(f"- {p.name} (id: {p.id})")
                except Exception:
                    pass

        except Exception as e:
            _logger.warning("Error building DB context: %s", e)

        return "\n".join(context_parts) if context_parts else "No specific context available."

    def _extract_name_tokens(self, text):
        """
        Heuristic: extract likely name tokens from text.

        Case-insensitive so voice/STT input (all-lowercase) still matches
        members in the DB.  Action/stop-words are filtered out.
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
        tokens = [t for t in tokens if len(t) > 1]
        return " ".join(tokens[:3])

    def _search_dojo_members(self, name_tokens, limit=5):
        """Search dojo members by name tokens (includes archived/inactive members)."""
        try:
            return self.env["dojo.member"].with_context(active_test=False).search([("name", "ilike", name_tokens)], limit=limit)
        except Exception:
            return []

    def _format_member_context(self, member):
        """Format member info for context."""
        parts = [f"- {member.name} (id: {member.id}"]
        if hasattr(member, 'partner_id') and member.partner_id:
            flags = []
            if member.partner_id.is_student:
                flags.append("student")
            if member.partner_id.is_guardian:
                flags.append("guardian")
            if member.partner_id.is_minor:
                flags.append("minor")
            if flags:
                parts.append(f", flags: {'/'.join(flags)}")
        if hasattr(member, 'membership_state'):
            parts.append(f", state: {member.membership_state}")
        if hasattr(member, 'current_rank_id') and member.current_rank_id:
            rank = member.current_rank_id
            stripe_count = getattr(member, 'current_stripe_count', 0) or 0
            max_stripes = getattr(rank, 'max_stripes', 0) or 0
            rank_str = rank.name
            if max_stripes > 0:
                rank_str += f" ({stripe_count}/{max_stripes} stripes)"
            parts.append(f", rank: {rank_str}")
        parts.append(")")
        return "".join(parts)

    def _get_upcoming_sessions(self, limit=8):
        """Get upcoming class sessions."""
        try:
            from datetime import date as _date
            today = _date.today().isoformat()
            return self.env["dojo.class.session"].search([
                ("start_datetime", ">=", today + " 00:00:00"),
                ("start_datetime", "<=", today + " 23:59:59"),
            ], order="start_datetime asc", limit=limit)
        except Exception:
            return []

    def _format_session_context(self, session):
        """Format session info for context."""
        name = session.template_id.name if session.template_id else f"Session #{session.id}"
        time_str = session.start_datetime.strftime("%H:%M") if session.start_datetime else "?"
        return f"- {name} at {time_str} (id: {session.id}, enrolled: {session.seats_taken}/{session.capacity})"
