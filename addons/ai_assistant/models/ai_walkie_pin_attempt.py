from datetime import datetime, timedelta

from odoo import fields, models


class AiWalkiePinAttempt(models.Model):
    _name = "ai.walkie.pin.attempt"
    _description = "AI Walkie PIN Attempt Tracker"
    _rec_name = "walkie_talkie_id"

    walkie_talkie_id = fields.Many2one(
        "ai.walkie.talkie",
        required=True,
        ondelete="cascade",
        index=True,
    )
    failed_attempts = fields.Integer(default=0)
    locked_until = fields.Datetime()
    last_attempt_at = fields.Datetime()

    _ai_walkie_pin_attempt_unique_walkie = models.Constraint(
        "unique(walkie_talkie_id)",
        "Only one PIN attempt tracker can exist per walkie station.",
    )

    def clear_state(self):
        if not self.failed_attempts and not self.locked_until:
            return
        self.write({
            "failed_attempts": 0,
            "locked_until": False,
            "last_attempt_at": fields.Datetime.now(),
        })

    def get_retry_minutes(self):
        self.ensure_one()
        now = datetime.utcnow()
        if self.locked_until and now < self.locked_until:
            return int((self.locked_until - now).total_seconds() / 60) + 1
        if self.locked_until:
            self.clear_state()
        return 0

    def register_failure(self, max_attempts, lockout_minutes):
        self.ensure_one()
        failed_attempts = (self.failed_attempts or 0) + 1
        vals = {
            "failed_attempts": failed_attempts,
            "last_attempt_at": fields.Datetime.now(),
            "locked_until": False,
        }
        if failed_attempts >= max_attempts:
            vals.update({
                "failed_attempts": 0,
                "locked_until": datetime.utcnow() + timedelta(minutes=lockout_minutes),
            })
            self.write(vals)
            return {
                "success": False,
                "error": "locked",
                "retry_in_minutes": lockout_minutes,
            }
        self.write(vals)
        return {
            "success": False,
            "error": "wrong_pin",
            "remaining_tries": max_attempts - failed_attempts,
        }