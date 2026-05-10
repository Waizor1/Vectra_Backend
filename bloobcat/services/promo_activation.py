from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users

logger = logging.getLogger(__name__)

DEFAULT_PROMO_TARIFF_NAME = "Промо-активация"
DEFAULT_PROGRESSIVE_MULTIPLIER = 0.961629
DEFAULT_LTE_PRICE_PER_GB = 1.5


async def _resolve_base_tariff(months: int) -> Optional[Tariffs]:
    return (
        await Tariffs.filter(months=months, is_active=True)
        .order_by("order")
        .first()
    )


async def activate_trial_account(
    user: Users,
    *,
    effects: Optional[Dict[str, Any]] = None,
) -> Optional[ActiveTariffs]:
    """
    Создаёт синтетический ActiveTariffs для триал-пользователя, чтобы он мог
    пользоваться платными top-up LTE/устройств (PATCH /user/active_tariff требует
    наличие active_tariff_id). Идемпотентно: если у пользователя уже есть
    active_tariff_id, возвращает None и ничего не меняет.

    Параметры синтетического тарифа берутся из базового активного тарифа того же
    срока (по умолчанию 1 месяц), чтобы LTE/устройства top-up считались по
    рыночным ценам, а не уходили в 0.
    """
    if user.active_tariff_id:
        return None

    spec_raw: Any = (effects or {}).get("activate_account")
    if not spec_raw:
        return None
    spec: Dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}

    months = int(spec.get("months") or 1)
    if months < 1:
        months = 1

    hwid_limit = int(spec.get("hwid_limit") or (user.hwid_limit or 1))
    if hwid_limit < 1:
        hwid_limit = 1

    lte_gb_total_raw = spec.get("lte_gb_total")
    if lte_gb_total_raw is None:
        lte_gb_total = int(user.lte_gb_total or 0)
    else:
        lte_gb_total = int(lte_gb_total_raw)
    if lte_gb_total < 0:
        lte_gb_total = 0

    base = await _resolve_base_tariff(months)
    if base is not None:
        price = int(base.calculate_price(hwid_limit))
        progressive_multiplier = float(base.get_effective_pricing()[1])
        lte_price_per_gb = float(base.lte_price_per_gb or DEFAULT_LTE_PRICE_PER_GB)
    else:
        price = 0
        progressive_multiplier = DEFAULT_PROGRESSIVE_MULTIPLIER
        lte_price_per_gb = DEFAULT_LTE_PRICE_PER_GB

    name = str(spec.get("name") or DEFAULT_PROMO_TARIFF_NAME)

    active = await ActiveTariffs.create(
        user=user,
        name=name,
        months=months,
        price=price,
        hwid_limit=hwid_limit,
        lte_gb_total=lte_gb_total,
        lte_gb_used=0.0,
        lte_price_per_gb=lte_price_per_gb,
        progressive_multiplier=progressive_multiplier,
        residual_day_fraction=0.0,
    )

    user.active_tariff_id = active.id
    user.hwid_limit = hwid_limit
    user.lte_gb_total = lte_gb_total
    if user.is_trial:
        user.is_trial = False
    user.used_trial = True
    await user.save()

    logger.info(
        "Promo activated trial account: user=%s active_tariff=%s hwid_limit=%s lte_gb_total=%s price=%s lte_price_per_gb=%s",
        user.id,
        active.id,
        hwid_limit,
        lte_gb_total,
        price,
        lte_price_per_gb,
    )
    return active
