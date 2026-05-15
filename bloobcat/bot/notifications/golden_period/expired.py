"""Golden Period expired — fired by the 10-min expiry scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bloobcat.bot.notifications.golden_period._dispatch import dispatch_event

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.db.users import Users


async def notify_golden_period_expired(
    user: "Users", period: "GoldenPeriod"
) -> None:
    await dispatch_event(
        user=user,
        period=period,
        event="expired",
        event_id=f"gp-expired-{period.id}",
        deeplink="/referrals/golden",
        template_kwargs={
            "name": user.full_name or "",
            "total": int(period.total_paid_rub or 0),
            "paid": int(period.paid_out_count or 0),
        },
        critical=False,
    )
