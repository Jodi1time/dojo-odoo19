import json

from psycopg2 import errors as pg_errors
from odoo import fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import KEY, KioskProblem, record_id, session_version
from .service import HubProblem, fingerprint


class HubReceiptScope(models.Model):
    _inherit = "dojo.hub.receipt"

    scope_member_ids = fields.Many2many("dojo.member", "dojo_hub_receipt_scope_member_rel", "receipt_id", "member_id",
        string="Members affected by a reviewed bulk action", readonly=True, copy=False)


class HubReviewedActions(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        return {}

    def _require_version(self, record, expected):
        if type(expected) is not int or expected != self._version(record):
            raise HubProblem("VERSION_CONFLICT")

    def _version(self, record):
        return session_version(record)

    def _touch(self, record):
        record.write({"active": record.active} if "active" in record._fields else {})
        record.invalidate_recordset()

    def _id_list(self, values, limit):
        if not isinstance(values, list) or len(values) > limit or len(set(map(str, values))) != len(values):
            raise HubProblem("INVALID_COMMAND", 400)
        return [record_id(v) for v in values]

    def _dispatch(self, site_id, operation, payload, request_key=None):
        operations = self._reviewed_operations()
        if not isinstance(operation, str) or operation not in operations:
            return super()._dispatch(site_id, operation, payload, request_key)
        spec = operations[operation]
        try:
            with self.env.cr.savepoint():
                site, grant = self._principal(site_id)
                self._role(grant, spec["roles"])
                if not isinstance(payload, dict):
                    raise HubProblem("INVALID_COMMAND", 400)
                if not spec["write"]:
                    return spec["handler"](site, grant, payload)
                if not isinstance(request_key, str) or not KEY.fullmatch(request_key):
                    raise HubProblem("INVALID_COMMAND", 400)
                self.env["dojo.kiosk.service"]._v2_lock("hub:%s:%s:%s" % (site.id, self.env.uid, request_key))
                digest = fingerprint({"operation": operation, "payload": spec.get("audit", lambda p: p)(payload)})
                receipt = self.env["dojo.hub.receipt"].sudo().search([
                    ("site_id", "=", site.id), ("user_id", "=", self.env.uid), ("request_key", "=", request_key)], limit=1)
                if receipt:
                    if receipt.fingerprint != digest or receipt.operation != operation:
                        raise HubProblem("IDEMPOTENCY_CONFLICT")
                    saved = json.loads(receipt.response_json)
                    if spec.get("replay"):
                        spec["replay"](site, grant, receipt, saved)
                    return {**saved, "replayed": True}
                result = spec["handler"](site, grant, payload)
                scope = result.pop("_scopeMemberIds", [])
                self.env["dojo.hub.receipt"].sudo().create({"site_id": site.id, "user_id": self.env.uid,
                    "request_key": request_key, "operation": operation, "fingerprint": digest,
                    "member_id": int(result["memberId"]) if result.get("memberId") else False,
                    "session_id": int(result["sessionId"]) if result.get("sessionId") else False,
                    "lead_id": int(result["leadId"]) if result.get("leadId") else False,
                    "task_id": int(result["taskId"]) if result.get("taskId") else False,
                    "scope_member_ids": [(6, 0, scope)], "response_json": json.dumps(result)})
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
