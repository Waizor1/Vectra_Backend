"""Golden Period clawback — warning sent after the 6h scanner reverses a payout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bloobcat.bot.notifications.golden_period._dispatch import dispatch_event

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
    from bloobcat.db.users import Users


def _format_breakdown(breakdown: dict, locale: str) -> str:
    parts: list[str] = []
    balance = int(breakdown.get("balance_rub", 0) or 0)
    days = int(breakdown.get("days_removed", 0) or 0)
    lte = float(breakdown.get("lte_gb_removed", 0.0) or 0.0)
    if locale == "ru":
        if balance > 0:
            parts.append(f"−{balance}₽ с баланса")
        if days > 0:
            parts.append(f"−{days} дн. с тарифа")
        if lte > 0:
            parts.append(f"−{lte:.2f} GB LTE")
    else:
        if balance > 0:
            parts.append(f"−{balance}₽ from balance")
        if days > 0:
            parts.append(f"−{days} days from tariff")
        if lte > 0:
            parts.append(f"−{lte:.2f} GB LTE")
    return ", ".join(parts) if parts else ""


async def notify_golden_period_clawback(
    user: "Users",
    period: "GoldenPeriod",
    payout: "GoldenPeriodPayout",
    breakdown: dict,
) -> None:
    from bloobcat.bot.notifications.localization import get_user_locale

    locale = get_user_locale(user)
    breakdown_text = _format_breakdown(breakdown, locale)
    await dispatch_event(
        user=user,
        period=period,
        event="clawback",
        event_id=f"gp-clawback-{payout.id}",
        deeplink="/referrals/golden",
        template_kwargs={
            "name": user.full_name or "",
            "amount": int(breakdown.get("amount", payout.amount_rub or 0) or 0),
            "breakdown": breakdown_text,
        },
        critical=True,  # warning — must be visible regardless of quiet hours
    )
