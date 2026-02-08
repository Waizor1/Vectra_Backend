from tortoise import fields, models


class FamilyAuditLogs(models.Model):
    id = fields.UUIDField(pk=True)
    owner = fields.ForeignKeyField("models.Users", related_name="family_audit_owner", on_delete=fields.CASCADE)
    actor = fields.ForeignKeyField("models.Users", related_name="family_audit_actor", on_delete=fields.CASCADE)
    action = fields.CharField(max_length=64)
    target_id = fields.CharField(max_length=128, null=True)
    details = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "family_audit_logs"
