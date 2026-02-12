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
    family_plan_enabled = fields.BooleanField(default=True, description="Показывать семейный вариант плана для этого тарифа")
    final_price_default = fields.IntField(null=True, description="Финальная цена карточки для devices_limit_default устройств. Если заполнено, используется как источник цены")
    final_price_family = fields.IntField(null=True, description="Финальная цена семейной карточки для devices_limit_family устройств (обычно для 12 месяцев)")
    lte_enabled = fields.BooleanField(default=False, description="Доступ к LTE серверам")
    lte_price_per_gb = fields.FloatField(default=0.0, description="Цена за 1 GB LTE трафика")

    @staticmethod
    def _sanitize_multiplier(value: float) -> float:
        # Keep multiplier in a stable range for pricing math.
        return max(0.1, min(0.9999, float(value)))

    @staticmethod
    def _geometric_sum(multiplier: float, device_count: int) -> float:
        if device_count <= 0:
            return 0.0
        m = Tariffs._sanitize_multiplier(multiplier)
        if abs(1.0 - m) < 1e-9:
            return float(device_count)
        return (1.0 - (m ** device_count)) / (1.0 - m)

    @staticmethod
    def _solve_multiplier_from_totals(
        target_default: int,
        default_devices: int,
        target_family: int,
        family_devices: int,
    ) -> float | None:
        if target_default <= 0 or target_family <= 0:
            return None
        if default_devices <= 0 or family_devices <= default_devices:
            return None

        ratio = float(target_family) / float(target_default)
        lo = 0.1
        hi = 0.9999
        for _ in range(80):
            mid = (lo + hi) / 2.0
            s_default = Tariffs._geometric_sum(mid, default_devices)
            s_family = Tariffs._geometric_sum(mid, family_devices)
            if s_default <= 0:
                return None
            current_ratio = s_family / s_default
            if current_ratio > ratio:
                hi = mid
            else:
                lo = mid
        return Tariffs._sanitize_multiplier((lo + hi) / 2.0)

    @staticmethod
    def _calculate_with_params(base_price: int, multiplier: float, device_count: int) -> int:
        if device_count <= 0:
            return 0
        if device_count == 1:
            return int(base_price)
        total = float(base_price)
        for device_num in range(2, device_count + 1):
            total += float(base_price) * (multiplier ** (device_num - 1))
        return int(round(total))

    def get_effective_pricing(self) -> tuple[int, float]:
        """
        Возвращает эффективные base_price и progressive_multiplier.
        Если в админке указаны финальные цены карточек, параметры пересчитываются автоматически.
        """
        base_price = max(1, int(self.base_price or 1))
        multiplier = self._sanitize_multiplier(float(self.progressive_multiplier or 0.9))

        target_default = int(self.final_price_default or 0)
        if target_default <= 0:
            return base_price, multiplier

        default_devices = max(1, int(self.devices_limit_default or 1))
        family_devices = max(default_devices, int(self.devices_limit_family or default_devices))

        target_family = int(self.final_price_family or 0)
        can_use_family_target = (
            bool(getattr(self, "family_plan_enabled", True))
            and family_devices > default_devices
            and target_family > target_default
        )

        if can_use_family_target:
            solved_multiplier = self._solve_multiplier_from_totals(
                target_default=target_default,
                default_devices=default_devices,
                target_family=target_family,
                family_devices=family_devices,
            )
            if solved_multiplier is not None:
                multiplier = solved_multiplier

        if can_use_family_target:
            best_base = base_price
            best_multiplier = multiplier
            best_error = None
            for step in range(-200, 201):
                candidate_multiplier = self._sanitize_multiplier(multiplier + (step * 0.000025))
                default_sum = self._geometric_sum(candidate_multiplier, default_devices)
                if default_sum <= 0:
                    continue
                base_estimate = float(target_default) / default_sum
                base_candidates = {
                    max(1, int(round(base_estimate))),
                    max(1, int(base_estimate)),
                    max(1, int(base_estimate) + 1),
                }
                for candidate_base in base_candidates:
                    got_default = self._calculate_with_params(candidate_base, candidate_multiplier, default_devices)
                    got_family = self._calculate_with_params(candidate_base, candidate_multiplier, family_devices)
                    error = abs(got_default - target_default) + abs(got_family - target_family)
                    if best_error is None or error < best_error:
                        best_error = error
                        best_base = candidate_base
                        best_multiplier = candidate_multiplier
                        if error == 0:
                            return best_base, best_multiplier
            return best_base, best_multiplier

        default_sum = self._geometric_sum(multiplier, default_devices)
        if default_sum <= 0:
            return base_price, multiplier

        base_price = max(1, int(round(float(target_default) / default_sum)))
        return base_price, multiplier

    async def sync_effective_pricing_fields(self) -> tuple[int, float]:
        """
        Синхронизирует служебные поля base_price/progressive_multiplier в БД
        с эффективными значениями, рассчитанными из финальных цен карточек.
        """
        effective_base, effective_multiplier = self.get_effective_pricing()
        needs_base_update = int(self.base_price or 0) != int(effective_base)
        needs_multiplier_update = abs(float(self.progressive_multiplier or 0.0) - float(effective_multiplier)) > 1e-6
        if self.id and (needs_base_update or needs_multiplier_update):
            await Tariffs.filter(id=self.id).update(
                base_price=int(effective_base),
                progressive_multiplier=float(effective_multiplier),
            )
            self.base_price = int(effective_base)
            self.progressive_multiplier = float(effective_multiplier)
        return effective_base, effective_multiplier

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

        effective_base_price, effective_multiplier = self.get_effective_pricing()

        if device_count == 1:
            return effective_base_price

        total_price = effective_base_price  # Первое устройство по полной цене
        
        # Каждое следующее устройство дешевле предыдущего
        for device_num in range(2, device_count + 1):
            device_price = effective_base_price * (effective_multiplier ** (device_num - 1))
            total_price += device_price
        
        # Use rounding to keep UI/admin and payment calculations consistent.
        return int(round(total_price))

    def get_device_savings_info(self, device_count: int = 1) -> dict:
        """
        Возвращает информацию об экономии при покупке нескольких устройств
        """
        if device_count <= 1:
            effective_base_price, _ = self.get_effective_pricing()
            return {
                "total_price": effective_base_price,
                "average_per_device": effective_base_price,
                "total_savings": 0,
                "savings_percentage": 0
            }
        
        actual_price = self.calculate_price(device_count)
        effective_base_price, _ = self.get_effective_pricing()
        full_price_total = effective_base_price * device_count
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
        return self.calculate_price(1)


Tariffs_Pydantic = pydantic_model_creator(Tariffs, name="Tariffs")


@register(Tariffs)
class UsersModelAdmin(TortoiseModelAdmin):
    list_display = (
        "order",
        "name",
        "months",
        "is_active",
        "family_plan_enabled",
        "final_price_default",
        "final_price_family",
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
        "family_plan_enabled",
        "final_price_default",
        "final_price_family",
        "base_price",
        "progressive_multiplier",
        "devices_limit_default",
        "devices_limit_family",
        "lte_enabled",
        "lte_price_per_gb",
    )
    ordering = ("order",)
    verbose_name = "Тарифы"
    verbose_name_plural = "Тарифы"
