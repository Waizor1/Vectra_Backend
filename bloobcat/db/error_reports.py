from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class ErrorReports(models.Model):
    id = fields.UUIDField(primary_key=True)
    user_id = fields.BigIntField(null=True)
    event_id = fields.CharField(max_length=64)
    code = fields.CharField(max_length=128)
    type = fields.CharField(max_length=64)
    message = fields.CharField(max_length=1024, null=True)
    name = fields.CharField(max_length=256, null=True)
    stack = fields.TextField(null=True)
    route = fields.CharField(max_length=512, null=True)
    href = fields.CharField(max_length=1024, null=True)
    user_agent = fields.CharField(max_length=512, null=True)
    extra = fields.JSONField(null=True)
    triage_severity = fields.CharField(max_length=24, default="medium")
    triage_status = fields.CharField(max_length=24, default="new")
    triage_owner = fields.CharField(max_length=128, null=True)
    triage_note = fields.TextField(null=True)
    triage_due_at = fields.DatetimeField(null=True)
    triage_updated_at = fields.DatetimeField(auto_now=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    reported_at = fields.DatetimeField(null=True)

    class Meta:
        table = "error_reports"


ErrorReports_Pydantic = pydantic_model_creator(ErrorReports, name="ErrorReports")
