"""Local device inventory for the optional device-per-user scheme."""

from __future__ import annotations

from enum import Enum

from tortoise import fields, models


class DeviceKind(str, Enum):
    LEGACY_HWID = "legacy_hwid"
    DEVICE_USER = "device_user"


class UserDevice(models.Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        "models.Users",
        related_name="user_devices",
        on_delete=fields.CASCADE,
        description="Subscription owner",
    )
    family_member = fields.ForeignKeyField(
        "models.FamilyMembers",
        related_name="user_devices",
        null=True,
        on_delete=fields.CASCADE,
        description="Family membership that owns this device slot, null for owner device",
    )
    kind = fields.CharEnumField(DeviceKind, max_length=16)
    remnawave_uuid = fields.UUIDField(null=True)
    hwid = fields.CharField(max_length=255, null=True)
    device_name = fields.CharField(max_length=128, null=True)
    platform = fields.CharField(max_length=64, null=True)
    device_model = fields.CharField(max_length=128, null=True)
    os_version = fields.CharField(max_length=64, null=True)
    metadata = fields.JSONField(null=True)
    meta_refreshed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    last_online_at = fields.DatetimeField(null=True)

    class Meta:
        table = "user_devices"
        indexes = (("user",), ("family_member",), ("remnawave_uuid",), ("hwid",))

    def __str__(self) -> str:  # pragma: no cover
        return f"UserDevice(id={self.id}, kind={self.kind}, hwid={self.hwid})"
