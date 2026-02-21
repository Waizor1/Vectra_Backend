from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class PartnerWithdrawals(models.Model):
    id = fields.UUIDField(pk=True)
    owner = fields.ForeignKeyField("models.Users", related_name="partner_withdrawals", on_delete=fields.CASCADE)
    amount_rub = fields.IntField()
    method = fields.CharField(max_length=16)
    details = fields.CharField(max_length=255, null=True)
    status = fields.CharField(max_length=24, default="created")
    paid_amount_rub = fields.IntField(null=True)
    error = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "partner_withdrawals"


PartnerWithdrawals_Pydantic = pydantic_model_creator(PartnerWithdrawals, name="PartnerWithdrawals")
