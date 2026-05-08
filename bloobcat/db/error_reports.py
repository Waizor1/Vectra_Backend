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

    # Release / build context
    app_version = fields.CharField(max_length=64, null=True)
    commit_sha = fields.CharField(max_length=64, null=True)
    bundle_hash = fields.CharField(max_length=64, null=True)

    # Session / platform context
    session_id = fields.CharField(max_length=64, null=True)
    platform = fields.CharField(max_length=32, null=True)
    tg_platform = fields.CharField(max_length=32, null=True)
    tg_version = fields.CharField(max_length=16, null=True)
    viewport_w = fields.IntField(null=True)
    viewport_h = fields.IntField(null=True)
    dpr = fields.FloatField(null=True)
    connection_type = fields.CharField(max_length=16, null=True)
    locale = fields.CharField(max_length=16, null=True)

    # User-action context (ring buffer of last actions before crash)
    breadcrumbs = fields.JSONField(null=True)

    # Client severity hint (server validates against an allow-list)
    severity_hint = fields.CharField(max_length=16, null=True)

    # Server-side correlation
    request_id = fields.CharField(max_length=64, null=True)

    # Runtime context — captured at the moment of failure
    page_age_ms = fields.IntField(null=True)
    document_ready_state = fields.CharField(max_length=16, null=True)
    document_visibility_state = fields.CharField(max_length=16, null=True)
    online = fields.BooleanField(null=True)
    save_data = fields.BooleanField(null=True)
    hardware_concurrency = fields.IntField(null=True)
    device_memory = fields.FloatField(null=True)
    js_heap_used_mb = fields.FloatField(null=True)
    js_heap_total_mb = fields.FloatField(null=True)
    js_heap_limit_mb = fields.FloatField(null=True)
    sw_controller = fields.CharField(max_length=256, null=True)
    referrer = fields.CharField(max_length=1024, null=True)

    # Dedup grouping
    fingerprint = fields.CharField(max_length=64, null=True, db_index=True)
    occurrences = fields.IntField(default=1)
    first_seen_at = fields.DatetimeField(null=True)
    last_seen_at = fields.DatetimeField(null=True)

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
