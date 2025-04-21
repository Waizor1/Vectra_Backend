from tortoise import fields, models # type: ignore
from tortoise.contrib.pydantic import pydantic_model_creator # type: ignore


class ProcessedPayments(models.Model):
    id = fields.IntField(pk=True)
    payment_id = fields.CharField(max_length=100, unique=True)
    user_id = fields.BigIntField()
    amount = fields.DecimalField(max_digits=10, decimal_places=2)
    processed_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharField(max_length=50)  # succeeded, refunded, canceled
    
    class Meta:
        table = "processed_payments"


ProcessedPayment_Pydantic = pydantic_model_creator(ProcessedPayments, name="ProcessedPayment") 