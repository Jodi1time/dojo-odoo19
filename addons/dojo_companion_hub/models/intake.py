"""Reviewed profiles on native records. Never activate a plan or guess authority."""
import json
import re

from psycopg2 import errors as pg_errors
from odoo import fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import KEY, KioskProblem, record_id, session_version
from .intake_validation import IntakeValidationError, normalized_name, validate_intake
from .service import HubProblem, exact, fingerprint

QUIET = dict(companion_internal_task=True, tracking_disable=True,
             mail_create_nosubscribe=True, mail_create_nolog=True, no_reset_password=True)


class HubIntake(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _intake_guardians(self, site, query):
        domain = [("company_id", "=", site.company_id.id), ("active", "=", True),
                  ("is_guardian", "=", True), ("is_minor", "=", False),
                  ("parent_id.is_household", "=", True), ("parent_id.active", "=", True),
                  ("parent_id.company_id", "=", site.company_id.id)]
        if query:
            domain += ["|", ("name", "ilike", query), ("parent_id.name", "ilike", query)]
        return self.env["res.partner"].sudo().search(domain, order="name, id", limit=51)

    def _intake_context(self, site, grant, p):
        exact(p, "query")
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        rows = self._intake_guardians(site, p["query"].strip())
        receipts = self.env["dojo.hub.receipt"].sudo().search([
            ("site_id", "=", site.id), ("user_id", "=", self.env.uid),
            ("operation", "=", "create_student"), ("member_id.company_id", "=", site.company_id.id),
            ("member_id.active", "=", True)], limit=10)
        return {"guardians": [{"id": str(r.id), "name": r.name,
            "householdId": str(r.parent_id.id), "household": r.parent_id.name,
            "version": session_version(r), "householdVersion": session_version(r.parent_id)} for r in rows[:50]],
            "moreGuardians": len(rows) > 50, "school": site.company_id.name,
            "today": str(fields.Date.context_today(self.with_context(tz=site.company_id.partner_id.tz or "UTC"))),
            "recent": [{"memberId": str(r.member_id.id), "name": r.member_id.name,
                        "memberNumber": r.member_id.member_number} for r in receipts]}

    def _intake_guardian(self, site, value, lock=False):
        guardian = self.env["res.partner"].sudo().search([
            ("id", "=", record_id(value["partnerId"])), ("company_id", "=", site.company_id.id),
            ("active", "=", True), ("is_guardian", "=", True), ("is_minor", "=", False)], limit=1)
        household = guardian.parent_id
        if (not guardian or not household.active or not household.is_household
                or household.company_id != site.company_id or str(household.id) != value["householdId"]):
            raise HubProblem("GUARDIAN_UNAVAILABLE", 404)
        if lock:
            for row in (guardian | household).sorted("id"):
                self._lock(row)
            guardian.invalidate_recordset()
            household.invalidate_recordset()
            if (not guardian.active or not guardian.is_guardian or guardian.is_minor
                    or guardian.company_id != site.company_id or guardian.parent_id != household
                    or not household.active or not household.is_household or household.company_id != site.company_id):
                raise HubProblem("GUARDIAN_UNAVAILABLE", 404)
        if (session_version(guardian) != value["expectedVersion"]
                or session_version(household) != value["expectedHouseholdVersion"]):
            raise HubProblem("VERSION_CONFLICT")
        return guardian, household

    def _intake_check_duplicates(self, site, value):
        # Contact details are not identity. A name + birth-date collision requires
        # staff review; it never silently merges records or reveals another school.
        student = value["student"]
        members = self.env["dojo.member"].sudo().with_context(active_test=False).search([
            ("company_id", "=", site.company_id.id), ("date_of_birth", "=", student["dateOfBirth"])], limit=1001)
        if len(members) > 1000:
            raise HubProblem("INTAKE_SCOPE_REVIEW_REQUIRED")
        if any(normalized_name(m.name) == normalized_name(student["name"]) for m in members):
            raise HubProblem("DUPLICATE_STUDENT_REVIEW_REQUIRED")
        guardian = value["guardian"]
        if guardian and guardian["kind"] == "new":
            # Same-school candidates only. Matching contact never grants authority.
            domain = [("company_id", "=", site.company_id.id), ("is_household", "=", False)]
            candidates = self.env["res.partner"].sudo().with_context(active_test=False).search(domain, limit=10001)
            if len(candidates) > 10000:
                raise HubProblem("INTAKE_SCOPE_REVIEW_REQUIRED")
            phone = re.sub(r"\D", "", guardian["phone"])
            email = guardian["email"].casefold()
            if any((email and (r.email or "").strip().casefold() == email) or
                   (phone and re.sub(r"\D", "", r.phone or "") == phone) for r in candidates):
                raise HubProblem("GUARDIAN_DUPLICATE_REVIEW_REQUIRED")

    def _intake_review(self, site, payload, lock=False):
        today = fields.Date.context_today(self.with_context(tz=site.company_id.partner_id.tz or "UTC"))
        value = validate_intake(payload, today)
        guardian = household = self.env["res.partner"]
        if value["guardian"] and value["guardian"]["kind"] == "existing":
            guardian, household = self._intake_guardian(site, value["guardian"], lock=lock)
        if lock:
            # Serializes intake across staff identities and request keys. Existing
            # per-command locks still protect retries; no customer data in lock keys.
            self.env["dojo.kiosk.service"]._v2_lock("intake-company:%s" % site.company_id.id)
        self._intake_check_duplicates(site, value)
        token = fingerprint({"intake": value, "siteId": site.id, "userId": self.env.uid})
        return value, token, guardian, household

    def _intake_preview(self, site, grant, p):
        exact(p, "intake")
        try:
            value, token, guardian, household = self._intake_review(site, p["intake"])
        except IntakeValidationError as exc:
            return {"valid": False, "issues": exc.issues}
        return {"valid": True, "issues": [], "intake": value, "reviewToken": token,
            "guardianName": guardian.name if guardian else value["guardian"]["name"] if value["guardian"] else None,
            "householdName": household.name if household else None,
            "state": "profile_only", "subscriptionCreated": False, "enrollmentCreated": False,
            "portalAccessGranted": False, "messageSent": False}

    def _create_student(self, site, grant, p):
        exact(p, "intake reviewToken")
        value, token, guardian, household = self._intake_review(site, p["intake"], lock=True)
        if not isinstance(p["reviewToken"], str) or p["reviewToken"] != token:
            raise HubProblem("INTAKE_REVIEW_CHANGED")
        Partner = self.env["res.partner"].sudo().with_company(site.company_id).with_context(**QUIET)
        child = value["studentType"] == "child"
        if child and value["guardian"]["kind"] == "new":
            g = value["guardian"]
            guardian = Partner.create({"name": g["name"], "email": g["email"] or False,
                "phone": g["phone"] or False, "company_id": site.company_id.id, "is_guardian": True})
            household = Partner.create({"name": "Family of " + g["name"], "is_household": True,
                "is_company": True, "company_id": site.company_id.id, "primary_guardian_id": guardian.id})
            guardian.write({"parent_id": household.id})
        student = value["student"]
        partner = Partner.create({"name": student["name"], "email": student["email"] or False,
            "phone": student["phone"] or False, "company_id": site.company_id.id,
            "is_student": True, "is_minor": child, "parent_id": household.id if household else False})
        # Explicit partner prevents delegated create from dropping member company_id.
        member = self.env["dojo.member"].sudo().with_company(site.company_id).with_context(**QUIET).create({
            "partner_id": partner.id, "company_id": site.company_id.id, "membership_state": "lead",
            "date_of_birth": student["dateOfBirth"], "gender": student["gender"] or False})
        return {"state": "student_created", "memberId": str(member.id), "memberNumber": member.member_number,
            "guardianId": str(guardian.id) if guardian else None, "householdId": str(household.id) if household else None,
            "reviewRef": value["reviewRef"], "subscriptionCreated": False, "enrollmentCreated": False,
            "portalAccessGranted": False, "messageSent": False}

    def _dispatch(self, site_id, operation, payload, request_key=None):
        if not isinstance(operation, str) or operation not in ("intake_context", "intake_preview", "create_student"):
            return super()._dispatch(site_id, operation, payload, request_key)
        try:
            with self.env.cr.savepoint():
                site, grant = self._principal(site_id)
                self._role(grant, "owner manager")
                if operation == "intake_context":
                    return self._intake_context(site, grant, payload)
                if operation == "intake_preview":
                    return self._intake_preview(site, grant, payload)
                exact(payload, "intake reviewToken")
                if not isinstance(request_key, str) or not KEY.fullmatch(request_key):
                    raise HubProblem("INVALID_COMMAND", 400)
                self.env["dojo.kiosk.service"]._v2_lock("hub:%s:%s:%s" % (site.id, self.env.uid, request_key))
                digest = fingerprint({"operation": operation, "payload": payload})
                receipt = self.env["dojo.hub.receipt"].sudo().search([
                    ("site_id", "=", site.id), ("user_id", "=", self.env.uid), ("request_key", "=", request_key)], limit=1)
                if receipt:
                    if receipt.fingerprint != digest:
                        raise HubProblem("IDEMPOTENCY_CONFLICT")
                    # Re-authorize even if the new profile was later archived.
                    if not receipt.member_id or receipt.member_id.company_id != site.company_id:
                        raise HubProblem("MEMBER_UNAVAILABLE", 404)
                    return {**json.loads(receipt.response_json), "replayed": True}
                result = self._create_student(site, grant, payload)
                self.env["dojo.hub.receipt"].sudo().create({"site_id": site.id, "user_id": self.env.uid,
                    "request_key": request_key, "operation": operation, "fingerprint": digest,
                    "member_id": int(result["memberId"]), "response_json": json.dumps(result)})
                return {**result, "replayed": False}
        except IntakeValidationError:
            return {"error": {"code": "INTAKE_FIELDS_REQUIRED", "status": 422}}
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
