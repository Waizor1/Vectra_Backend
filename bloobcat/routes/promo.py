from __future__ import annotations

import hashlib
import hmac
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from bloobcat.db.promotions import PromoCode, PromoUsage
from bloobcat.funcs.validate import validate
from bloobcat.db.discounts import PersonalDiscount
from bloobcat.settings import promo_settings
from tortoise.transactions import atomic, in_transaction

router = APIRouter(prefix="/promo", tags=["promo"])


class PromoValidateRequest(BaseModel):
    code: str


class PromoValidateResponse(BaseModel):
    valid: bool
    reasons: list[str] = []
    effects: Optional[Dict[str, Any]] = None
    remaining_activations: Optional[int] = None
    per_user_remaining: Optional[int] = None
    expires_at: Optional[date] = None


class PromoRedeemResponse(BaseModel):
    success: bool
    reasons: list[str] = []
    effects: Optional[Dict[str, Any]] = None
    remaining_activations: Optional[int] = None
    per_user_remaining: Optional[int] = None
    expires_at: Optional[date] = None


async def _hash_code(raw_code: str) -> str:
    if not promo_settings.hmac_secret:
        # Конфигурация не задана — запрещаем использование до настройки
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Promo HMAC secret is not configured",
        )
    secret = promo_settings.hmac_secret.get_secret_value().encode()
    return hmac.new(secret, raw_code.encode(), hashlib.sha256).hexdigest()


@router.post("/validate", response_model=PromoValidateResponse)
async def validate_promo(req: PromoValidateRequest, user=Depends(validate)):
    code_hmac = await _hash_code(req.code)

    promo = await PromoCode.get_or_none(code_hmac=code_hmac)
    if not promo:
        return PromoValidateResponse(valid=False, reasons=["Промокод не найден"]) 

    reasons: list[str] = []

    if promo.disabled:
        reasons.append("Промокод отключен")

    if PromoCode.is_expired(promo.expires_at):
        reasons.append("Срок действия промокода истек")

    total_used = await PromoUsage.filter(promo_code=promo).count()
    remaining_total = max(0, promo.max_activations - total_used)
    if remaining_total <= 0:
        reasons.append("Лимит активаций исчерпан")

    user_used = await PromoUsage.filter(promo_code=promo, user_id=user.id).count()
    remaining_user = max(0, promo.per_user_limit - user_used)
    if remaining_user <= 0:
        reasons.append("Лимит использования на пользователя исчерпан")

    valid = len(reasons) == 0

    return PromoValidateResponse(
        valid=valid,
        reasons=reasons,
        effects=promo.effects if valid else None,
        remaining_activations=remaining_total,
        per_user_remaining=remaining_user,
        expires_at=promo.expires_at,
    )


@router.post("/redeem", response_model=PromoRedeemResponse)
async def redeem_promo(req: PromoValidateRequest, user=Depends(validate)):
    code_hmac = await _hash_code(req.code)

    # Обязательно внутри транзакции, так как используется select_for_update
    async with in_transaction():
        # Lock promo row to avoid race conditions on concurrent redemption attempts
        promo = await PromoCode.filter(code_hmac=code_hmac).select_for_update().first()
        if not promo:
            return PromoRedeemResponse(success=False, reasons=["Промокод не найден"]) 

        reasons: list[str] = []

        if promo.disabled:
            reasons.append("Промокод отключен")

        if PromoCode.is_expired(promo.expires_at):
            reasons.append("Срок действия промокода истек")

        # Пересчитываем использование в рамках транзакции
        total_used_before = await PromoUsage.filter(promo_code=promo).count()
        remaining_total = max(0, promo.max_activations - total_used_before)
        if remaining_total <= 0:
            reasons.append("Лимит активаций исчерпан")

        user_used_before = await PromoUsage.filter(promo_code=promo, user_id=user.id).count()
        remaining_user = max(0, promo.per_user_limit - user_used_before)
        if remaining_user <= 0:
            reasons.append("Лимит использования на пользователя исчерпан")

        if reasons:
            return PromoRedeemResponse(
                success=False,
                reasons=reasons,
                remaining_activations=remaining_total,
                per_user_remaining=remaining_user,
                expires_at=promo.expires_at,
            )

        # Фиксируем использование
        await PromoUsage.create(
            promo_code=promo,
            user_id=user.id,
            context={"source": "webapp", "action": "redeem"}
        )

        # Применяем эффекты (гибкая схема effects)
        effects: Dict[str, Any] = promo.effects or {}

        # 1) Продление подписки на N дней
        extend_days = effects.get("extend_days")
        if isinstance(extend_days, int) and extend_days > 0:
            await user.extend_subscription(extend_days)

        # 2) Увеличение лимита устройств (HWID)
        add_hwid = effects.get("add_hwid")
        if isinstance(add_hwid, int) and add_hwid > 0:
            current_limit = user.hwid_limit if user.hwid_limit is not None else 1
            user.hwid_limit = max(1, current_limit + add_hwid)
            await user.save()
            # Синхронизируем hwidDeviceLimit в RemnaWave (если есть UUID)
            if user.remnawave_uuid:
                try:
                    from bloobcat.routes.remnawave.client import RemnaWaveClient
                    from bloobcat.settings import remnawave_settings
                    remnawave_client = RemnaWaveClient(
                        remnawave_settings.url,
                        remnawave_settings.token.get_secret_value()
                    )
                    try:
                        await remnawave_client.users.update_user(
                            user.remnawave_uuid,
                            hwidDeviceLimit=user.hwid_limit
                        )
                    finally:
                        await remnawave_client.close()
                except Exception:
                    # Проглатываем ошибки синхронизации: бэкенд периодически делает батч-синк
                    pass

        # 3) Персональная скидка (discount_percent)
        discount_percent = effects.get("discount_percent")
        if isinstance(discount_percent, int) and discount_percent > 0:
            is_permanent = bool(effects.get("permanent") or effects.get("is_permanent") or False)
            remaining_uses = int(effects.get("uses") or (0 if is_permanent else 1))
            expires_at = effects.get("discount_expires_at") or effects.get("expires_at")

            await PersonalDiscount.create(
                user_id=user.id,
                percent=min(100, discount_percent),
                is_permanent=is_permanent,
                remaining_uses=max(0, remaining_uses),
                expires_at=expires_at,
                source="promo",
                metadata={"promo_id": promo.id}
            )

        # Обновляем остатки после записи
        total_used_after = await PromoUsage.filter(promo_code=promo).count()
        remaining_total_after = max(0, promo.max_activations - total_used_after)

        user_used_after = await PromoUsage.filter(promo_code=promo, user_id=user.id).count()
        remaining_user_after = max(0, promo.per_user_limit - user_used_after)

        return PromoRedeemResponse(
            success=True,
            reasons=[],
            effects=promo.effects,
            remaining_activations=remaining_total_after,
            per_user_remaining=remaining_user_after,
            expires_at=promo.expires_at,
        )