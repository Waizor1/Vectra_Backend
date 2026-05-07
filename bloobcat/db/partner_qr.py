from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class PartnerQr(models.Model):
    id = fields.UUIDField(primary_key=True)
    owner = fields.ForeignKeyField("models.Users", related_name="partner_qr_codes", on_delete=fields.CASCADE)
    title = fields.CharField(max_length=120)
    slug = fields.CharField(max_length=64, null=True)
    link = fields.CharField(max_length=255, null=True)
    is_active = fields.BooleanField(default=True)
    views_count = fields.IntField(default=0)
    activations_count = fields.IntField(default=0)
    utm_source = fields.CharField(max_length=64, null=True)
    utm_medium = fields.CharField(max_length=64, null=True)
    utm_campaign = fields.CharField(max_length=120, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "partner_qr_codes"


PartnerQr_Pydantic = pydantic_model_creator(PartnerQr, name="PartnerQr")
