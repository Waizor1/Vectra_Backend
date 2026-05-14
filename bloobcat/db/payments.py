from tortoise import fields, models # type: ignore
from tortoise.contrib.pydantic import pydantic_model_creator # type: ignore


class ProcessedPayments(models.Model):
    id = fields.IntField(primary_key=True)
    payment_id = fields.CharField(max_length=100, unique=True)
    provider = fields.CharField(max_length=32, default="yookassa")
    client_request_id = fields.CharField(max_length=100, null=True)
    payment_url = fields.TextField(null=True)
    provider_payload = fields.TextField(null=True)
    user_id = fields.BigIntField()
    amount = fields.DecimalField(max_digits=10, decimal_places=2)
    # Разбивка суммы платежа:
    # - amount_external: реальная сумма (например, YooKassa)
    # - amount_from_balance: сумма списания с бонусного/реферального баланса
    amount_external = fields.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_from_balance = fields.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Назначение платежа: "subscription" | "lte_topup" | "devices_topup".
    # NULL трактуется как подписка (legacy-записи до миграции 116).
    # Используется маркетинговой логикой (сегменты) и аналитикой, чтобы
    # топапы не считались «первой покупкой подписки».
    payment_purpose = fields.CharField(max_length=32, null=True)
    processed_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharField(max_length=50)  # succeeded, refunded, canceled
    processing_state = fields.CharField(max_length=32, default="idle")
    effect_applied = fields.BooleanField(default=False)
    attempt_count = fields.IntField(default=0)
    last_attempt_at = fields.DatetimeField(null=True)
    last_source = fields.CharField(max_length=32, null=True)
    last_error = fields.TextField(null=True)

    class Meta:
        table = "processed_payments"


ProcessedPayment_Pydantic = pydantic_model_creator(ProcessedPayments, name="ProcessedPayment")
