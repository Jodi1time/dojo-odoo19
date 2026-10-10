"""Fixed, authenticated actions. No caller-supplied ORM methods or authority."""
import hashlib
import json
import secrets

from psycopg2 import errors as pg_errors
from odoo import fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import KEY, KioskProblem, iso, record_id, session_version


class HubProblem(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise HubProblem("INVALID_COMMAND", 400)


def text(value, maximum=1500):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise HubProblem("INVALID_COMMAND", 400)
    return value.strip()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class HubService(models.AbstractModel):
    _name = "dojo.hub.service"
    _description = "Scoped Companion Hub Actions"

    def _principal(self, site_id):
        # Never use a supplied user/role/company. The HTTP session establishes uid.
        if self.env.user._is_public() or not self.env.user.active:
            raise HubProblem("UNAUTHENTICATED", 401)
        site = self.env["dojo.hub.site"].sudo().search([("id", "=", record_id(site_id)), ("active", "=", True)], limit=1)
        if not site or site.company_id not in self.env.user.company_ids:
            raise HubProblem("FORBIDDEN", 403)
        grant = self.env["dojo.hub.grant"].sudo().search([("site_id", "=", site.id), ("user_id", "=", self.env.uid), ("active", "=", True)], limit=1)
        if not grant:
            raise HubProblem("FORBIDDEN", 403)
        return site, grant

    def _role(self, grant, roles="owner manager instructor"):
        if grant.role not in roles.split():
            raise HubProblem("FORBIDDEN", 403)

    def _members(self, site, grant):
        domain = [("company_id", "=", site.company_id.id), ("active", "=", True)]
        if grant.role == "instructor":
            members = self.env["dojo.class.enrollment"].sudo().search([
                ("session_id", "in", grant.session_ids.ids), ("session_id.company_id", "=", site.company_id.id),
                ("status", "=", "registered")]).member_id
            domain.append(("id", "in", members.ids))
        elif grant.role == "guardian":
            bindings = self.env["dojo.hub.guardian"].sudo().search([("site_id", "=", site.id), ("partner_id", "=", self.env.user.partner_id.id), ("active", "=", True)])
            domain.append(("id", "in", (grant.member_ids & bindings.member_ids).ids))
        elif grant.role == "member":
            domain.append(("id", "in", grant.member_ids.ids))
        return self.env["dojo.member"].sudo().search(domain, limit=500)

    def _member(self, site, grant, value):
        member_id = record_id(value)
        member = self._members(site, grant).filtered(lambda m: m.id == member_id)
        if not member:
            raise HubProblem("MEMBER_UNAVAILABLE", 404)
        return member

    def _session(self, site, grant, value):
        domain = [("id", "=", record_id(value)), ("company_id", "=", site.company_id.id), ("template_id.company_id", "=", site.company_id.id), ("template_id.active", "=", True)]
        if grant.role == "instructor":
            domain.append(("id", "in", grant.session_ids.ids))
        session = self.env["dojo.class.session"].sudo().search(domain, limit=1)
        if not session:
            raise HubProblem("SESSION_UNAVAILABLE", 404)
        return session

    def _lock(self, record):
        record.flush_recordset()
        # _table is code-owned; a client can never select the model/table.
        self.env.cr.execute('SELECT id FROM "%s" WHERE id = %%s FOR UPDATE NOWAIT' % record._table, [record.id])
        record.invalidate_recordset()

    def _binding(self, message):
        binding = self.env["dojo.hub.guardian"].sudo().search([
            ("site_id", "=", message.site_id.id), ("channel", "=", message.channel),
            ("contact_ref", "=", message.contact_ref), ("active", "=", True)], limit=1)
        if not binding or not binding.allow_reply or (message.member_id and message.member_id not in binding.member_ids):
            raise HubProblem("GUARDIAN_REVIEW_REQUIRED")
        return binding

    def _message(self, site, grant, value, unresolved=False):
        message = self.env["dojo.hub.message"].sudo().search([("id", "=", record_id(value)), ("site_id", "=", site.id)], limit=1)
        if not message:
            raise HubProblem("MESSAGE_UNAVAILABLE", 404)
        if grant.role == "instructor" or not unresolved:
            if not message.member_id or not message.session_id:
                raise HubProblem("CLASS_REVIEW_REQUIRED")
            self._member(site, grant, str(message.member_id.id))
            self._session(site, grant, str(message.session_id.id))
        return message

    def _context(self, site, grant, payload):
        exact(payload, "")
        members = self._members(site, grant)
        sessions_domain = [("company_id", "=", site.company_id.id), ("state", "=", "open"), ("end_datetime", ">", fields.Datetime.now())]
        if grant.role == "instructor":
            sessions_domain.append(("id", "in", grant.session_ids.ids))
        elif grant.role in ("guardian", "member"):
            sessions_domain.append(("id", "in", self.env["dojo.class.enrollment"].sudo().search([("member_id", "in", members.ids), ("status", "=", "registered")]).session_id.ids))
        sessions = self.env["dojo.class.session"].sudo().search(sessions_domain, order="start_datetime", limit=100)
        domain = [("site_id", "=", site.id)]
        if grant.role == "instructor":
            domain += [("member_id", "in", members.ids), ("session_id", "in", grant.session_ids.ids)]
        elif grant.role == "guardian":
            domain += [("member_id", "in", members.ids), ("binding_id.partner_id", "=", self.env.user.partner_id.id)]
        elif grant.role == "member":
            domain.append(("id", "=", 0))
        messages = self.env["dojo.hub.message"].sudo().search(domain, limit=50)
        deliveries = self.env["dojo.hub.delivery"].sudo().search([("site_id", "=", site.id), ("message_id", "in", messages.ids)], limit=100)
        audit = self.env["dojo.hub.receipt"].sudo().search([("site_id", "=", site.id), ("member_id", "in", members.ids)], limit=50)
        if grant.role in ("guardian", "member"):
            audit = audit.filtered(lambda a: a.operation in ("checkout", "book", "change_class", "rank"))
        return {"principal": {"userId": str(self.env.uid), "name": self.env.user.name, "role": grant.role, "siteId": str(site.id)},
            "members": [{"id": str(m.id), "name": m.name, "version": session_version(m), "state": m.membership_state,
                "rank": m.current_rank_id.name or "Unassigned"} for m in members],
            "sessions": [{"id": str(s.id), "title": s.template_id.name, "startsAt": iso(s.start_datetime), "future": s.start_datetime > fields.Datetime.now(), "version": session_version(s)} for s in sessions],
            "messages": [{"id": str(m.id), "text": m.source_text, "state": m.state, "summary": m.summary or "", "reply": m.reply_draft or "",
                "mode": m.draft_mode or "Not drafted", "revision": m.revision, "channel": m.channel,
                "guardianName": m.binding_id.partner_id.name if m.binding_id else "", "contactRef": m.contact_ref,
                "memberId": str(m.member_id.id) if m.member_id else None, "sessionId": str(m.session_id.id) if m.session_id else None} for m in messages],
            "deliveries": [{"id": str(d.id), "messageId": str(d.message_id.id), "state": d.state, "providerRef": d.provider_ref or None,
                "approvedBy": d.approved_by.name, "approvedAt": iso(d.approved_at)} for d in deliveries],
            "timeline": [{"id": str(a.id), "memberId": str(a.member_id.id), "action": a.operation, "actor": a.user_id.name, "at": iso(a.create_date)} for a in audit],
            "capabilities": {"identity": "odoo_user_session", "ai": "enabled_not_exercised" if site.ai_enabled else "disabled",
                "outbound": "configured_not_exercised" if site.outbound_enabled and site.provider_origin and site._secret("provider_token_env") else "not_configured",
                "inbound": "configured_not_exercised" if site._secret("webhook_secret_env") else "not_configured",
                "ebGym": "bridge_installed_not_verified" if "dojo.gym.link" in self.env else "bridge_not_installed", "productionReady": False}}

    def _resolve(self, site, grant, p):
        exact(p, "messageId memberId sessionId expectedRevision")
        message = self._message(site, grant, p["messageId"], unresolved=True)
        member = self._member(site, grant, p["memberId"])
        session = self._session(site, grant, p["sessionId"])
        self._lock(message)
        if type(p["expectedRevision"]) is not int or message.revision != p["expectedRevision"]:
            raise HubProblem("VERSION_CONFLICT")
        if self.env["dojo.hub.delivery"].sudo().search_count([("message_id", "=", message.id)]):
            raise HubProblem("ALREADY_APPROVED")
        binding = self._binding(message)
        if member not in binding.member_ids:
            raise HubProblem("GUARDIAN_REVIEW_REQUIRED")
        if not self.env["dojo.class.enrollment"].sudo().search_count([("member_id", "=", member.id), ("session_id", "=", session.id), ("status", "=", "registered")]):
            raise HubProblem("NOT_ON_ROSTER")
        message.write({"binding_id": binding.id, "member_id": member.id, "session_id": session.id, "state": "resolved",
            "revision": message.revision + 1, "summary": False, "reply_draft": False, "draft_mode": False})
        return {"messageId": str(message.id), "memberId": str(member.id), "revision": message.revision}

    def _draft(self, site, grant, p):
        exact(p, "messageId expectedRevision")
        message = self._message(site, grant, p["messageId"])
        self._lock(message)
        self._binding(message)
        if type(p["expectedRevision"]) is not int or message.revision != p["expectedRevision"]:
            raise HubProblem("VERSION_CONFLICT")
        if self.env["dojo.hub.delivery"].sudo().search_count([("message_id", "=", message.id)]):
            raise HubProblem("ALREADY_APPROVED")
        if site.ai_enabled:
            summary, reply, mode = self.env["dojo.kiosk.service"]._followup_draft(site.kiosk_config_id, message.source_text, source="verified_guardian")
        else:
            summary, reply, mode = ("A verified guardian requested attendance follow-up. Review the original message and the student's class before deciding next steps.",
                "Thank you for letting us know. We will review suitable makeup options and confirm availability and eligibility with you.", "Template draft - AI disabled")
        message.write({"summary": summary, "reply_draft": reply, "draft_mode": mode, "revision": message.revision + 1})
        return {"messageId": str(message.id), "memberId": str(message.member_id.id), "revision": message.revision, "summary": summary, "reply": reply, "mode": mode}

    def _approve_reply(self, site, grant, p):
        exact(p, "messageId expectedRevision reply")
        message = self._message(site, grant, p["messageId"])
        self._lock(message)
        binding = self._binding(message)
        if type(p["expectedRevision"]) is not int or message.revision != p["expectedRevision"] or not message.reply_draft:
            raise HubProblem("VERSION_CONFLICT")
        if not site.outbound_enabled or not site.provider_origin or not site._secret("provider_token_env"):
            raise HubProblem("PROVIDER_NOT_CONFIGURED", 503)
        body = text(p["reply"], 1500)
        existing = self.env["dojo.hub.delivery"].sudo().search([("message_id", "=", message.id)], limit=1)
        if existing:
            if existing.body != body or existing.approved_revision != message.revision:
                raise HubProblem("ALREADY_APPROVED")
            return {"deliveryId": str(existing.id), "memberId": str(message.member_id.id), "state": existing.state}
        delivery = self.env["dojo.hub.delivery"].sudo().create({"site_id": site.id, "message_id": message.id, "binding_id": binding.id,
            "approved_by": self.env.uid, "approved_revision": message.revision, "binding_version": str(session_version(binding)),
            "workspace_ref": site.workspace_ref, "provider_origin": site.provider_origin,
            "channel": binding.channel, "contact_ref": binding.contact_ref, "body": body})
        return {"deliveryId": str(delivery.id), "memberId": str(message.member_id.id), "state": "queued"}

    def _checkout(self, site, grant, p):
        exact(p, "memberId sessionId")
        member, session = self._member(site, grant, p["memberId"]), self._session(site, grant, p["sessionId"])
        attendance = self.env["dojo.attendance.log"].sudo().search([("member_id", "=", member.id), ("session_id", "=", session.id), ("company_id", "=", site.company_id.id), ("status", "in", ["present", "late"])], limit=1)
        if not attendance:
            raise HubProblem("NOT_CHECKED_IN")
        self._lock(attendance)
        if not attendance.checkout_datetime:
            if fields.Datetime.now() <= attendance.checkin_datetime:
                raise HubProblem("RETRY_AFTER_CHECKIN")
            attendance.checkout_datetime = fields.Datetime.now()
        return {"memberId": str(member.id), "attendanceId": str(attendance.id), "checkedOutAt": iso(attendance.checkout_datetime)}

    def _enroll(self, site, grant, member, session):
        self._lock(session)
        self._lock(member)
        if session.state != "open" or session.start_datetime <= fields.Datetime.now():
            raise HubProblem("SESSION_NOT_OPEN")
        enrollment = self.env["dojo.class.enrollment"].sudo().search([("session_id", "=", session.id), ("member_id", "=", member.id)], limit=1)
        if enrollment:
            if enrollment.status != "registered" or enrollment.attendance_state != "pending":
                raise HubProblem("ENROLLMENT_REVIEW_REQUIRED")
        else:
            enrollment = self.env["dojo.class.enrollment"].sudo().create({"session_id": session.id, "member_id": member.id})
        # Existing dojo subscription, roster and credit rules are still authoritative.
        self.env["dojo.kiosk.service"]._v2_eligible(site.kiosk_config_id, session, member, enrollment)
        self._gym_eligibility(member, session)
        return enrollment

    def _gym_eligibility(self, member, session):
        """Optional EB Gym bridge extends this without replacing dojo eligibility."""

    def _book(self, site, grant, p):
        exact(p, "memberId sessionId expectedVersion")
        member, session = self._member(site, grant, p["memberId"]), self._session(site, grant, p["sessionId"])
        self._lock(session)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(session):
            raise HubProblem("VERSION_CONFLICT")
        row = self._enroll(site, grant, member, session)
        return {"memberId": str(member.id), "sessionId": str(session.id), "enrollmentId": str(row.id), "state": "registered"}

    def _change_class(self, site, grant, p):
        exact(p, "memberId sessionId fromSessionId expectedVersion")
        if p["sessionId"] == p["fromSessionId"]:
            raise HubProblem("INVALID_COMMAND", 400)
        member = self._member(site, grant, p["memberId"])
        previous = self._session(site, grant, p["fromSessionId"])
        self._lock(previous)
        old = self.env["dojo.class.enrollment"].sudo().search([("session_id", "=", previous.id), ("member_id", "=", member.id), ("status", "=", "registered")], limit=1)
        if not old or previous.start_datetime <= fields.Datetime.now() or old.attendance_state != "pending":
            raise HubProblem("ENROLLMENT_REVIEW_REQUIRED")
        # Cancellation, credit release, new reservation and receipt share one transaction.
        old.write({"status": "cancelled"})
        return self._book(site, grant, {k: v for k, v in p.items() if k != "fromSessionId"})

    def _update_member(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "memberId expectedVersion changes")
        member = self._member(site, grant, p["memberId"])
        self._lock(member)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(member):
            raise HubProblem("VERSION_CONFLICT")
        changes = p["changes"]
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "phone", "email"}:
            raise HubProblem("INVALID_COMMAND", 400)
        member.write({k: text(v, 200) for k, v in changes.items()})
        return {"memberId": str(member.id), "version": session_version(member), "state": "updated"}

    def _rank(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "memberId rankId expectedVersion stripes reason")
        member = self._member(site, grant, p["memberId"])
        self._lock(member)
        if type(p["expectedVersion"]) is not int or p["expectedVersion"] != session_version(member):
            raise HubProblem("VERSION_CONFLICT")
        rank = self.env["dojo.belt.rank"].sudo().search([("id", "=", record_id(p["rankId"])), ("company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not rank or type(p["stripes"]) is not int or not 0 <= p["stripes"] <= rank.max_stripes:
            raise HubProblem("INVALID_RANK", 422)
        row = self.env["dojo.member.rank"].sudo().create({"member_id": member.id, "rank_id": rank.id, "stripe_count": p["stripes"], "notes": text(p["reason"], 500)})
        member.invalidate_recordset()
        return {"memberId": str(member.id), "rankHistoryId": str(row.id), "state": "awarded"}

    def _link_guardian(self, site, grant, p):
        self._role(grant, "owner manager")
        exact(p, "partnerId memberIds channel contactRef verificationRef allowReply")
        if not isinstance(p["memberIds"], list) or not 1 <= len(p["memberIds"]) <= 20 or p["channel"] not in ("sms", "whatsapp") or type(p["allowReply"]) is not bool:
            raise HubProblem("INVALID_COMMAND", 400)
        partner = self.env["res.partner"].sudo().search([("id", "=", record_id(p["partnerId"])), ("active", "=", True), "|", ("company_id", "=", site.company_id.id), ("company_id", "=", False)], limit=1)
        if not partner:
            raise HubProblem("PARTNER_UNAVAILABLE", 404)
        member_ids = [self._member(site, grant, v).id for v in p["memberIds"]]
        row = self.env["dojo.hub.guardian"].sudo().create({"site_id": site.id, "partner_id": partner.id, "member_ids": [(6, 0, member_ids)],
            "channel": p["channel"], "contact_ref": text(p["contactRef"], 128), "verification_ref": text(p["verificationRef"], 300),
            "allow_reply": p["allowReply"], "verified_by": self.env.uid})
        return {"bindingId": str(row.id), "state": "verified_by_staff"}

    def _provision(self, site, grant, p):
        self._role(grant, "owner manager")
        if not isinstance(p, dict):
            raise HubProblem("INVALID_COMMAND", 400)
        exact(p, "name login role memberIds sessionIds verificationRef" + (" partnerId" if "partnerId" in p else ""))
        allowed = ("instructor", "guardian", "member") + (("manager",) if grant.role == "owner" else ())
        if p["role"] not in allowed or not isinstance(p["memberIds"], list) or len(p["memberIds"]) > 20 or not isinstance(p["sessionIds"], list) or len(p["sessionIds"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        text(p["verificationRef"], 300)
        login, name = text(p["login"], 200), text(p["name"], 200)
        # Never hijack/link an existing account simply because the email matches.
        if self.env["res.users"].sudo().with_context(active_test=False).search_count([("login", "=", login)]):
            raise HubProblem("EXISTING_USER_REQUIRES_ADMIN_LINK")
        members = [self._member(site, grant, v).id for v in p["memberIds"]]
        sessions = [self._session(site, grant, v).id for v in p["sessionIds"]]
        if p["role"] == "member" and members:
            raise HubProblem("EXISTING_MEMBER_REQUIRES_ADMIN_LINK")
        partner = self.env["res.partner"]
        if p["role"] == "guardian":
            if not members or not p.get("partnerId"):
                raise HubProblem("VERIFIED_GUARDIAN_REQUIRED")
            partner = self.env["res.partner"].sudo().search([("id", "=", record_id(p["partnerId"])), ("active", "=", True)], limit=1)
            bindings = self.env["dojo.hub.guardian"].sudo().search([("site_id", "=", site.id), ("partner_id", "=", partner.id),
                ("active", "=", True), ("verification_ref", "=", p["verificationRef"])])
            if not partner or partner.user_ids or not set(members) <= set(bindings.member_ids.ids):
                raise HubProblem("VERIFIED_GUARDIAN_REQUIRED")
        elif p.get("partnerId"):
            raise HubProblem("INVALID_COMMAND", 400)
        user = self.env["res.users"].sudo().with_context(no_reset_password=True, mail_create_nosubscribe=True, tracking_disable=True).create({
            "name": partner.name if partner else name, **({"partner_id": partner.id} if partner else {}),
            "login": login, "password": secrets.token_urlsafe(48), "company_id": site.company_id.id,
            "company_ids": [(6, 0, [site.company_id.id])], "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        member = self.env["dojo.member"]
        if p["role"] == "member":
            member = self.env["dojo.member"].sudo().create({"partner_id": user.partner_id.id, "company_id": site.company_id.id, "membership_state": "lead"})
            members = member.ids
        access = self.env["dojo.hub.grant"].sudo().create({"site_id": site.id, "user_id": user.id, "role": p["role"], "member_ids": [(6, 0, members)], "session_ids": [(6, 0, sessions)]})
        return {"userId": str(user.id), "grantId": str(access.id), "memberId": str(member.id) if member else None,
            "state": "password_setup_required", "providerProvisioning": "not_configured", "verificationRef": p["verificationRef"]}

    def _dispatch(self, site_id, operation, payload, request_key=None):
        try:
            with self.env.cr.savepoint():
                site, grant = self._principal(site_id)
                if operation == "context":
                    return self._context(site, grant, payload)
                self._role(grant)
                handlers = {"resolve": self._resolve, "draft": self._draft, "approve_reply": self._approve_reply,
                    "checkout": self._checkout, "book": self._book, "change_class": self._change_class,
                    "update_member": self._update_member, "rank": self._rank, "link_guardian": self._link_guardian, "provision": self._provision}
                if not isinstance(operation, str) or operation not in handlers or not isinstance(request_key, str) or not KEY.fullmatch(request_key):
                    raise HubProblem("INVALID_COMMAND", 400)
                self.env["dojo.kiosk.service"]._v2_lock("hub:%s:%s:%s" % (site.id, self.env.uid, request_key))
                digest = fingerprint({"operation": operation, "payload": payload})
                receipt = self.env["dojo.hub.receipt"].sudo().search([("site_id", "=", site.id), ("user_id", "=", self.env.uid), ("request_key", "=", request_key)], limit=1)
                if receipt:
                    if receipt.fingerprint != digest:
                        raise HubProblem("IDEMPOTENCY_CONFLICT")
                    if receipt.member_id:
                        self._member(site, grant, str(receipt.member_id.id))
                    if isinstance(payload, dict) and payload.get("sessionId"):
                        self._session(site, grant, payload["sessionId"])
                    if isinstance(payload, dict) and payload.get("messageId"):
                        self._message(site, grant, payload["messageId"], unresolved=True)
                    if operation in ("provision", "link_guardian", "update_member", "rank"):
                        self._role(grant, "owner manager")
                    return {**json.loads(receipt.response_json), "replayed": True}
                result = handlers[operation](site, grant, payload)
                self.env["dojo.hub.receipt"].sudo().create({"site_id": site.id, "user_id": self.env.uid, "request_key": request_key,
                    "operation": operation, "fingerprint": digest, "member_id": int(result["memberId"]) if result.get("memberId") else False, "response_json": json.dumps(result)})
                return {**result, "replayed": False}
        except HubProblem as exc:
            return {"error": {"code": exc.code, "status": exc.status}}
        except KioskProblem as exc:
            return {"error": {"code": exc.code, "status": 422}}
        except AccessError:
            return {"error": {"code": "FORBIDDEN", "status": 403}}
        except (ValidationError, UserError):
            return {"error": {"code": "BUSINESS_RULE_REVIEW_REQUIRED", "status": 422}}
        except (pg_errors.UniqueViolation, pg_errors.SerializationFailure, pg_errors.LockNotAvailable, pg_errors.DeadlockDetected):
            return {"error": {"code": "CONCURRENT_RETRY", "status": 409}}
