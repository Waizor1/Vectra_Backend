from tortoise import fields, models # type: ignore
from tortoise.contrib.pydantic import pydantic_model_creator # type: ignore


class ProcessedPayments(models.Model):
    id = fields.IntField(pk=True)
    payment_id = fields.CharField(max_length=100, unique=True)
    user_id = fields.BigIntField()
    amount = fields.DecimalField(max_digits=10, decimal_places=2)
    # Разбивка суммы платежа:
    # - amount_external: реальная сумма (например, YooKassa)
    # - amount_from_balance: сумма списания с бонусного/реферального баланса
    amount_external = fields.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_from_balance = fields.DecimalField(max_digits=10, decimal_places=2, default=0)
    processed_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharField(max_length=50)  # succeeded, refunded, canceled
    
    class Meta:
        table = "processed_payments"


ProcessedPayment_Pydantic = pydantic_model_creator(ProcessedPayments, name="ProcessedPayment") 