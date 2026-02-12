from fastadmin import TortoiseModelAdmin, register
from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class Tariffs(models.Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=100)
    months = fields.IntField()
    base_price = fields.IntField(description="Базовая цена за 1 устройство")
    progressive_multiplier = fields.FloatField(default=0.9, description="Множитель прогрессивной скидки (0.1-1.0). Чем меньше, тем больше скидка на каждое следующее устройство")
    order = fields.IntField(default=3, description="Порядок отображения тарифа")
    is_active = fields.BooleanField(default=True, description="Активен ли тариф для новых покупок")
    devices_limit_default = fields.IntField(default=3, description="Лимит устройств для основного плана тарифа")
    devices_limit_family = fields.IntField(default=10, description="Лимит устройств для семейного плана (используется для 12 месяцев)")
    lte_enabled = fields.BooleanField(default=False, description="Доступ к LTE серверам")
    lte_price_per_gb = fields.FloatField(default=0.0, description="Цена за 1 GB LTE трафика")

    # Метод для расчета итоговой цены тарифа на основе количества устройств с прогрессивной скидкой
    def calculate_price(self, device_count: int = 1) -> int:
        """
        Рассчитывает цену тарифа для указанного количества устройств с прогрессивной скидкой.
        Формула: базовая_цена + сумма(базовая_цена * (множитель ^ номер_устройства)) для каждого дополнительного устройства
        
        Примеры с base_price=100, progressive_multiplier=0.9:
        - 1 устройство: 100₽
        - 2 устройства: 100₽ + 100₽*0.9 = 190₽ (среднее 95₽ за устройство)
        - 3 устройства: 100₽ + 100₽*0.9 + 100₽*0.81 = 271₽ (среднее 90₽ за устройство)
        - 5 устройств: 100₽ + 90₽ + 81₽ + 73₽ + 66₽ = 410₽ (среднее 82₽ за устройство)
        """
        if device_count <= 0:
            return 0
        
        if device_count == 1:
            return self.base_price
        
        total_price = self.base_price  # Первое устройство по полной цене
        
        # Каждое следующее устройство дешевле предыдущего
        for device_num in range(2, device_count + 1):
            device_price = self.base_price * (self.progressive_multiplier ** (device_num - 1))
            total_price += device_price
        
        # Use rounding to keep UI/admin and payment calculations consistent.
        return int(round(total_price))

    def get_device_savings_info(self, device_count: int = 1) -> dict:
        """
        Возвращает информацию об экономии при покупке нескольких устройств
        """
        if device_count <= 1:
            return {
                "total_price": self.base_price,
                "average_per_device": self.base_price,
                "total_savings": 0,
                "savings_percentage": 0
            }
        
        actual_price = self.calculate_price(device_count)
        full_price_total = self.base_price * device_count
        savings = full_price_total - actual_price
        average_per_device = actual_price / device_count
        savings_percentage = (savings / full_price_total) * 100
        
        return {
            "total_price": actual_price,
            "average_per_device": int(average_per_device),
            "total_savings": savings,
            "savings_percentage": round(savings_percentage, 1),
            "full_price_total": full_price_total
        }

    # Совместимость со старым API - поле price теперь рассчитывается
    @property 
    def price(self) -> int:
        """Совместимость со старым API - возвращает цену для 1 устройства"""
        return self.base_price


Tariffs_Pydantic = pydantic_model_creator(Tariffs, name="Tariffs")


@register(Tariffs)
class UsersModelAdmin(TortoiseModelAdmin):
    list_display = (
        "order",
        "name",
        "months",
        "is_active",
        "base_price",
        "progressive_multiplier",
        "devices_limit_default",
        "devices_limit_family",
        "lte_enabled",
        "lte_price_per_gb",
    )
    list_editable = (
        "order",
        "is_active",
        "progressive_multiplier",
        "devices_limit_default",
        "devices_limit_family",
        "lte_enabled",
        "lte_price_per_gb",
    )
    ordering = ("order",)
    verbose_name = "Тарифы"
    verbose_name_plural = "Тарифы"
