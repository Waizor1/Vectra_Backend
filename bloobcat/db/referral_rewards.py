from tortoise import fields, models


class ReferralRewards(models.Model):
    """Ledger of referral rewards (source of truth).

    We use a unique constraint to ensure:
    - only ONE "first payment" reward per referred user (anti-abuse + idempotency)
    - safe behavior under concurrent webhooks / retries
    """

    id = fields.IntField(pk=True)

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

