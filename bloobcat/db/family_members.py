from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class FamilyMembers(models.Model):
    id = fields.UUIDField(primary_key=True)
    owner = fields.ForeignKeyField("models.Users", related_name="family_members_owner", on_delete=fields.CASCADE)
    member = fields.ForeignKeyField("models.Users", related_name="family_members_member", on_delete=fields.CASCADE)
    allocated_devices = fields.IntField(default=1)
    status = fields.CharField(max_length=24, default="active")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    # Кэш crypt5 happ://-линка для участника семьи (если ему отдаётся отдельная
    # подписочная ссылка). Refresh не чаще чем раз в TTL.
    happ_cryptolink_v5 = fields.TextField(null=True)
    happ_cryptolink_v5_at = fields.DatetimeField(null=True)

    class Meta:
        table = "family_members"
        unique_together = ("owner", "member")


FamilyMembers_Pydantic = pydantic_model_creator(FamilyMembers, name="FamilyMembers")
