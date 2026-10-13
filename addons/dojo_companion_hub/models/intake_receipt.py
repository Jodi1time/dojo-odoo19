"""A database uniqueness backstop for different-key or different-staff races."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from .intake_validation import normalized_name
from .service import fingerprint


class IntakeReceipt(models.Model):
    _inherit = "dojo.hub.receipt"

    intake_identity_key = fields.Char(readonly=True, copy=False, index=True)
    _unique_intake_identity = models.Constraint(
        "unique(intake_identity_key)",
        "This student intake has already been recorded. Review the existing native profile.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        values = [dict(v) for v in vals_list]
        for value in values:
            # The key is generated from the created native record, never accepted
            # from the client. Other audit operations leave it NULL.
            value.pop("intake_identity_key", None)
            if value.get("operation") == "create_student":
                member = self.env["dojo.member"].sudo().browse(value.get("member_id")).exists()
                if len(member) != 1 or not member.company_id or not member.date_of_birth:
                    raise ValidationError("A student creation receipt requires its native identity.")
                value["intake_identity_key"] = fingerprint({"company": member.company_id.id,
                    "name": normalized_name(member.name), "birth": str(member.date_of_birth)})
        return super().create(values)
