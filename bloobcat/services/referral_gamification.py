from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from tortoise.expressions import F, Q
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.referral_rewards import (
    ReferralCashbackRewards,
    ReferralLevelRewards,
    ReferralRewards,
)
from bloobcat.db.users import Users
from bloobcat.funcs.referral_attribution import build_referral_link, is_partner_source_utm
from bloobcat.logger import get_logger

logger = get_logger("services.referral_gamification")

ReferralLevelKey = str


@dataclass(frozen=True)
class ReferralLevel:
    key: ReferralLevelKey
    name: str
    threshold: int
    cashback_percent: int
    chest_reward_label: str


REFERRAL_LEVELS: tuple[ReferralLevel, ...] = (
    ReferralLevel("bronze", "Бронза", 0, 20, "50 ₽ или скидка 10%"),
    ReferralLevel("silver", "Серебро", 1, 25, "100 ₽ или скидка 15%"),
    ReferralLevel("gold", "Золото", 3, 30, "200 ₽ или скидка 20%"),
    ReferralLevel("platinum", "Платина", 6, 35, "300 ₽ или скидка 25%"),
    ReferralLevel("diamond", "Алмаз", 9, 40, "500 ₽ или скидка 30%"),
)

CHEST_REWARD_BY_LEVEL: dict[str, dict[str, int]] = {
    "bronze": {"balance": 50, "discount_percent": 10},
    "silver": {"balance": 100, "discount_percent": 15},
    "gold": {"balance": 200, "discount_percent": 20},
    "platinum": {"balance": 300, "discount_percent": 25},
    "diamond": {"balance": 500, "discount_percent": 30},
}

LEVEL_INDEX_BY_KEY = {level.key: idx for idx, level in enumerate(REFERRAL_LEVELS)}
LEVEL_BY_KEY = {level.key: level for level in REFERRAL_LEVELS}


def _round_rub(value: float | Decimal | int) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def get_referral_level(paid_friends_count: int) -> ReferralLevel:
    count = max(0, int(paid_friends_count or 0))
    current = REFERRAL_LEVELS[0]
    for level in REFERRAL_LEVELS:
        if count >= level.threshold:
            current = level
        else:
            break
    return current


def get_next_referral_level(paid_friends_count: int) -> ReferralLevel | None:
    count = max(0, int(paid_friends_count or 0))
    for level in REFERRAL_LEVELS:
        if count < level.threshold:
            return level
    return None


def _level_payload(level: ReferralLevel) -> dict[str, Any]:
    return {
        "key": level.key,
        "name": level.name,
        "threshold": int(level.threshold),
        "cashbackPercent": int(level.cashback_percent),
    }


def _reward_value_label(reward_type: str | None, reward_value: int | None) -> str:
    value = int(reward_value or 0)
    if reward_type == "balance":
        return f"+{value} ₽ на баланс"
    if reward_type == "discount_percent":
        return f"скидка {value}% на следующую покупку"
    return "Сундук ждёт открытия"


def _chest_title(level_key: str) -> str:
    level = LEVEL_BY_KEY.get(level_key)
    return f"Сундук за {level.name if level else level_key}"


async def get_paid_friends_count(referrer_user_id: int) -> int:
    return await ReferralRewards.filter(
        referrer_user_id=int(referrer_user_id),
        kind="first_payment",
    ).count()


async def get_invited_count(referrer_user_id: int) -> int:
    return (
        await Users.filter(
            Q(referred_by=int(referrer_user_id))
            & (Q(utm__isnull=True) | (~Q(utm="partner") & ~Q(utm__startswith="qr_")))
        )
        .count()
    )


async def ensure_referral_level_rewards(
    *, user_id: int, paid_friends_count: int
) -> list[ReferralLevelRewards]:
    """Create one available chest for every reached paid-friends level.

    Safe to call from payment, backfill, and status reads: unique(user, level) makes
    this idempotent and no cashback is ever backfilled here.
    """
    created: list[ReferralLevelRewards] = []
    count = max(0, int(paid_friends_count or 0))
    for level in REFERRAL_LEVELS:
        # Bronze level is now the baseline (everyone joins at bronze with 10% cashback),
        # but the bronze chest is still earned: it requires at least one paid friend.
        # Skip chest creation for users who have not yet earned any paid friend.
        if count < max(1, level.threshold):
            continue
        try:
            chest = await ReferralLevelRewards.create(
                user_id=int(user_id),
                level_key=level.key,
                status="available",
            )
            created.append(chest)
        except IntegrityError:
            continue
    return created


async def award_referral_cashback(
    *,
    payment_id: str,
    referral_user: Users,
    amount_external_rub: int,
) -> dict[str, Any]:
    """Award ordinary referral cashback from real external RUB only.

    This path deliberately skips partner / QR attribution and partner referrers.
    Partner economics remain in PartnerEarnings and are not mixed with this ledger.
    """
    pid = str(payment_id or "").strip()
    amount_external = max(0, int(amount_external_rub or 0))
    if not pid:
        return {"applied": False, "reason": "missing_payment_id"}
    if amount_external <= 0:
        return {"applied": False, "reason": "no_external_amount"}

    referrer_id = int(getattr(referral_user, "referred_by", 0) or 0)
    if not referrer_id:
        return {"applied": False, "reason": "no_referrer"}
    if is_partner_source_utm(getattr(referral_user, "utm", None)):
        return {"applied": False, "reason": "partner_source"}

    referrer = await Users.get_or_none(id=referrer_id)
    if not referrer:
        return {"applied": False, "reason": "missing_referrer"}
    if bool(getattr(referrer, "is_partner", False)):
        return {"applied": False, "reason": "partner_referrer"}

    existing = await ReferralCashbackRewards.get_or_none(payment_id=pid)
    if existing:
        paid_count = await get_paid_friends_count(referrer_id)
        await ensure_referral_level_rewards(user_id=referrer_id, paid_friends_count=paid_count)
        return {
            "applied": False,
            "reason": "duplicate_payment",
            "referrer_id": int(existing.referrer_user_id),
            "reward_rub": int(existing.reward_rub or 0),
            "cashback_percent": int(existing.cashback_percent or 0),
            "level_key": str(existing.level_key),
            "level_name": LEVEL_BY_KEY.get(str(existing.level_key), REFERRAL_LEVELS[0]).name,
        }

    paid_count = await get_paid_friends_count(referrer_id)
    level = get_referral_level(paid_count)
    percent = max(0, int(level.cashback_percent or 0))
    if percent <= 0:
        await ensure_referral_level_rewards(user_id=referrer_id, paid_friends_count=paid_count)
        return {
            "applied": False,
            "reason": "zero_percent_level",
            "referrer_id": referrer_id,
            "reward_rub": 0,
            "cashback_percent": percent,
            "level_key": level.key,
            "level_name": level.name,
        }

    reward_rub = _round_rub(float(amount_external) * float(percent) / 100.0)
    if reward_rub <= 0:
        await ensure_referral_level_rewards(user_id=referrer_id, paid_friends_count=paid_count)
        return {
            "applied": False,
            "reason": "zero_reward",
            "referrer_id": referrer_id,
            "reward_rub": 0,
            "cashback_percent": percent,
            "level_key": level.key,
            "level_name": level.name,
        }

    try:
        async with in_transaction() as conn:
            cashback = await ReferralCashbackRewards.create(
                payment_id=pid,
                referrer_user_id=referrer_id,
                referred_user_id=int(referral_user.id),
                amount_external_rub=int(amount_external),
                cashback_percent=int(percent),
                reward_rub=int(reward_rub),
                level_key=level.key,
                using_db=conn,
            )
            await Users.filter(id=referrer_id).using_db(conn).update(
                balance=F("balance") + int(reward_rub)
            )
    except IntegrityError:
        return {"applied": False, "reason": "duplicate_payment"}

    created_chests = await ensure_referral_level_rewards(
        user_id=referrer_id, paid_friends_count=paid_count
    )
    return {
        "applied": True,
        "reason": "awarded",
        "referrer_id": referrer_id,
        "cashback_id": int(cashback.id),
        "reward_rub": int(reward_rub),
        "cashback_percent": int(percent),
        "level_key": level.key,
        "level_name": level.name,
        "paid_friends_count": int(paid_count),
        "created_chests": [
            {"id": int(chest.id), "levelKey": chest.level_key, "levelName": LEVEL_BY_KEY.get(chest.level_key, REFERRAL_LEVELS[0]).name}
            for chest in created_chests
        ],
    }


def choose_chest_reward(level_key: str, *, random_value: float | None = None) -> tuple[str, int]:
    rewards = CHEST_REWARD_BY_LEVEL.get(level_key)
    if not rewards:
        raise ValueError(f"Unknown referral chest level: {level_key}")
    roll = random.random() if random_value is None else float(random_value)
    if roll < 0.60:
        return "balance", int(rewards["balance"])
    return "discount_percent", int(rewards["discount_percent"])


async def open_referral_chest(*, user: Users, chest_id: int) -> dict[str, Any] | None:
    async with in_transaction() as conn:
        chest = (
            await ReferralLevelRewards.select_for_update()
            .using_db(conn)
            .get_or_none(id=int(chest_id), user_id=int(user.id))
        )
        if not chest or chest.status != "available":
            return None
        reward_type, reward_value = choose_chest_reward(str(chest.level_key))
        await ReferralLevelRewards.filter(id=chest.id).using_db(conn).update(
            status="opened",
            reward_type=reward_type,
            reward_value=int(reward_value),
            opened_at=datetime.now(timezone.utc),
        )
        if reward_type == "balance":
            await Users.filter(id=int(user.id)).using_db(conn).update(
                balance=F("balance") + int(reward_value)
            )
        else:
            await PersonalDiscount.create(
                user_id=int(user.id),
                percent=int(reward_value),
                is_permanent=False,
                remaining_uses=1,
                source="referral_level_chest",
                metadata={
                    "referral_level_reward_id": int(chest.id),
                    "level_key": str(chest.level_key),
                },
                using_db=conn,
            )

    level = LEVEL_BY_KEY.get(str(chest.level_key), REFERRAL_LEVELS[0])
    return {
        "id": int(chest.id),
        "levelKey": str(chest.level_key),
        "levelName": level.name,
        "type": reward_type,
        "value": int(reward_value),
        "valueLabel": _reward_value_label(reward_type, reward_value),
        "title": _chest_title(str(chest.level_key)),
    }


async def build_referral_status(user: Users, *, ensure_chests: bool = True) -> dict[str, Any]:
    user_id = int(user.id)
    invited_count = 0 if bool(getattr(user, "is_partner", False)) else await get_invited_count(user_id)
    paid_friends_count = 0 if bool(getattr(user, "is_partner", False)) else await get_paid_friends_count(user_id)
    if ensure_chests and not bool(getattr(user, "is_partner", False)):
        await ensure_referral_level_rewards(
            user_id=user_id, paid_friends_count=paid_friends_count
        )

    current_level = get_referral_level(paid_friends_count)
    next_level = get_next_referral_level(paid_friends_count)
    total_cashback = sum(
        int(row.reward_rub or 0)
        for row in await ReferralCashbackRewards.filter(referrer_user_id=user_id).all()
    )
    fresh_user = await Users.get_or_none(id=user_id)
    available_balance = int(getattr(fresh_user or user, "balance", 0) or 0)

    pending_chests_rows = (
        await ReferralLevelRewards.filter(user_id=user_id, status="available")
        .order_by("id")
        .all()
    )
    pending_chests = []
    for chest in pending_chests_rows:
        level = LEVEL_BY_KEY.get(str(chest.level_key), REFERRAL_LEVELS[0])
        pending_chests.append(
            {
                "id": int(chest.id),
                "levelKey": str(chest.level_key),
                "levelName": level.name,
                "title": _chest_title(str(chest.level_key)),
            }
        )

    cashback_rows = (
        await ReferralCashbackRewards.filter(referrer_user_id=user_id)
        .order_by("-created_at")
        .limit(5)
    )
    chest_rows = (
        await ReferralLevelRewards.filter(user_id=user_id, status="opened")
        .order_by("-opened_at", "-id")
        .limit(5)
    )
    last_rewards: list[dict[str, Any]] = []
    for row in cashback_rows:
        last_rewards.append(
            {
                "type": "cashback",
                "title": f"Кэшбек {int(row.cashback_percent or 0)}%",
                "valueLabel": f"+{int(row.reward_rub or 0)} ₽",
                "createdAt": row.created_at.isoformat() if row.created_at else "",
            }
        )
    for row in chest_rows:
        last_rewards.append(
            {
                "type": "chest",
                "title": _chest_title(str(row.level_key)),
                "valueLabel": _reward_value_label(row.reward_type, row.reward_value),
                "createdAt": row.opened_at.isoformat() if row.opened_at else (row.created_at.isoformat() if row.created_at else ""),
            }
        )
    last_rewards.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    last_rewards = last_rewards[:6]

    next_payload = None
    if next_level is not None:
        next_payload = {
            **_level_payload(next_level),
            "friendsLeft": max(0, int(next_level.threshold) - int(paid_friends_count)),
        }

    referral_link = await build_referral_link(user_id)
    return {
        "referralLink": referral_link,
        "friendsCount": int(invited_count),
        "invitedCount": int(invited_count),
        "paidFriendsCount": int(paid_friends_count),
        "totalCashbackRub": int(total_cashback),
        "availableBalanceRub": int(available_balance),
        "currentLevel": _level_payload(current_level),
        "nextLevel": next_payload,
        "levels": [
            {
                **_level_payload(level),
                "chestRewardLabel": level.chest_reward_label,
                "reached": int(paid_friends_count) >= int(level.threshold),
            }
            for level in REFERRAL_LEVELS
        ],
        "pendingChests": pending_chests,
        "lastRewards": last_rewards,
        "totalBonusDays": int(getattr(user, "referral_bonus_days_total", 0) or 0),
        "level": int(LEVEL_INDEX_BY_KEY.get(current_level.key, 0)),
    }
