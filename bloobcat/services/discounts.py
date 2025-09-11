from __future__ import annotations

from typing import Optional, Tuple

from bloobcat.db.discounts import PersonalDiscount


def _apply_percent_to_price(base_price: int, percent: int) -> int:
    if percent <= 0:
        return max(0, int(base_price))
    discount_value = int(round(base_price * (percent / 100.0)))
    final_price = max(0, int(base_price) - discount_value)
    return final_price


async def get_best_discount(user_id: int) -> Optional[Tuple[PersonalDiscount, int]]:
    return await PersonalDiscount.get_best_active_for_user(user_id)


    


async def apply_personal_discount(user_id: int, base_price: int) -> tuple[int, Optional[int], int]:
    """
    Возвращает (финальная_цена, discount_id, percent).
    Если активной скидки нет — возвращает исходную цену, None, 0.
    """
    best = await get_best_discount(user_id)
    if not best:
        return int(base_price), None, 0
    discount_obj, percent = best
    final_price = _apply_percent_to_price(int(base_price), int(percent))
    return final_price, int(discount_obj.id), int(percent)


async def consume_discount_if_needed(discount_id: Optional[int]) -> bool:
    """Пытается списать разовую скидку. Возвращает True, если списание произошло."""
    if not discount_id:
        return False
    obj = await PersonalDiscount.get_or_none(id=discount_id)
    if not obj:
        return False
    # Для постоянных скидок фактического списания нет, но скидка валидна для платежа
    if bool(obj.is_permanent):
        return True
    before = int(obj.remaining_uses or 0)
    await obj.consume_one()
    after_obj = await PersonalDiscount.get_or_none(id=discount_id)
    after = int(after_obj.remaining_uses or 0) if after_obj else before
    return after < before


    


