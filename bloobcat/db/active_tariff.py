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
    lte_gb_total = fields.IntField(default=0, description="LTE трафик (GB), купленный на период")
    lte_gb_used = fields.FloatField(default=0.0, description="LTE трафик (GB), использованный на период")
    lte_price_per_gb = fields.FloatField(default=0.0, description="Снапшот цены за 1 GB LTE трафика")
    lte_autopay_free = fields.BooleanField(default=False, description="Не включать LTE стоимость в автоплатеж")
    lte_usage_last_date = fields.DateField(null=True, description="Дата последнего учтенного LTE трафика")
    lte_usage_last_total_gb = fields.FloatField(default=0.0, description="Учтенный LTE трафик за последнюю дату (GB)")
    devices_decrease_count = fields.IntField(
        default=0,
        description="Сколько раз пользователь уменьшал лимит устройств в текущем периоде",
    )
    progressive_multiplier = fields.FloatField(null=True, description="Снапшот множителя прогрессивной скидки")
    residual_day_fraction = fields.FloatField(null=True, description="Накопленная дробная часть дней при конвертациях")

    class Meta:
        table = "active_tariffs"


@register(ActiveTariffs)
class ActiveTariffsModelAdmin(TortoiseModelAdmin):
    list_display = (
        "id",
        "user",
        "name",
        "months",
        "price",
        "hwid_limit",
        "lte_gb_total",
        "lte_gb_used",
        "lte_price_per_gb",
        "lte_autopay_free",
        "progressive_multiplier",
        "residual_day_fraction",
        "devices_decrease_count",
    )
    list_editable = (
        "lte_gb_total",
    )
    readonly_fields = (
        "id",
        "user",
        "name",
        "months",
        "price",
        "hwid_limit",
        "lte_gb_used",
        "lte_price_per_gb",
        "lte_autopay_free",
        "lte_usage_last_date",
        "lte_usage_last_total_gb",
        "progressive_multiplier",
        "residual_day_fraction",
        "devices_decrease_count",
    ) # Make fields read-only in admin
    verbose_name = "Активный Тариф"
    verbose_name_plural = "Активные Тарифы"

    async def save_model(self, pk, form_data=None):
        from bloobcat.db.notifications import NotificationMarks
        from bloobcat.logger import get_logger
        from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status

        logger = get_logger("active_tariff_admin")
        original_obj = None
        original_lte_total = None
        original_lte_used = None

        if pk:
            original_obj = await ActiveTariffs.get_or_none(id=pk)
            if original_obj:
                original_lte_total = original_obj.lte_gb_total
                original_lte_used = original_obj.lte_gb_used

        result = await super().save_model(pk, form_data)

        obj = await ActiveTariffs.get_or_none(id=pk)
        if not obj or not obj.user_id:
            return result

        if original_obj is not None and obj.lte_gb_total != original_lte_total:
            try:
                user = await obj.user
                effective_lte_total = (
                    user.lte_gb_total
                    if user and user.lte_gb_total is not None
                    else (obj.lte_gb_total or 0)
                )
                should_enable = float(effective_lte_total or 0) > float(obj.lte_gb_used or 0)
                if user and user.remnawave_uuid:
                    await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable)
                await NotificationMarks.filter(user_id=obj.user_id, type="lte_usage").delete()
                logger.info(
                    "Admin updated lte_gb_total: tariff=%s user=%s total=%s used=%s",
                    obj.id,
                    obj.user_id,
                    obj.lte_gb_total,
                    obj.lte_gb_used,
                )
            except Exception as e:
                logger.error(
                    "Admin LTE update failed: tariff=%s user=%s error=%s",
                    obj.id,
                    obj.user_id,
                    e,
                )

        if original_obj is not None and obj.lte_gb_used != original_lte_used:
            await NotificationMarks.filter(user_id=obj.user_id, type="lte_usage").delete()

        return result
