from tortoise import fields, models


class HwidDeviceLocal(models.Model):
    """
    Локальный справочник HWID устройств, увиденных в RemnaWave.
    Не чистим записи при удалении устройств на стороне панели.
    """

    id = fields.IntField(pk=True)
    hwid = fields.CharField(max_length=255)
    user_uuid = fields.UUIDField(null=True)
    telegram_user_id = fields.BigIntField(null=True)
    first_seen_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "hwid_devices_local"
        unique_together = (("hwid", "user_uuid"),)
        indexes = (("hwid",),)

    def __str__(self) -> str:  # pragma: no cover - для отладки
        return f"{self.hwid} -> {self.user_uuid}"
