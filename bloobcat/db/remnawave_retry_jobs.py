from tortoise import fields, models


class RemnaWaveRetryJobs(models.Model):
    id = fields.IntField(pk=True)
    job_type = fields.CharField(max_length=64)
    user_id = fields.BigIntField()
    remnawave_uuid = fields.CharField(max_length=64)
    attempts = fields.IntField(default=0)
    next_retry_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharField(max_length=24, default="pending")
    last_error = fields.CharField(max_length=1024, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "remnawave_retry_jobs"
        indexes = (("status", "next_retry_at"), ("job_type", "user_id"))
