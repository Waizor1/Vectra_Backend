"""Golden Period cap reached — celebrates 15/15 payouts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bloobcat.bot.notifications.golden_period._dispatch import dispatch_event

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.db.users import Users


async def notify_golden_period_cap_reached(
    user: "Users", period: "GoldenPeriod"
) -> None:
    await dispatch_event(
        user=user,
        period=period,
        event="cap_reached",
        event_id=f"gp-cap-{period.id}",
        deeplink="/referrals/golden",
        template_kwargs={
            "name": user.full_name or "",
            "cap": int(period.cap or 0),
            "total": int(period.total_paid_rub or 0),
        },
        critical=False,
    )
