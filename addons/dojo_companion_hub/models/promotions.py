from datetime import date

from odoo import api, fields, models
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso, record_id
from .service import HubProblem, exact, fingerprint, text

ROSTER_LIMIT = 100
RESULTS = ("pending", "pass", "fail", "withdrew")


class MemberRankReversal(models.Model):
    _inherit = "dojo.member.rank"

    active = fields.Boolean(default=True)
    reversed_at = fields.Datetime(readonly=True, copy=False)
    reversed_by = fields.Many2one("res.users", readonly=True, copy=False, ondelete="restrict")
    reversal_reason = fields.Text(readonly=True, copy=False)


class MemberRankCurrent(models.Model):
    _inherit = "dojo.member"

    @api.depends("rank_history_ids.date_awarded", "rank_history_ids.rank_id", "rank_history_ids.active")
    def _compute_current_rank(self):
        return super()._compute_current_rank()

    @api.depends("rank_history_ids.date_awarded", "rank_history_ids.stripe_count", "rank_history_ids.active")
    def _compute_current_stripe_count(self):
        return super()._compute_current_stripe_count()

    @api.depends("attendance_log_ids.status", "attendance_log_ids.checkin_datetime", "rank_history_ids.date_awarded", "rank_history_ids.active")
    def _compute_attendance_since_last_rank(self):
        return super()._compute_attendance_since_last_rank()


class HubPromotions(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations.update({
            "testing_eligibility": {"handler": self._testing_eligibility, "roles": "owner manager", "write": False},
            "testing_roster": {"handler": self._testing_roster, "roles": "owner manager", "write": False},
            "promotion_preview": {"handler": self._promotion_preview, "roles": "owner manager", "write": False},
            "promotion_history": {"handler": self._promotion_history, "roles": "owner manager", "write": False},
            "save_testing_roster": {"handler": self._save_testing_roster, "roles": "owner manager", "write": True, "replay": self._replay_test},
            "record_test_results": {"handler": self._record_test_results, "roles": "owner manager", "write": True, "replay": self._replay_test},
            "promote_test_results": {"handler": self._promote_test_results, "roles": "owner manager", "write": True, "replay": self._replay_test},
            "reverse_promotion": {"handler": self._reverse_promotion, "roles": "owner manager", "write": True, "replay": self._replay_company_member},
        })
        return operations

    def _replay_test(self, site, grant, receipt, saved):
        self._belt_test(site, saved["testId"])

    def _ranks(self, site):
        return self.env["dojo.belt.rank"].sudo().search([("company_id", "in", [False, site.company_id.id]), ("active", "=", True)],
            order="sequence, id", limit=200)

    def _rank_in_scope(self, site, value):
        rank = self._ranks(site).filtered(lambda r: r.id == record_id(value))
        if not rank:
            raise HubProblem("INVALID_RANK", 422)
        return rank

    def _tests(self):
        return self.env["dojo.belt.test"].sudo().with_context(tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True)

    def _audit(self, test, reason):
        test.write({"notes": "\n".join(filter(None, [test.notes, "%s %s: %s" % (iso(fields.Datetime.now()), self.env.user.name, reason)]))})

    def _invite_flags(self, members):
        Registration = self.env["dojo.belt.test.registration"].sudo()
        for member in members:
            pending = bool(Registration.search_count([("member_id", "=", member.id), ("result", "=", "pending"),
                ("test_id.state", "in", ["scheduled", "in_progress"])]))
            if member.test_invite_pending != pending:
                member.sudo().write({"test_invite_pending": pending})

    def _belt_test(self, site, value):
        test = self._tests().search([("id", "=", record_id(value)), ("company_id", "=", site.company_id.id)], limit=1)
        if not test:
            raise HubProblem("TEST_UNAVAILABLE", 404)
        return test

    def _promotion_of(self, registration):
        return self.env["dojo.member.rank"].sudo().search([("test_registration_id", "=", registration.id), ("active", "=", True)], limit=1)

    def _eligibility_row(self, site, member, open_registrations):
        next_rank = member.sudo()._get_next_belt_rank()
        threshold = next_rank.attendance_threshold if next_rank else 0
        attended = member.attendance_since_last_rank
        status = "top_rank" if not next_rank else "no_threshold" if not threshold else "eligible" if attended >= threshold else "not_yet"
        registration = open_registrations.get(member.id)
        return {"memberId": str(member.id), "name": member.name, "currentRank": member.current_rank_id.name or None,
            "nextRank": {"id": str(next_rank.id), "name": next_rank.name} if next_rank else None, "attended": attended,
            "threshold": threshold or None, "status": status,
            "openTest": {"testId": str(registration.test_id.id), "name": registration.test_id.name} if registration else None}

    def _testing_eligibility(self, site, grant, p):
        exact(p, "query")
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        domain = [("company_id", "=", site.company_id.id), ("active", "=", True)]
        if p["query"].strip():
            domain.append(("name", "ilike", p["query"].strip()))
        members = self.env["dojo.member"].sudo().search(domain, order="name, id", limit=500)
        registrations = self.env["dojo.belt.test.registration"].sudo().search([("member_id", "in", members.ids),
            ("result", "=", "pending"), ("test_id.state", "in", ["scheduled", "in_progress"]), ("test_id.company_id", "=", site.company_id.id)])
        open_registrations = {r.member_id.id: r for r in registrations}
        rows = [self._eligibility_row(site, m, open_registrations) for m in members]
        order = {"eligible": 0, "not_yet": 1, "no_threshold": 2, "top_rank": 3}
        rows.sort(key=lambda r: (order[r["status"]], r["name"].casefold()))
        tests = self.env["dojo.belt.test"].sudo().search([("company_id", "=", site.company_id.id), ("state", "!=", "cancelled")],
            order="test_date desc, id desc", limit=50)
        return {"rows": rows, "limit": 500, "ranks": [{"id": str(r.id), "name": r.name, "sequence": r.sequence} for r in self._ranks(site)],
            "tests": [{"id": str(t.id), "name": t.name, "date": str(t.test_date), "state": t.state, "count": len(t.registration_ids)} for t in tests]}

    def _testing_roster(self, site, grant, p):
        exact(p, "testId")
        test = self._belt_test(site, p["testId"])
        rows = []
        for registration in test.registration_ids.sorted(lambda r: ((r.member_id.name or "").casefold(), r.id)):
            promotion = self._promotion_of(registration)
            rows.append({"registrationId": str(registration.id), "memberId": str(registration.member_id.id), "name": registration.member_id.name,
                "currentRank": registration.member_id.current_rank_id.name or None, "targetRank": registration.target_rank_id.name,
                "result": registration.result, "promotionId": str(promotion.id) if promotion else None})
        return {"test": {"id": str(test.id), "name": test.name, "date": str(test.test_date), "state": test.state, "version": self._version(test),
                "program": test.program_id.name or None}, "rows": rows, "school": site.company_id.name}

    def _save_testing_roster(self, site, grant, p):
        exact(p, "testId expectedVersion name testDate add removeRegistrationIds reason")
        reason = text(p["reason"], 500)
        name = text(p["name"], 200)
        try:
            test_date = date.fromisoformat(p["testDate"]) if isinstance(p["testDate"], str) and len(p["testDate"]) == 10 else None
        except ValueError:
            test_date = None
        if not test_date:
            raise HubProblem("INVALID_COMMAND", 400)
        if not isinstance(p["add"], list) or len(p["add"]) > ROSTER_LIMIT:
            raise HubProblem("INVALID_COMMAND", 400)
        removing = self._id_list(p["removeRegistrationIds"], ROSTER_LIMIT)
        Test = self._tests()
        if p["testId"] is None:
            if p["expectedVersion"] != 0 or removing:
                raise HubProblem("INVALID_COMMAND", 400)
            test = Test.create({"name": name, "test_date": test_date, "state": "scheduled", "company_id": site.company_id.id})
        else:
            test = self._belt_test(site, p["testId"])
            self._lock(test)
            self._require_version(test, p["expectedVersion"])
            if test.state != "scheduled":
                raise HubProblem("TEST_NOT_EDITABLE")
            test.write({"name": name, "test_date": test_date})
        Registration = self.env["dojo.belt.test.registration"].sudo()
        added = Registration
        seen = set()
        for row in p["add"]:
            if not isinstance(row, dict) or set(row) != {"memberId", "targetRankId"}:
                raise HubProblem("INVALID_COMMAND", 400)
            member = self._member(site, grant, row["memberId"])
            if member.id in seen or member in test.registration_ids.member_id:
                raise HubProblem("ROSTER_DUPLICATE")
            seen.add(member.id)
            target = self._rank_in_scope(site, row["targetRankId"])
            if member.current_rank_id and target.sequence <= member.current_rank_id.sequence:
                raise HubProblem("TARGET_RANK_NOT_HIGHER", 422)
            added |= Registration.create({"test_id": test.id, "member_id": member.id, "target_rank_id": target.id, "result": "pending"})
        removed = test.registration_ids.filtered(lambda r: r.id in removing)
        if len(removed) != len(removing) or removed.filtered(lambda r: r.result != "pending"):
            raise HubProblem("ROSTER_CHANGED")
        removed_members = removed.member_id
        removed.unlink()
        self._invite_flags(added.member_id | removed_members)
        self._audit(test, reason)
        return {"testId": str(test.id), "state": "testing_roster_saved", "reason": reason, "added": [str(r.id) for r in added],
            "removedMembers": [str(i) for i in removed_members.ids], "count": len(test.registration_ids), "version": self._version(test),
            "_scopeMemberIds": added.member_id.ids + removed_members.ids}

    def _record_test_results(self, site, grant, p):
        exact(p, "testId expectedVersion results reason")
        reason = text(p["reason"], 500)
        test = self._belt_test(site, p["testId"])
        self._lock(test)
        self._require_version(test, p["expectedVersion"])
        if test.state not in ("scheduled", "in_progress"):
            raise HubProblem("TEST_NOT_EDITABLE")
        if not isinstance(p["results"], list) or not 1 <= len(p["results"]) <= ROSTER_LIMIT:
            raise HubProblem("INVALID_COMMAND", 400)
        changed = []
        for row in p["results"]:
            if not isinstance(row, dict) or set(row) != {"registrationId", "result"} or row["result"] not in RESULTS:
                raise HubProblem("INVALID_COMMAND", 400)
            registration = test.registration_ids.filtered(lambda r: r.id == record_id(row["registrationId"]))
            if not registration:
                raise HubProblem("ROSTER_CHANGED")
            if self._promotion_of(registration):
                raise HubProblem("PROMOTED_RESULT_LOCKED")
            if registration.result != row["result"]:
                changed.append({"registrationId": str(registration.id), "before": registration.result, "after": row["result"]})
                registration.write({"result": row["result"]})
        if not changed:
            raise HubProblem("RESULTS_UNCHANGED")
        test.write({"state": "in_progress"})
        self._audit(test, reason)
        return {"testId": str(test.id), "state": "test_results_saved", "reason": reason, "changes": changed, "version": self._version(test),
            "_scopeMemberIds": test.registration_ids.filtered(lambda r: str(r.id) in {c["registrationId"] for c in changed}).member_id.ids}

    def _promotion_plan(self, site, grant, p):
        test = self._belt_test(site, p["testId"])
        ids = self._id_list(p["registrationIds"], ROSTER_LIMIT)
        if not ids:
            raise HubProblem("INVALID_COMMAND", 400)
        registrations = test.registration_ids.filtered(lambda r: r.id in ids)
        if len(registrations) != len(ids):
            raise HubProblem("ROSTER_CHANGED")
        plan, conflicts = [], []
        for registration in registrations.sorted("id"):
            member, target = registration.member_id, registration.target_rank_id
            problem = None
            if registration.result != "pass":
                problem = "Result is not pass"
            elif self._promotion_of(registration):
                problem = "Already promoted from this test"
            elif not member.active or member.company_id != site.company_id:
                problem = "Student is archived or outside this school"
            elif not target.active or target.company_id not in (self.env["res.company"], site.company_id):
                problem = "Target rank is not active in this school"
            elif member.current_rank_id and target.sequence <= member.current_rank_id.sequence:
                problem = "Student already holds this rank or higher"
            row = {"registrationId": str(registration.id), "memberId": str(member.id), "name": member.name,
                "from": member.current_rank_id.name or None, "to": target.name}
            (conflicts if problem else plan).append({**row, "problem": problem} if problem else row)
        summary = {"testId": str(test.id), "test": test.name, "date": str(test.test_date), "promotions": plan, "conflicts": conflicts}
        token = fingerprint({"summary": summary, "test": self._version(test), "siteId": site.id, "userId": self.env.uid})
        return test, registrations, summary, token

    def _promotion_preview(self, site, grant, p):
        exact(p, "testId registrationIds")
        test, _, summary, token = self._promotion_plan(site, grant, p)
        return {**summary, "version": self._version(test), "valid": bool(summary["promotions"]) and not summary["conflicts"],
            "reviewToken": token if summary["promotions"] and not summary["conflicts"] else None}

    def _promote_test_results(self, site, grant, p):
        exact(p, "testId expectedVersion registrationIds reason reviewToken")
        reason = text(p["reason"], 500)
        test = self._belt_test(site, p["testId"])
        self._lock(test)
        self._require_version(test, p["expectedVersion"])
        test, registrations, summary, token = self._promotion_plan(site, grant, p)
        if summary["conflicts"] or not summary["promotions"]:
            raise HubProblem("PROMOTION_REVIEW_REQUIRED")
        if not isinstance(p["reviewToken"], str) or p["reviewToken"] != token:
            raise HubProblem("PROMOTION_REVIEW_CHANGED")
        Rank = self.env["dojo.member.rank"].sudo()
        created = Rank
        for registration in registrations.sorted("id"):
            self._lock(registration.member_id)
            created |= Rank.create({"member_id": registration.member_id.id, "rank_id": registration.target_rank_id.id,
                "program_id": test.program_id.id or False, "date_awarded": test.test_date, "stripe_count": 0,
                "test_registration_id": registration.id, "notes": reason})
            self._touch(registration.member_id)
        if all(r.result != "pending" for r in test.registration_ids):
            test.write({"state": "completed"})
        self._audit(test, reason)
        return {"testId": str(test.id), "state": "promotions_awarded", "reason": reason,
            "promotions": [{"rankHistoryId": str(r.id), "memberId": str(r.member_id.id), "rank": r.rank_id.name} for r in created],
            "messageSent": False, "invoiceCreated": False,
            "version": self._version(test), "_scopeMemberIds": created.member_id.ids}

    def _promotion_history(self, site, grant, p):
        exact(p, "query")
        if not isinstance(p["query"], str) or len(p["query"]) > 100:
            raise HubProblem("INVALID_COMMAND", 400)
        domain = [("member_id.company_id", "=", site.company_id.id)]
        if p["query"].strip():
            domain.append(("member_id.name", "ilike", p["query"].strip()))
        rows = self.env["dojo.member.rank"].sudo().with_context(active_test=False).search(domain, order="date_awarded desc, id desc", limit=101)
        latest = {}
        for row in self.env["dojo.member.rank"].sudo().search([("member_id", "in", rows.member_id.ids)], order="date_awarded desc, id desc"):
            latest.setdefault(row.member_id.id, row.id)
        return {"rows": [{"id": str(r.id), "memberId": str(r.member_id.id), "name": r.member_id.name, "rank": r.rank_id.name,
            "stripes": r.stripe_count, "date": str(r.date_awarded), "memberVersion": self._version(r.member_id),
            "source": r.test_registration_id.test_id.name if r.test_registration_id else "Individual award",
            "reversed": not r.active, "reversedBy": r.reversed_by.name or None, "reversedAt": iso(r.reversed_at) if r.reversed_at else None,
            "reversalReason": r.reversal_reason or None, "latest": latest.get(r.member_id.id) == r.id and r.active} for r in rows[:100]],
            "more": len(rows) > 100}

    def _reverse_promotion(self, site, grant, p):
        exact(p, "rankHistoryId expectedVersion reason")
        reason = text(p["reason"], 500)
        row = self.env["dojo.member.rank"].sudo().search([("id", "=", record_id(p["rankHistoryId"])),
            ("member_id.company_id", "=", site.company_id.id), ("active", "=", True)], limit=1)
        if not row:
            raise HubProblem("PROMOTION_UNAVAILABLE", 404)
        member = row.member_id
        self._lock(member)
        self._lock(row)
        self._require_version(member, p["expectedVersion"])
        latest = self.env["dojo.member.rank"].sudo().search([("member_id", "=", member.id)], order="date_awarded desc, id desc", limit=1)
        if latest != row:
            raise HubProblem("REVERSAL_NOT_LATEST")
        before = member.current_rank_id.name or None
        row.write({"active": False, "reversed_at": fields.Datetime.now(), "reversed_by": self.env.uid, "reversal_reason": reason})
        self._touch(member)
        member.invalidate_recordset()
        return {"memberId": str(member.id), "rankHistoryId": str(row.id), "state": "promotion_reversed", "reason": reason,
            "before": before, "after": member.current_rank_id.name or None, "version": self._version(member)}
