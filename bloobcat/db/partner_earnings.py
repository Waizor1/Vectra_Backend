from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class PartnerEarnings(models.Model):
    """
    Partner earnings event.

    One row per successful payment of a referred user (idempotent by payment_id).
    Used to build partner profit chart, totals, and QR/source attribution.
    """

    id = fields.UUIDField(primary_key=True)

    # Payment id from ProcessedPayments.payment_id / YooKassa webhook / balance payment id.
    payment_id = fields.CharField(max_length=100, unique=True)

    partner = fields.ForeignKeyField(
        "models.Users",
        related_name="partner_earnings",
        on_delete=fields.CASCADE,
    )
    referral_id = fields.BigIntField()

    # Optional attribution to a partner QR code.
    qr_code = fields.ForeignKeyField(
        "models.PartnerQr",
        related_name="earnings",
        null=True,
        on_delete=fields.SET_NULL,
    )

    # Attribution channel (helps distinguish "Referral link" vs QR codes in analytics).
    # Allowed values: "qr" | "referral_link" | "unknown"
    source = fields.CharField(max_length=24, default="unknown")

    amount_total_rub = fields.IntField()
    reward_rub = fields.IntField()
    percent = fields.IntField()

    # Review state for partner-cashback fraud screening (HWID overlap, etc.).
    # "active": balance was credited immediately (default for clean cases).
    # "pending_review": cashback frozen, admin must approve/reject in bot.
    # "approved": admin approved, balance credited retroactively.
    # "rejected": admin rejected, balance never credited.
    review_status = fields.CharField(max_length=24, default="active")
    # Snapshot of fraud signals captured at award time (e.g. shared HWIDs).
    review_signals = fields.JSONField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "partner_earnings"


PartnerEarnings_Pydantic = pydantic_model_creator(PartnerEarnings, name="PartnerEarnings")
