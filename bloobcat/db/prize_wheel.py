from datetime import datetime, timezone
from typing import List

from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator
from fastadmin import TortoiseModelAdmin, register

from bloobcat.logger import get_logger


logger = get_logger("prize_wheel_models")


class PrizeWheelHistory(models.Model):
    """История выигрышей на колесе призов"""
    id = fields.IntField(pk=True)
    user_id = fields.BigIntField(description="ID пользователя")
    prize_type = fields.CharField(max_length=64, description="Тип выигранного приза (строка)")
    prize_name = fields.CharField(max_length=255, description="Название приза")
    prize_value = fields.CharField(max_length=255, description="Значение приза (например, количество дней)")
    is_claimed = fields.BooleanField(default=False, description="Был ли приз получен")
    claimed_at = fields.DatetimeField(null=True, description="Время получения приза")
    is_rejected = fields.BooleanField(default=False, description="Был ли приз отклонен админом")
    rejected_at = fields.DatetimeField(null=True, description="Время отклонения приза")
    admin_notified = fields.BooleanField(default=False, description="Уведомлен ли админ о призе")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "prize_wheel_history"

    async def mark_as_claimed(self) -> None:
        self.is_claimed = True
        self.claimed_at = datetime.now(timezone.utc)
        await self.save()
        logger.info(f"Приз {self.prize_type} для пользователя {self.user_id} отмечен как полученный")

    async def mark_admin_notified(self) -> None:
        self.admin_notified = True
        await self.save()
        logger.info(f"Админ уведомлен о призе {self.prize_type} для пользователя {self.user_id}")

    async def mark_as_rejected(self) -> None:
        self.is_rejected = True
        self.rejected_at = datetime.now(timezone.utc)
        await self.save()
        logger.info(
            f"Приз {self.prize_type} для пользователя {self.user_id} отмечен как отклоненный"
        )


class PrizeWheelConfig(models.Model):
    """Конфигурация колеса призов"""
    id = fields.IntField(pk=True)
    prize_type = fields.CharField(max_length=64, description="Тип приза: subscription | extra_spin | material_prize")
    prize_name = fields.CharField(max_length=255, description="Название приза")
    prize_value = fields.CharField(max_length=255, description="Значение приза")
    probability = fields.FloatField(description="Вероятность выпадения (от 0 до 1)")
    is_active = fields.BooleanField(default=True, description="Активен ли приз")
    requires_admin = fields.BooleanField(default=False, description="Требует ли приз участия админа")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "prize_wheel_config"
        unique_together = (("prize_type",),)

    @classmethod
    async def get_active_prizes(cls) -> List["PrizeWheelConfig"]:
        return await cls.filter(is_active=True).order_by("-probability")


# Pydantic модели для API
PrizeWheelHistory_Pydantic = pydantic_model_creator(PrizeWheelHistory, name="PrizeWheelHistory")
PrizeWheelConfig_Pydantic = pydantic_model_creator(PrizeWheelConfig, name="PrizeWheelConfig")


@register(PrizeWheelHistory)
class PrizeWheelHistoryModelAdmin(TortoiseModelAdmin):
    search_fields = ("user_id", "prize_name", "prize_type")
    list_display = (
        "id",
        "user_id",
        "prize_name",
        "prize_value",
        "is_claimed",
        "claimed_at",
        "is_rejected",
        "rejected_at",
        "admin_notified",
        "created_at",
    )
    readonly_fields = (
        "id",
        "created_at",
    )
    fields = (
        "user_id",
        "prize_type",
        "prize_name",
        "prize_value",
        "is_claimed",
        "claimed_at",
        "is_rejected",
        "rejected_at",
        "admin_notified",
        "created_at",
    )
    search_help_text = "ID пользователя, название приза, тип приза"
    verbose_name = "История колеса призов"
    verbose_name_plural = "История колеса призов"


@register(PrizeWheelConfig)
class PrizeWheelConfigModelAdmin(TortoiseModelAdmin):
    search_fields = ("prize_name", "prize_type")
    list_display = (
        "id",
        "prize_type",
        "prize_name",
        "prize_value",
        "probability",
        "is_active",
        "requires_admin",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )
    fields = (
        "prize_type",
        "prize_name",
        "prize_value",
        "probability",
        "is_active",
        "requires_admin",
        "created_at",
        "updated_at",
    )
    search_help_text = "Название приза, тип приза"
    verbose_name = "Конфигурация призов"
    verbose_name_plural = "Конфигурация призов"

    async def save_model(self, pk, form_data=None):
        """Валидируем, что сумма вероятностей активных призов не превышает 100%.
        Разрешаем диапазон [0, 1] для каждого приза.
        """
        # Текущие значения (если редактирование)
        current_obj = None
        if pk:
            current_obj = await PrizeWheelConfig.get_or_none(id=pk)

        # Извлекаем новые значения из формы или берем текущие
        def _bool(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return False
            s = str(v).strip().lower()
            return s in {"1", "true", "yes", "on"}

        try:
            new_prob = form_data.get("probability") if form_data else None
            new_prob_f = float(new_prob) if new_prob is not None else (current_obj.probability if current_obj else 0.0)
        except Exception:
            new_prob_f = 0.0

        new_active_v = form_data.get("is_active") if form_data else None
        new_active = _bool(new_active_v) if new_active_v is not None else (current_obj.is_active if current_obj else True)

        # Границы одной вероятности
        if new_prob_f < 0.0 or new_prob_f > 1.0:
            raise ValueError("Вероятность приза должна быть в диапазоне от 0 до 1")

        # Бизнес-валидации по типам
        new_type = (form_data.get("prize_type") if form_data else None) or (current_obj.prize_type if current_obj else "")
        new_value = (form_data.get("prize_value") if form_data else None) or (current_obj.prize_value if current_obj else "")
        if str(new_type).strip() == "subscription":
            try:
                days = int(str(new_value).strip())
                if days <= 0:
                    raise ValueError
            except Exception:
                raise ValueError("Для типа 'subscription' поле 'prize_value' должно быть целым числом дней (> 0)")

        # Сумма остальных активных призов
        others = await PrizeWheelConfig.filter(is_active=True).all()
        others_sum = 0.0
        for it in others:
            if pk and it.id == pk:
                continue
            try:
                others_sum += float(it.probability or 0.0)
            except Exception:
                pass

        candidate_total = others_sum + (new_prob_f if new_active else 0.0)
        if candidate_total > 1.0 + 1e-9:
            raise ValueError(
                f"Сумма вероятностей активных призов превышает 100%: {candidate_total:.4f}. Уменьшите значения."
            )

        # Все ок — сохраняем
        return await super().save_model(pk, form_data)


