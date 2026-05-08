from datetime import datetime, timezone
from typing import List

from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator

from bloobcat.logger import get_logger


logger = get_logger("prize_wheel_models")


class PrizeWheelHistory(models.Model):
    """История выигрышей на колесе призов"""
    id = fields.IntField(primary_key=True)
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
    id = fields.IntField(primary_key=True)
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
        unique_together = (("prize_type", "prize_value"),)

    @classmethod
    async def get_active_prizes(cls) -> List["PrizeWheelConfig"]:
        return await cls.filter(is_active=True).order_by("-probability")


# Pydantic модели для API
PrizeWheelHistory_Pydantic = pydantic_model_creator(PrizeWheelHistory, name="PrizeWheelHistory")
PrizeWheelConfig_Pydantic = pydantic_model_creator(PrizeWheelConfig, name="PrizeWheelConfig")



