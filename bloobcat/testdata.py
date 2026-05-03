from __future__ import annotations

from datetime import date, timedelta
import hashlib
import hmac
from typing import Any, Dict

from bloobcat.db.tariff import Tariffs
from bloobcat.db.promotions import PromoBatch, PromoCode
from bloobcat.db.prize_wheel import PrizeWheelConfig
from bloobcat.logger import get_logger
from bloobcat.settings import promo_settings

logger = get_logger("testdata")


def _hash_code(raw_code: str) -> str:
    if not promo_settings.hmac_secret:
        raise RuntimeError("PROMO_HMAC_SECRET не задан — нельзя создать тестовые промокоды")
    secret = promo_settings.hmac_secret.get_secret_value().encode()
    return hmac.new(secret, raw_code.encode(), hashlib.sha256).hexdigest()


async def _upsert_tariff(data: Dict[str, Any]) -> None:
    obj = await Tariffs.get_or_none(name=data["name"], months=data["months"])
    if obj:
        updated = False
        for field in ("base_price", "progressive_multiplier", "order"):
            if getattr(obj, field) != data[field]:
                setattr(obj, field, data[field])
                updated = True
        if updated:
            await obj.save()
            logger.info(f"Обновлён тестовый тариф {data['name']} ({data['months']}м)")
    else:
        await Tariffs.create(**data)
        logger.info(f"Создан тестовый тариф {data['name']} ({data['months']}м)")


async def _upsert_prize(data: Dict[str, Any]) -> None:
    obj = await PrizeWheelConfig.get_or_none(prize_type=data["prize_type"], prize_value=data["prize_value"])
    if obj:
        changed = False
        for field in ("prize_name", "probability", "is_active", "requires_admin"):
            if getattr(obj, field) != data[field]:
                setattr(obj, field, data[field])
                changed = True
        if changed:
            await obj.save()
            logger.info(f"Обновлён приз колеса: {data['prize_type']} {data['prize_value']}")
    else:
        await PrizeWheelConfig.create(**data)
        logger.info(f"Создан приз колеса: {data['prize_type']} {data['prize_value']}")


async def _upsert_promo(batch: PromoBatch, raw_code: str, data: Dict[str, Any]) -> None:
    code_hmac = _hash_code(raw_code)
    obj = await PromoCode.get_or_none(code_hmac=code_hmac)
    payload = {
        "batch": batch,
        "name": data["name"],
        "effects": data["effects"],
        "max_activations": data.get("max_activations", 100),
        "per_user_limit": data.get("per_user_limit", 1),
        "expires_at": data.get("expires_at"),
        "disabled": data.get("disabled", False),
        "code_hmac": code_hmac,
    }
    if obj:
        changed = False
        for field, value in payload.items():
            if getattr(obj, field) != value:
                setattr(obj, field, value)
                changed = True
        if changed:
            await obj.save()
            logger.info(f"Обновлён тестовый промокод {data['name']}")
    else:
        await PromoCode.create(**payload)
        logger.info(f"Создан тестовый промокод {data['name']}")


async def seed_test_fixtures() -> None:
    """Идempotентное заполнение тестовых тарифов, промокодов и колеса призов при TESTMODE."""
    logger.info("TESTMODE=TRUE: подготавливаем тестовые данные")

    tariffs = [
        {"name": "Месяц", "months": 1, "base_price": 1000, "progressive_multiplier": 0.9, "order": 1},
        {"name": "Квартал", "months": 3, "base_price": 2700, "progressive_multiplier": 0.9, "order": 2},
        {"name": "Полгода", "months": 6, "base_price": 4800, "progressive_multiplier": 0.9, "order": 3},
        {"name": "Год", "months": 12, "base_price": 8400, "progressive_multiplier": 0.9, "order": 4},
    ]
    for t in tariffs:
        await _upsert_tariff(t)

    # Призы колеса: суммарная вероятность < 1
    prizes = [
        {"prize_type": "subscription", "prize_name": "Подписка 7 дней", "prize_value": "7", "probability": 0.05, "is_active": True, "requires_admin": False},
        {"prize_type": "extra_spin", "prize_name": "Еще одна попытка", "prize_value": "1", "probability": 0.1, "is_active": True, "requires_admin": False},
        {"prize_type": "discount_percent", "prize_name": "Скидка 15%", "prize_value": "15", "probability": 0.08, "is_active": True, "requires_admin": False},
        {"prize_type": "material_prize", "prize_name": "Футболка", "prize_value": "Размер M", "probability": 0.02, "is_active": True, "requires_admin": True},
    ]
    for p in prizes:
        await _upsert_prize(p)

    if not promo_settings.hmac_secret:
        logger.warning("PROMO_HMAC_SECRET не задан — промокоды для TESTMODE пропущены")
    else:
        batch, _ = await PromoBatch.get_or_create(title="QA minmax", defaults={"notes": "Автосоздание TESTMODE"})
        expires = date.today() + timedelta(days=365)
        promos = [
            {
                "raw_code": "QA20MIN3MAX12",
                "name": "20% 3-12m x2",
                "effects": {"discount_percent": 20, "uses": 2, "min_months": 3, "max_months": 12},
                "max_activations": 100,
                "per_user_limit": 1,
                "expires_at": expires,
            },
            {
                "raw_code": "QA30MAX6",
                "name": "30% max6",
                "effects": {"discount_percent": 30, "uses": 1, "max_months": 6},
                "max_activations": 50,
                "per_user_limit": 1,
                "expires_at": expires,
            },
            {
                "raw_code": "QA15MIN6",
                "name": "15% from6",
                "effects": {"discount_percent": 15, "uses": 3, "min_months": 6},
                "max_activations": 200,
                "per_user_limit": 2,
                "expires_at": expires,
            },
            {
                "raw_code": "QA10ANY",
                "name": "10% any",
                "effects": {"discount_percent": 10, "uses": 1},
                "max_activations": 500,
                "per_user_limit": 2,
                "expires_at": expires,
            },
        ]

        for p in promos:
            await _upsert_promo(batch, p["raw_code"], p)

    logger.info("Тестовые данные готовы (тарифы, промокоды, колесо)")

