"""Cashback fraud screening: detect referral self-dealing via HWID overlap.

Without this guard a partner can register a second account, mark themselves as
the referrer, pay from the second account, and harvest cashback to their own
partner balance. Whenever referrer and referred share a HWID we freeze the
PartnerEarnings row at status='pending_review' and surface it to admins in the
bot for explicit approve/reject (with a "contact partner" deeplink).
"""

from __future__ import annotations

import logging
from typing import Any

from bloobcat.db.hwid_local import HwidDeviceLocal
from bloobcat.db.user_devices import UserDevice
from bloobcat.db.users import Users

logger = logging.getLogger(__name__)


async def _safe_values_list(query) -> list:
    """Run a Tortoise values_list query, returning [] on any failure.

    Cashback screening must be fail-open: if HWID tables are missing or DB is
    flaky, we'd rather pay the partner than block legitimate cashback while the
    operator investigates. Frozen-by-mistake cashback is more disruptive than
    occasionally missing a self-dealing detection.
    """

    try:
        return list(await query)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_hwid_query_failed err=%s", exc)
        return []


async def _collect_user_hwids(user: Users) -> set[str]:
    """Union of HWIDs seen for this user across local inventories.

    Fail-open by design — any error collecting HWIDs returns an empty set so
    cashback flow keeps running. Operationally we'd rather pay legitimate
    partners than freeze on a missing-table or DB-flake.
    """

    hwids: set[str] = set()

    try:
        user_id = int(user.id) if user.id is not None else None
        remnawave_uuid = getattr(user, "remnawave_uuid", None)

        if user_id is not None:
            ud_rows = await _safe_values_list(
                UserDevice.filter(user_id=user_id).values_list("hwid", flat=True)
            )
            hwids.update(str(h) for h in ud_rows if h)

            local_by_tg = await _safe_values_list(
                HwidDeviceLocal.filter(telegram_user_id=user_id).values_list(
                    "hwid", flat=True
                )
            )
            hwids.update(str(h) for h in local_by_tg if h)

        if remnawave_uuid:
            local_by_uuid = await _safe_values_list(
                HwidDeviceLocal.filter(user_uuid=remnawave_uuid).values_list(
                    "hwid", flat=True
                )
            )
            hwids.update(str(h) for h in local_by_uuid if h)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_hwid_collection_failed err=%s", exc)
        return set()

    return {h.strip() for h in hwids if h and h.strip()}


async def _safe_detect(referrer: Users, referred: Users) -> dict[str, Any]:
    """Outer fail-open wrapper used by `_award_partner_cashback` injection."""

    try:
        return await detect_referral_overlap_signals(referrer, referred)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_detect_failed err=%s", exc)
        return {"hwid_overlap": [], "same_telegram_id": False}


async def detect_referral_overlap_signals(
    referrer: Users, referred: Users
) -> dict[str, Any]:
    """Collect fraud signals between a referrer and the user that just paid.

    Returns a JSON-safe dict shaped like::

        {
            "hwid_overlap": ["...", "..."],   # shared HWID strings (may be empty)
            "same_telegram_id": False,        # always False unless DB is corrupt
        }
    """

    signals: dict[str, Any] = {"hwid_overlap": [], "same_telegram_id": False}

    if not referrer or not referred:
        return signals

    if int(referrer.id or 0) == int(referred.id or 0):
        signals["same_telegram_id"] = True
        return signals

    referrer_hwids = await _collect_user_hwids(referrer)
    referred_hwids = await _collect_user_hwids(referred)

    overlap = sorted(referrer_hwids & referred_hwids)
    signals["hwid_overlap"] = overlap

    return signals


def should_freeze_cashback(signals: dict[str, Any]) -> bool:
    """Decide whether to freeze a PartnerEarnings row for admin review."""

    if not signals:
        return False
    if signals.get("same_telegram_id"):
        return True
    overlap = signals.get("hwid_overlap") or []
    return bool(overlap)


def build_admin_review_text(
    *,
    earning_id: str,
    referrer: Users,
    referred: Users,
    amount_total_rub: int,
    reward_rub: int,
    percent: int,
    signals: dict[str, Any],
) -> str:
    """Render a Telegram-friendly admin card explaining why a cashback was frozen."""

    referrer_handle = (
        f"@{referrer.username}" if getattr(referrer, "username", None) else "(нет username)"
    )
    referred_handle = (
        f"@{referred.username}" if getattr(referred, "username", None) else "(нет username)"
    )

    overlap = signals.get("hwid_overlap") or []
    overlap_lines = "\n".join(f"  • <code>{h}</code>" for h in overlap[:5]) or "  (нет)"
    if len(overlap) > 5:
        overlap_lines += f"\n  ... и ещё {len(overlap) - 5}"

    same_tg = "ДА" if signals.get("same_telegram_id") else "нет"

    return (
        "🚨 <b>Партнёрский кешбэк заморожен</b>\n"
        "Сработал детектор self-dealing — нужно принять решение вручную.\n\n"
        f"💰 Сумма платежа: <b>{amount_total_rub} ₽</b>\n"
        f"🎁 Кешбэк партнёру: <b>{reward_rub} ₽</b> ({percent}%)\n\n"
        f"🤝 <b>Партнёр-приглашающий:</b>\n"
        f"  ID <code>{referrer.id}</code> {referrer_handle}\n\n"
        f"👤 <b>Приглашённый (платил):</b>\n"
        f"  ID <code>{referred.id}</code> {referred_handle}\n\n"
        f"🔍 <b>Сигналы:</b>\n"
        f"  Тот же telegram_id: {same_tg}\n"
        f"  Совпадающие HWID:\n{overlap_lines}\n\n"
        f"<i>earning_id: <code>{earning_id}</code></i>"
    )
