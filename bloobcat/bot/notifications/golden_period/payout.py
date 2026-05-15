"""Golden Period payout — fired each time +100₽ lands on the referrer's balance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bloobcat.bot.notifications.golden_period._dispatch import dispatch_event

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
    from bloobcat.db.users import Users


async def notify_golden_period_payout(
    user: "Users",
    period: "GoldenPeriod",
    payout: "GoldenPeriodPayout",
) -> None:
    paid = int(period.paid_out_count or 0) + 1  # this payout pushes counter
    cap = int(period.cap or 0)
    remaining = max(0, cap - paid)
    await dispatch_event(
        user=user,
        period=period,
        event="payout",
        event_id=f"gp-payout-{payout.id}",
        deeplink="/referrals/golden",
        template_kwargs={
            "name": user.full_name or "",
            "amount": int(payout.amount_rub or 0),
            "paid": paid,
            "cap": cap,
            "remaining": remaining,
        },
        critical=False,
    )
