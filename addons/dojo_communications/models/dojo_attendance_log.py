import logging

from odoo import api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# Phone fields on res.partner that we look up for SMS destinations
_MOBILE_FIELDS = ("mobile", "phone")


def _partner_mobile(partner):
    """Return the best mobile number for a res.partner record, or False."""
    for f in _MOBILE_FIELDS:
        val = getattr(partner, f, False)
        if val:
            return val
    return False


class DojoAttendanceLog(models.Model):
    _inherit = "dojo.attendance.log"

    notification_sent = fields.Boolean(
        string="Parent Notified",
        default=False,
        help="Set to True once the check-in notification has been dispatched.",
    )

    # ------------------------------------------------------------------
    # Override create to send parent notification on every new check-in
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.status in ("present", "late"):
                try:
                    self._send_checkin_notification(record)
                except Exception as exc:  # noqa: BLE001
                    _logger.error(
                        "dojo_communications: check-in notification failed for member %s: %s",
                        record.member_id.display_name,
                        exc,
                    )
        return records

    # ------------------------------------------------------------------
    # Notification dispatch
    # ------------------------------------------------------------------

    def _send_checkin_notification(self, log):
        """Send SMS + email to the primary guardian of the checked-in member."""
        member = log.member_id
        household = member.partner_id.parent_id

        # Resolve notification recipient
        if household.is_household and household.primary_guardian_id:
            guardian_partner = household.primary_guardian_id
        else:
            # No household; notify the member themselves (adult student)
            guardian_partner = member.partner_id

        # ---- Email ----
        email_template = self.env.ref(
            "dojo_communications.mail_template_checkin_email",
            raise_if_not_found=False,
        )
        if email_template and guardian_partner.email:
            email_template.send_mail(
                log.id,
                force_send=True,
                email_values={"email_to": guardian_partner.email},
            )

        # ---- SMS ----
        sms_template = self.env.ref(
            "dojo_communications.mail_template_checkin_sms",
            raise_if_not_found=False,
        )
        mobile = _partner_mobile(guardian_partner)
        if sms_template and mobile:
            source = html2plaintext(str(sms_template.body_html or "")).strip()
            body = source and self.env["mail.render.mixin"].with_context(
                lang=guardian_partner.lang or self.env.lang
            )._render_template(source, log._name, [log.id], engine="inline_template")[log.id]
            body_plain = body and html2plaintext(str(body)).strip()
            self.env["sms.sms"].create(
                {
                    "number": mobile,
                    "body": body_plain or f"{member.display_name} just checked in to {log.session_id.name}.",
                    "partner_id": guardian_partner.id,
                }
            ).send()

        log.notification_sent = True
