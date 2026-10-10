"""A bounded, audited Companion read action. No generated text or arbitrary ORM."""
import hashlib
import json
from psycopg2 import errors as pg_errors
from odoo import fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .dojo_kiosk_v2 import KEY, KioskProblem, iso, problem, record_id


class DojoCompanionReviewReceipt(models.Model):
    _name = "dojo.companion.review.receipt"
    _description = "Companion Attendance Review Evidence"
    config_id = fields.Many2one("dojo.kiosk.config", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one("dojo.member", required=True, ondelete="restrict", index=True)
    idempotency_key = fields.Char(required=True, index=True)
    fingerprint = fields.Char(required=True)
    response_json = fields.Text(required=True)
    _unique_review_key = models.Constraint("unique(config_id, idempotency_key)", "Review key already used.")


class DojoCompanionReviewService(models.AbstractModel):
    _inherit = "dojo.kiosk.service"

    def _v2_review_attendance(self, token, gateway_key, command):
        correlation = "companion-invalid-request"
        try:
            with self.env.cr.savepoint():
                config = self._v2_authorize(token, gateway_key, staff=True)
                self = self.sudo().with_company(config.company_id).with_context(allowed_company_ids=[config.company_id.id])
                if not isinstance(command, dict) or set(command) != {"idempotencyKey", "correlationId", "suggestionId", "memberId"}:
                    raise KioskProblem("INVALID_COMMAND")
                for field in ("idempotencyKey", "correlationId"):
                    if not isinstance(command[field], str) or not KEY.fullmatch(command[field]):
                        raise KioskProblem("INVALID_COMMAND")
                correlation = command["correlationId"]
                record_id(command["memberId"])
                if command["suggestionId"] != "attendance-review:" + command["memberId"]:
                    raise KioskProblem("INVALID_COMMAND")
                member = self._v2_member(config, command["memberId"])
                fingerprint = hashlib.sha256(command["suggestionId"].encode()).hexdigest()
                self._v2_lock("companion:%s:%s" % (config.id, command["idempotencyKey"]))
                Receipt = self.env["dojo.companion.review.receipt"].sudo()
                saved = Receipt.search([("config_id", "=", config.id), ("idempotency_key", "=", command["idempotencyKey"])], limit=1)
                if saved:
                    if saved.fingerprint != fingerprint:
                        raise KioskProblem("IDEMPOTENCY_CONFLICT")
                    result = json.loads(saved.response_json)
                    result.update(replayed=True, correlationId=correlation)
                    return result
                data = self._v2_member_read(config, str(member.id))
                latest = data["attendance"]["latest"]
                count = data["attendance"]["lastSevenDays"]
                text = "Verified in Odoo: %s check-in%s in the last seven days." % (count, "" if count == 1 else "s")
                if latest:
                    text += " Latest class: " + latest["sessionTitle"] + "."
                saved = Receipt.create({"config_id": config.id, "company_id": config.company_id.id,
                    "member_id": member.id, "idempotency_key": command["idempotencyKey"],
                    "fingerprint": fingerprint, "response_json": "{}"})
                result = {"receipt": {"id": str(saved.id), "suggestionId": command["suggestionId"],
                    "summary": text, "actor": "Authorized staff test session", "at": iso(fields.Datetime.now())},
                    "replayed": False, "correlationId": correlation,
                    "evidence": {"source": "odoo-test", "memberId": str(member.id), "attendance": data["attendance"]}}
                saved.response_json = json.dumps(result, sort_keys=True)
                return result
        except KioskProblem as exc:
            return problem(exc.code, correlation)
        except AccessError:
            return problem("FORBIDDEN", correlation)
        except (ValidationError, UserError):
            return problem("CAPABILITY_DISABLED", correlation)
        except (pg_errors.UniqueViolation, pg_errors.SerializationFailure, pg_errors.LockNotAvailable, pg_errors.DeadlockDetected):
            return problem("CONCURRENT_RETRY", correlation)
