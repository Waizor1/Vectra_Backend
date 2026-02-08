from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class FamilyInvites(models.Model):
    id = fields.UUIDField(pk=True)
    owner = fields.ForeignKeyField("models.Users", related_name="family_invites", on_delete=fields.CASCADE)
    allocated_devices = fields.IntField(default=1)
    token_hash = fields.CharField(max_length=128, unique=True, index=True)
    expires_at = fields.DatetimeField(null=True)
    max_uses = fields.IntField(default=1)
    used_count = fields.IntField(default=0)
    used_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "family_invites"


FamilyInvites_Pydantic = pydantic_model_creator(FamilyInvites, name="FamilyInvites")
