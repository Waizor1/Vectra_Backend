from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Dict, Any, Tuple

from tortoise import fields, models


class PersonalDiscount(models.Model):
    """
    Персональная скидка пользователя.
    - percent: размер скидки в процентах (1..100)
    - is_permanent: постоянная скидка (без списывания оставшихся использований)
    - remaining_uses: сколько применений осталось (для разовых/многоразовых)
    - expires_at: дата истечения действия скидки
    - source: источник (promo|prize_wheel|admin|other)
    - metadata: произвольные данные об источнике
    """

    id = fields.IntField(pk=True)
    user_id = fields.BigIntField(index=True)
    percent = fields.IntField()
    is_permanent = fields.BooleanField(default=False)
    remaining_uses = fields.IntField(default=0)
    expires_at = fields.DateField(null=True)
    source = fields.CharField(max_length=64, null=True)
    metadata: Dict[str, Any] = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "personal_discounts"
        indexes = ("user_id",)

    @staticmethod
    def _is_active_row(percent: int, is_permanent: bool, remaining_uses: int, expires_at: Optional[date]) -> bool:
        if not isinstance(percent, int) or percent <= 0:
            return False
        if expires_at and expires_at < date.today():
            return False
        if is_permanent:
            return True
        return remaining_uses > 0

    @classmethod
    async def get_best_active_for_user(cls, user_id: int) -> Optional[Tuple["PersonalDiscount", int]]:
        """Возвращает (скидка, процент) с максимальным percent среди активных."""
        candidates = await cls.filter(user_id=user_id).all()
        best: Optional[PersonalDiscount] = None
        best_percent = 0
        for c in candidates:
            if cls._is_active_row(c.percent, c.is_permanent, int(c.remaining_uses or 0), c.expires_at):
                if int(c.percent) > best_percent:
                    best = c
                    best_percent = int(c.percent)
        return (best, best_percent) if best else None

    async def consume_one(self) -> None:
        """Списывает одно использование для не-постоянной скидки."""
        if self.is_permanent:
            return
        uses_left = int(self.remaining_uses or 0)
        if uses_left > 0:
            self.remaining_uses = uses_left - 1
            await self.save()


"""
Админские классы намеренно удалены по требованию: эти модели не отображаются в FastAdmin.
"""


