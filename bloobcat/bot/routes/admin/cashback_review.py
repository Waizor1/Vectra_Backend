"""Admin callback handlers for frozen cashback review (H-2).

When `_award_partner_cashback` detects a HWID overlap between the referrer and
the paying referral, it creates a PartnerEarnings row with review_status =
'pending_review' and posts an admin card with Approve/Reject buttons. The
buttons land here.
"""

from __future__ import annotations

import logging
import uuid

from aiogram import Router
from aiogram.types import CallbackQuery
from tortoise.expressions import F

from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.users import Users

from .functions import IsAdmin

logger = logging.getLogger("bot_admin_cashback_review")

router = Router()


def _parse_callback(data: str) -> tuple[str, uuid.UUID] | None:
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, action, earning_id = parts
    if action not in {"approve", "reject"}:
        return None
    try:
        return action, uuid.UUID(earning_id)
    except ValueError:
        return None


@router.callback_query(
    lambda c: c.data and c.data.startswith("cashback_review:"),
    IsAdmin(),
)
async def handle_cashback_review(callback: CallbackQuery) -> None:
    parsed = _parse_callback(callback.data or "")
    if not parsed:
        await callback.answer("Некорректный callback", show_alert=True)
        return

    action, earning_id = parsed

    earning = await PartnerEarnings.get_or_none(id=earning_id).prefetch_related(
        "partner"
    )
    if not earning:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    current_status = str(getattr(earning, "review_status", "") or "").lower()
    if current_status not in {"pending_review", ""}:
        await callback.answer(
            f"Уже обработано: {current_status}", show_alert=True
        )
        return

    admin_id = callback.from_user.id if callback.from_user else None

    if action == "approve":
        earning.review_status = "approved"
        await earning.save(update_fields=["review_status"])
        # Credit balance now (atomic increment, idempotent because status guards re-entry).
        await Users.filter(id=earning.partner_id).update(
            balance=F("balance") + int(earning.reward_rub)
        )
        logger.info(
            "cashback_review_approved earning=%s admin=%s reward=%s partner=%s",
            earning.id,
            admin_id,
            earning.reward_rub,
            earning.partner_id,
        )
        if callback.message:
            await callback.message.edit_text(
                (callback.message.text or "")
                + f"\n\n✅ <b>ОДОБРЕНО</b> (admin id={admin_id})\n"
                f"Партнёру начислено {earning.reward_rub} ₽.",
                parse_mode="HTML",
            )
        await callback.answer("Одобрено, баланс начислен")
        return

    if action == "reject":
        earning.review_status = "rejected"
        await earning.save(update_fields=["review_status"])
        logger.info(
            "cashback_review_rejected earning=%s admin=%s partner=%s",
            earning.id,
            admin_id,
            earning.partner_id,
        )
        if callback.message:
            await callback.message.edit_text(
                (callback.message.text or "")
                + f"\n\n❌ <b>ОТМЕНЕНО</b> (admin id={admin_id})\n"
                f"Баланс партнёра не пополнен.",
                parse_mode="HTML",
            )
        await callback.answer("Отменено, баланс не начислен")
        return
