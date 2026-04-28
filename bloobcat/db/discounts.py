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
    - source: источник (promo|prize_wheel|admin|other|winback)
    - metadata: произвольные данные об источнике
    - min_months/max_months: ограничения по длительности тарифа (если указаны)
    """

    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField(db_index=True)
    percent = fields.IntField()
    is_permanent = fields.BooleanField(default=False)
    remaining_uses = fields.IntField(default=0)
    expires_at = fields.DateField(null=True)
    source = fields.CharField(max_length=64, null=True)
    metadata: Dict[str, Any] = fields.JSONField(default=dict)
    min_months = fields.IntField(null=True)
    max_months = fields.IntField(null=True)
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

    @staticmethod
    def _matches_months(months: Optional[int], min_months: Optional[int], max_months: Optional[int]) -> bool:
        """Проверяет, подходит ли скидка под ограничения по длительности тарифа."""
        if months is None:
            return True
        if min_months is not None and months < int(min_months):
            return False
        if max_months is not None and months > int(max_months):
            return False
        return True

    @staticmethod
    def _source_priority(source: Optional[str]) -> int:
        """Используется как тай-брейкер: больший приоритет выигрывает."""
        if not source:
            return 2
        src = str(source).lower()
        if src in {"promo", "prize_wheel"}:
            return 3
        if src in {"winback"}:
            return 1
        return 2  # admin/other/default

    @classmethod
    async def get_best_active_for_user(
        cls, user_id: int, months: Optional[int] = None
    ) -> Optional[Tuple["PersonalDiscount", int]]:
        """Возвращает (скидка, процент) среди активных записей с учётом min/max месяцев."""
        candidates = await cls.filter(user_id=user_id).all()
        best: Optional[PersonalDiscount] = None
        best_percent = 0
        best_prio = -1
        for c in candidates:
            if cls._is_active_row(c.percent, c.is_permanent, int(c.remaining_uses or 0), c.expires_at) and cls._matches_months(
                months, getattr(c, "min_months", None), getattr(c, "max_months", None)
            ):
                prio = cls._source_priority(c.source)
                pct = int(c.percent)
                if best is None or pct > best_percent or (pct == best_percent and prio > best_prio):
                    best = c
                    best_percent = pct
                    best_prio = prio
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
