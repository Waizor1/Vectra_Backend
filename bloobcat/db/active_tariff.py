from fastadmin import TortoiseModelAdmin, register
from tortoise import fields, models
import random

def generate_random_id():
    """
    Generates a random 5-digit string for ActiveTariffs ID.
    """
    return ''.join(str(random.randint(0,9)) for _ in range(5))


class ActiveTariffs(models.Model):
    """
    Stores a snapshot of tariff details that users are actively subscribed to.
    Each record has a random five-digit ID.
    """
    id = fields.CharField(max_length=5, primary_key=True, default=generate_random_id, description="5-digit generated ActiveTariff ID")
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users", related_name="active_tariffs", description="Subscribed user", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=100)
    months = fields.IntField()
    price = fields.IntField()
    hwid_limit = fields.IntField(default=1, description="Лимит количества устройств")

    class Meta:
        table = "active_tariffs"


@register(ActiveTariffs)
class ActiveTariffsModelAdmin(TortoiseModelAdmin):
    list_display = ("id", "user", "name", "months", "price", "hwid_limit")
    readonly_fields = ("id", "user", "name", "months", "price", "hwid_limit") # Make fields read-only in admin
    verbose_name = "Активный Тариф"
    verbose_name_plural = "Активные Тарифы" 