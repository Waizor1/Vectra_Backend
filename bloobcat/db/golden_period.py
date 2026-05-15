"""Golden Period — 24h invite-blitz state for active referrers.

The Golden Period is a one-shot 24-hour campaign opened to users who
accumulate at least N (default 3) days of online VPN sessions. While the
window is open, every invitee whose VPN key activates earns the referrer
+100 ₽ on internal balance up to a `cap` (default 15) total payouts.

Three tables form the lifecycle:

    GoldenPeriodConfig   singleton row, edited via admin Directus extension.
                         `is_enabled=False` by default — the entire feature
                         is dark on production until ops flip the toggle.
    GoldenPeriod         per-user 24h window. `status` moves linearly:
                         `active` → `expired` (scheduler) | `closed`
                         (admin override). One ACTIVE row per user via a
                         partial UNIQUE index, but lifetime allows future
                         repeat campaigns by inserting another row when
                         status moves off `active`.
    GoldenPeriodPayout   one row per (period, referred_user) — UNIQUE on
                         that pair guarantees idempotency. Status moves
                         `optimistic` → `confirmed` (clawback window
                         elapsed cleanly) | `clawed_back` (signals fired).

All payouts are *optimistic*: balance is credited the instant the referred
user flips `key_activated=true`, then a 6-hour scanner reconsiders within
the configurable `clawback_window_days` and reverses suspect rows. Clawback
deducts from balance first, then proportionally trims days/LTE GB from the
referrer's active tariff (math.ceil-rounded against the user) — see
`bloobcat/services/golden_period_clawback.py` for the full algorithm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:  # avoid runtime circular imports
    from bloobcat.db.users import Users  # noqa: F401


class GoldenPeriodConfig(models.Model):
    """Singleton row keyed by `slug='default'` editable via Directus admin.

    All Golden Period mutations read this row at request time, so flipping
    `is_enabled=False` is the kill-switch that stops new activations and
    new payouts within ~one cache cycle (the in-process service cache TTL).
    """

    id = fields.IntField(primary_key=True)
    slug = fields.CharField(max_length=64, unique=True, description="Singleton key, default='default'")
    default_cap = fields.IntField(default=15, description="Снапшот при создании GoldenPeriod")
    payout_amount_rub = fields.IntField(default=100, description="₽ за каждого активировавшего инвайти")
    eligibility_min_active_days = fields.IntField(
        default=3,
        description="Минимум кумулятивных активных дней онлайн для активации Golden Period",
    )
    window_hours = fields.IntField(default=24, description="Длительность окна Golden Period в часах")
    is_enabled = fields.BooleanField(
        default=False,
        description="Главный feature-flag. По умолчанию выключен на проде до явного включения через Directus.",
    )
    clawback_window_days = fields.IntField(
        default=30,
        description="Окно (в днях) после payout, в течение которого scanner может откатить балл",
    )
    message_templates = fields.JSONField(
        default=dict,
        description=(
            "Локализованные тексты уведомлений. "
            "Структура: {locale: {event: {channel: text}}}, например {'ru': {'payout': {'push': '+100₽!'}}}."
        ),
    )
    signal_thresholds = fields.JSONField(
        default=dict,
        description=(
            "Пороги детектора clawback. Поддерживаемые ключи: "
            "ip_cidr (default 24), tg_id_distance (default 5), "
            "registration_window_seconds (default 60)."
        ),
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "golden_period_configs"


class GoldenPeriod(models.Model):
    """Per-user 24-hour Golden Period window."""

    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="golden_periods",
        on_delete=fields.CASCADE,
        description="Owner of the period (referrer who earns payouts)",
    )
    config: fields.ForeignKeyNullableRelation[GoldenPeriodConfig] = fields.ForeignKeyField(
        "models.GoldenPeriodConfig",
        related_name="periods",
        null=True,
        on_delete=fields.SET_NULL,
        description="Snapshot reference to the config used to create this period",
    )
    started_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField(description="started_at + config.window_hours")
    cap = fields.IntField(description="Снапшот config.default_cap")
    payout_amount_rub = fields.IntField(default=100, description="Снапшот config.payout_amount_rub")
    paid_out_count = fields.IntField(default=0, description="Atomic counter via F-expression")
    total_paid_rub = fields.IntField(default=0)
    status = fields.CharField(
        max_length=16,
        default="active",
        description="active | expired | closed",
    )
    seen_at = fields.DatetimeField(
        null=True,
        description="Когда пользователь dismissed welcome modal (для FE-баннера)",
    )
    triggered_by_active_days = fields.IntField(description="Снапшот cumulative active days at activation")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "golden_periods"
        indexes = (
            ("user_id",),
            ("status", "expires_at"),
            ("started_at",),
        )


class GoldenPeriodPayout(models.Model):
    """One payout per (period, referred_user) — UNIQUE makes it idempotent."""

    id = fields.IntField(primary_key=True)
    golden_period: fields.ForeignKeyRelation[GoldenPeriod] = fields.ForeignKeyField(
        "models.GoldenPeriod",
        related_name="payouts",
        on_delete=fields.CASCADE,
    )
    referrer_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="golden_payouts_received",
        on_delete=fields.CASCADE,
        description="Duplicate of golden_period.user_id for indexing/listing",
    )
    referred_user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="golden_payouts_caused",
        on_delete=fields.CASCADE,
    )
    amount_rub = fields.IntField()
    status = fields.CharField(
        max_length=16,
        default="optimistic",
        description="optimistic | confirmed | clawed_back",
    )
    paid_at = fields.DatetimeField(auto_now_add=True)
    confirmed_at = fields.DatetimeField(null=True)
    clawed_back_at = fields.DatetimeField(null=True)
    clawback_reason = fields.CharField(
        max_length=64,
        null=True,
        description="hwid_overlap | ip_block | device_fp | tg_family | velocity",
    )
    clawback_payload = fields.JSONField(
        null=True,
        description="Полный snapshot сигналов для аудита",
    )
    clawback_balance_rub = fields.IntField(
        null=True,
        description="Сколько ₽ списано с balance",
    )
    clawback_days_removed = fields.IntField(
        null=True,
        description="Сколько дней снято с активного тарифа",
    )
    clawback_lte_gb_removed = fields.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        description="Сколько GB LTE снято",
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "golden_period_payouts"
        unique_together = (("golden_period", "referred_user"),)
        indexes = (
            ("referrer_user_id", "status"),
            ("status", "paid_at"),
        )
