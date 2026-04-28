from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class FamilyDevices(models.Model):
    id = fields.UUIDField(primary_key=True)
    user = fields.ForeignKeyField("models.Users", related_name="family_devices", on_delete=fields.CASCADE)
    client_id = fields.CharField(max_length=64, null=True)
    title = fields.CharField(max_length=100)
    subtitle = fields.CharField(max_length=200)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "family_devices"
        unique_together = ("user", "client_id")


FamilyDevices_Pydantic = pydantic_model_creator(FamilyDevices, name="FamilyDevices")
