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
    is_promo_synthetic = fields.BooleanField(
        default=False,
        description=(
            "True если строка синтезирована эффектом activate_account промокода. "
            "Используется анти-твинком: такие пользователи не считаются 'paid' для целей "
            "пропуска HWID-санкций — фактически это расширенный триал."
        ),
    )

    class Meta:
        table = "active_tariffs"
