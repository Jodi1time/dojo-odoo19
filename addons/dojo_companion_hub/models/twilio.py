import os
import re

import requests

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.dojo_kiosk.models.dojo_kiosk_v2 import iso
from .service import HubProblem, exact, text

TWILIO_TEST_FROM = "+15005550006"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json"
ACCOUNT = re.compile(r"^AC[0-9a-f]{32}$")
TOKEN = re.compile(r"^[0-9a-f]{32}$")
MESSAGE = re.compile(r"^(SM|MM)[0-9a-f]{32}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
DISPATCH_LIMIT = 100


def e164(value):
    digits = re.sub(r"[^0-9]", "", value or "")
    if (value or "").strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


class HubSiteTwilio(models.Model):
    _inherit = "dojo.hub.site"

    twilio_account_sid_env = fields.Char(groups="base.group_system",
        help="Name of the Odoo process environment variable holding the Twilio TEST Account SID.")
    twilio_auth_token_env = fields.Char(groups="base.group_system",
        help="Name of the Odoo process environment variable holding the Twilio TEST auth token.")
    twilio_from_number = fields.Char(groups="base.group_system",
        help="Only Twilio's test sender +15005550006 is accepted, so no real text can be delivered.")

    @api.constrains("twilio_account_sid_env", "twilio_auth_token_env", "twilio_from_number")
    def _check_twilio_test_only(self):
        for site in self:
            for name in (site.twilio_account_sid_env, site.twilio_auth_token_env):
                if name and not ENV_NAME.fullmatch(name):
                    raise ValidationError("Twilio credentials must be referenced by environment variable name.")
            if site.twilio_from_number and site.twilio_from_number != TWILIO_TEST_FROM:
                raise ValidationError("Only Twilio's test sender number is allowed until live sending is approved.")

    def _twilio(self):
        self.ensure_one()
        if not self.twilio_account_sid_env or not self.twilio_auth_token_env or self.twilio_from_number != TWILIO_TEST_FROM:
            return None
        account = os.environ.get(self.twilio_account_sid_env, "")
        token = os.environ.get(self.twilio_auth_token_env, "")
        if not ACCOUNT.fullmatch(account) or not TOKEN.fullmatch(token):
            return None
        return account, token


class HubBroadcastRecipientDispatch(models.Model):
    _inherit = "dojo.hub.broadcast.recipient"

    state = fields.Selection(selection_add=[("dispatching", "Sending"), ("uncertain", "Needs reconciliation")],
        ondelete={"dispatching": "set default", "uncertain": "set default"})
    dispatched_at = fields.Datetime()


class HubTwilioDispatch(models.AbstractModel):
    _inherit = "dojo.hub.service"

    def _reviewed_operations(self):
        operations = super()._reviewed_operations()
        operations["dispatch_broadcast"] = {"handler": self._dispatch_broadcast, "roles": "owner manager", "write": True,
            "replay": self._replay_broadcast}
        return operations

    def _provider_state(self, site):
        return "twilio_test" if site.sudo()._twilio() else super()._provider_state(site)

    def _broadcast_row(self, row):
        data = super()._broadcast_row(row)
        data["providerRefs"] = len(row.recipient_ids.filtered(lambda r: r.provider_ref))
        return data

    def _twilio_mark(self, recipient_id, state, reason, provider_ref=None, expected=None):
        with self.env.registry.cursor() as cr:
            cr.execute("SELECT state FROM dojo_hub_broadcast_recipient WHERE id = %s FOR UPDATE NOWAIT", [recipient_id])
            row = cr.fetchone()
            if not row or (expected and row[0] != expected):
                return False
            cr.execute("""UPDATE dojo_hub_broadcast_recipient SET state = %s, reason = %s, provider_ref = coalesce(%s, provider_ref),
                dispatched_at = (now() at time zone 'UTC'), write_date = (now() at time zone 'UTC') WHERE id = %s""",
                [state, reason, provider_ref, recipient_id])
            cr.commit()
        return True

    def _twilio_send(self, credentials, recipient_id, number, body):
        if not self._twilio_mark(recipient_id, "dispatching", "dispatch_started", expected="queued"):
            return "skipped", None, None
        state, reference, reason, detail = "uncertain", None, "provider_result_requires_reconciliation", None
        try:
            response = requests.post(TWILIO_URL % credentials[0], auth=credentials,
                data={"To": number, "From": TWILIO_TEST_FROM, "Body": body}, timeout=(5, 15), allow_redirects=False)
            payload = response.json() if len(response.content) <= 65536 else None
            if response.status_code in (200, 201) and isinstance(payload, dict) and MESSAGE.fullmatch(str(payload.get("sid") or "")):
                state, reference, reason = "accepted", payload["sid"], "accepted_test_credentials_not_delivered"
            elif 400 <= response.status_code < 500 and isinstance(payload, dict):
                state, reason = "failed", "twilio_%s" % int(payload.get("code") or response.status_code)
            else:
                detail = "http_%s" % response.status_code
        except (requests.RequestException, ValueError, TypeError) as exc:
            detail = type(exc).__name__
        self._twilio_mark(recipient_id, state, reason, provider_ref=reference, expected="dispatching")
        return state, reference, reason if state != "uncertain" else "%s:%s" % (reason, detail)

    def _dispatch_broadcast(self, site, grant, p):
        exact(p, "broadcastId expectedVersion reason")
        reason = text(p["reason"], 500)
        row = self._broadcast(site, p["broadcastId"])
        self._lock(row)
        self._require_version(row, p["expectedVersion"])
        if row.state != "approved":
            raise HubProblem("BROADCAST_NOT_APPROVED")
        if row.channel != "sms":
            raise HubProblem("BROADCAST_CHANNEL_NOT_CONNECTED", 422)
        credentials = site.sudo()._twilio()
        if not credentials:
            raise HubProblem("PROVIDER_NOT_CONFIGURED", 503)
        if row.scheduled_at and row.scheduled_at > fields.Datetime.now():
            raise HubProblem("BROADCAST_NOT_DUE")
        queued = row.recipient_ids.filtered(lambda r: r.state == "queued").sorted("id")[:DISPATCH_LIMIT]
        if not queued:
            raise HubProblem("BROADCAST_NOTHING_QUEUED")
        counts = {"accepted": 0, "failed": 0, "uncertain": 0, "excluded": 0, "skipped": 0}
        references, problems = [], {}
        for recipient in queued:
            partner = recipient.partner_id
            number = e164(recipient.address)
            if self._address(partner, "sms") != recipient.address or self._exclusion(site, partner, "sms", row.purpose) or not number:
                if self._twilio_mark(recipient.id, "excluded", "consent_or_contact_changed" if number else "invalid_number", expected="queued"):
                    counts["excluded"] += 1
                continue
            state, reference, outcome = self._twilio_send(credentials, recipient.id, number, row.body)
            counts[state] += 1
            if state in ("failed", "uncertain"):
                problems[outcome] = problems.get(outcome, 0) + 1
            if reference:
                references.append(reference)
        row.write({"note": "\n".join(filter(None, [row.note, "%s %s sent through Twilio test credentials: %s" % (
            iso(fields.Datetime.now()), self.env.user.name, reason)]))})
        return {"broadcastId": str(row.id), "state": "broadcast_dispatched", "provider": "twilio_test", **counts,
            "providerReferences": len(references), "problems": problems, "realMessagesSent": False, "deliveryConfirmed": False, "messageSent": False,
            "version": self._version(row)}
