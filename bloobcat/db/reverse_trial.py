"""ReverseTrialState — per-user lifecycle record for the 7-day reverse trial.

The reverse trial flips the new-user flow: instead of starting on a 1 GB free
trial that is hard to convert, every fresh registration immediately receives
the top-tier paid tariff for `reverse_trial_days` days. When the window
expires, the user is downgraded to the regular free state and handed a
single-use -50 % personal discount valid for 14 days. Conversion lands inside
the discount window or the discount silently expires.

One row per user is the contract: the UNIQUE constraint on `user_id` prevents
duplicate grants and makes the table the source-of-truth for "did this user
ever receive a reverse trial?". The lifecycle status moves linearly:
    active → expired (downgrade scheduler) | converted_to_paid (paid purchase)
            | cancelled (admin / abuse signal).
"""

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:  # avoid runtime circular import with users
    from bloobcat.db.users import Users  # noqa: F401
    from bloobcat.db.discounts import PersonalDiscount  # noqa: F401


class ReverseTrialState(models.Model):
    """One-per-user lifecycle row for the 7-day reverse trial."""

    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="reverse_trial_states",
        on_delete=fields.CASCADE,
        unique=True,
        description="Owner of the reverse trial. UNIQUE — one grant per lifetime.",
    )
    granted_at = fields.DatetimeField(
        auto_now_add=True,
        description="Когда был выдан reverse trial",
    )
    expires_at = fields.DatetimeField(
        description="Когда reverse trial истекает (granted_at + reverse_trial_days)",
    )
    status = fields.CharField(
        max_length=24,
        default="active",
        description="active | expired | converted_to_paid | cancelled",
    )
    tariff_sku_snapshot = fields.CharField(
        max_length=64,
        null=True,
        description="Snapshot of the granted tariff sku/name for analytics",
    )
    tariff_active_id_snapshot = fields.CharField(
        max_length=5,
        null=True,
        description=(
            "Soft FK to active_tariffs.id (the synthetic row created for this trial). "
            "NOT a hard FK — we keep this column readable even after the synthetic "
            "ActiveTariffs row is deleted on downgrade."
        ),
    )
    discount_personal_id = fields.IntField(
        null=True,
        description="ID of the PersonalDiscount issued at downgrade (SET NULL on delete)",
    )
    discount_used_at = fields.DatetimeField(
        null=True,
        description="Когда пользователь применил выданную при даунгрейде скидку",
    )
    downgraded_at = fields.DatetimeField(
        null=True,
        description="Когда был выполнен авто-даунгрейд после окончания trial",
    )
    pre_warning_sent_at = fields.DatetimeField(
        null=True,
        description="Когда было отправлено уведомление за день до окончания (idempotency)",
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "reverse_trial_states"
        indexes = (
            ("status", "expires_at"),
            ("user_id",),
        )
