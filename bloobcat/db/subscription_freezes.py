from tortoise import fields, models


class SubscriptionFreezes(models.Model):
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField(db_index=True)
    freeze_reason = fields.CharField(max_length=32, default="family_overlay")
    is_active = fields.BooleanField(default=True, db_index=True)

    base_remaining_days = fields.IntField(default=0)
    base_expires_at_snapshot = fields.DateField(null=True)
    family_expires_at = fields.DateField()

    base_tariff_name = fields.CharField(max_length=255, null=True)
    base_tariff_months = fields.IntField(null=True)
    base_tariff_price = fields.IntField(null=True)
    base_hwid_limit = fields.IntField(null=True)
    base_lte_gb_total = fields.IntField(null=True)
    base_lte_gb_used = fields.FloatField(null=True)
    base_lte_price_per_gb = fields.FloatField(null=True)
    base_progressive_multiplier = fields.FloatField(null=True)
    base_residual_day_fraction = fields.FloatField(null=True)

    resume_applied = fields.BooleanField(default=False)
    resumed_at = fields.DatetimeField(null=True)
    resume_attempt_count = fields.IntField(default=0)
    last_resume_error = fields.TextField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "subscription_freezes"
