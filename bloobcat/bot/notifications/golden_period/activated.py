"""Golden Period activated — invites the user to start sharing right away."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bloobcat.bot.notifications.golden_period._dispatch import dispatch_event

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.db.users import Users


async def notify_golden_period_activated(
    user: "Users", period: "GoldenPeriod"
) -> None:
    await dispatch_event(
        user=user,
        period=period,
        event="activated",
        event_id=f"gp-activated-{period.id}",
        deeplink="/referrals/golden",
        template_kwargs={
            "name": user.full_name or "",
            "amount": int(period.payout_amount_rub or 100),
            "cap": int(period.cap or 0),
        },
        critical=True,  # primary CTA — show even during quiet hours
    )
