from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class AnalyticsPaymentEvents(models.Model):
    """
    Normalized successful payment ledger for service-growth analytics.

    One row per processed payment id. This keeps Directus analytics away from
    parsing provider JSON payloads on every dashboard load.
    """

    id = fields.IntField(primary_key=True)
    payment_id = fields.CharField(max_length=128, unique=True)
    user_id = fields.BigIntField(db_index=True)
    paid_at = fields.DatetimeField(db_index=True)
    provider = fields.CharField(max_length=32, null=True)
    kind = fields.CharField(max_length=32, default="subscription")
    tariff_kind = fields.CharField(max_length=32, null=True)
    months = fields.IntField(null=True)
    device_count = fields.IntField(null=True)
    subscription_revenue_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    lte_revenue_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_external_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_from_balance_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    lte_gb_purchased = fields.FloatField(default=0.0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "analytics_payment_events"


class AnalyticsServiceDaily(models.Model):
    """
    Daily paid-service totals consumed by Directus.

    Products:
    - main_paid: main subscription traffic/revenue excluding trial traffic
    - lte_paid: paid LTE traffic/revenue excluding trial traffic
    - all_paid: combined paid service totals
    """

    id = fields.IntField(primary_key=True)
    day = fields.DateField()
    product = fields.CharField(max_length=32)
    traffic_bytes = fields.BigIntField(default=0)
    traffic_gb = fields.FloatField(default=0.0)
    subscription_revenue_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    lte_revenue_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_external_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_from_balance_rub = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    lte_gb_purchased = fields.FloatField(default=0.0)
    payments_count = fields.IntField(default=0)
    paying_users = fields.IntField(default=0)
    rub_per_gb = fields.FloatField(default=0.0)
    last_calculated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "analytics_service_daily"
        unique_together = (("day", "product"),)


class AnalyticsTrialDaily(models.Model):
    """Daily trial totals and the top trial traffic consumer for abuse visibility."""

    id = fields.IntField(primary_key=True)
    day = fields.DateField(unique=True)
    new_trials = fields.IntField(default=0)
    active_trials = fields.IntField(default=0)
    traffic_bytes = fields.BigIntField(default=0)
    traffic_gb = fields.FloatField(default=0.0)
    top_user_id = fields.BigIntField(null=True)
    top_user_traffic_gb = fields.FloatField(default=0.0)
    flagged_users_count = fields.IntField(default=0)
    last_calculated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "analytics_trial_daily"


class AnalyticsTrialRiskFlags(models.Model):
    """Operator-visible trial traffic risk flags. v1 flags only; it does not block users."""

    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField(db_index=True)
    day = fields.DateField(db_index=True)
    traffic_gb = fields.FloatField(default=0.0)
    share_pct = fields.FloatField(default=0.0)
    reason = fields.CharField(max_length=64)
    severity = fields.CharField(max_length=24, default="warning")
    status = fields.CharField(max_length=24, default="new")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "analytics_trial_risk_flags"
        unique_together = (("user_id", "day", "reason"),)


AnalyticsPaymentEvent_Pydantic = pydantic_model_creator(
    AnalyticsPaymentEvents, name="AnalyticsPaymentEvent"
)
AnalyticsServiceDaily_Pydantic = pydantic_model_creator(
    AnalyticsServiceDaily, name="AnalyticsServiceDaily"
)
AnalyticsTrialDaily_Pydantic = pydantic_model_creator(
    AnalyticsTrialDaily, name="AnalyticsTrialDaily"
)
AnalyticsTrialRiskFlag_Pydantic = pydantic_model_creator(
    AnalyticsTrialRiskFlags, name="AnalyticsTrialRiskFlag"
)
