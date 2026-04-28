from tortoise import fields, models


class ReferralRewards(models.Model):
    """Ledger of referral rewards (source of truth).

    We use a unique constraint to ensure:
    - only ONE "first payment" reward per referred user (anti-abuse + idempotency)
    - safe behavior under concurrent webhooks / retries
    """

    id = fields.IntField(primary_key=True)

    referred_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="referral_rewards_referred",
        on_delete=fields.CASCADE,
    )
    referrer_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="referral_rewards_referrer",
        on_delete=fields.CASCADE,
    )

    kind = fields.CharField(max_length=32, default="first_payment")
    payment_id = fields.CharField(max_length=128, null=True)

    months = fields.IntField(null=True)
    device_count = fields.IntField(null=True)
    amount_rub = fields.IntField(null=True)

    friend_bonus_days = fields.IntField(default=0)
    referrer_bonus_days = fields.IntField(default=0)
    applied_to_subscription = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "referral_rewards"
        indexes = ("referred_user_id", "referrer_user_id", "kind")
        unique_together = (("referred_user_id", "kind"),)


class ReferralCashbackRewards(models.Model):
    """Internal-balance cashback ledger for the ordinary referral program.

    This is intentionally separate from PartnerEarnings: ordinary users earn only
    in-service balance and never withdrawable partner income.
    """

    id = fields.IntField(primary_key=True)
    payment_id = fields.CharField(max_length=128, unique=True)
    referrer_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="referral_cashback_rewards_referrer",
        on_delete=fields.CASCADE,
    )
    referred_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="referral_cashback_rewards_referred",
        on_delete=fields.CASCADE,
    )
    amount_external_rub = fields.IntField(default=0)
    cashback_percent = fields.IntField(default=0)
    reward_rub = fields.IntField(default=0)
    level_key = fields.CharField(max_length=32)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "referral_cashback_rewards"
        indexes = (
            "payment_id",
            "referrer_user_id",
            "referred_user_id",
            "level_key",
            "created_at",
        )


class ReferralLevelRewards(models.Model):
    """One free level chest per reached ordinary referral level."""

    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="referral_level_rewards",
        on_delete=fields.CASCADE,
    )
    level_key = fields.CharField(max_length=32)
    status = fields.CharField(max_length=16, default="available")
    reward_type = fields.CharField(max_length=32, null=True)
    reward_value = fields.IntField(null=True)
    opened_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "referral_level_rewards"
        indexes = ("user_id", "status", "level_key")
        unique_together = (("user_id", "level_key"),)
