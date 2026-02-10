import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

# Ensure the project root is importable when running as a script (so `import bloobcat` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.users import Users  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.routes.payment import _apply_referral_first_payment_reward  # noqa: E402

logger = get_logger("scripts.backfill_referral_reward")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill referral first-payment reward for a specific user/payment.\n"
            "Safe to re-run: uses referral_rewards ledger idempotency."
        )
    )
    p.add_argument("--referred-user-id", type=int, required=True, help="Telegram user id of the payer (referred).")
    p.add_argument("--payment-id", type=str, required=True, help="Payment id (YooKassa id) used for idempotency.")
    p.add_argument("--months", type=int, default=0, help="Purchase months (1/3/6/12).")
    p.add_argument("--device-count", type=int, default=1, help="Purchased device count (1/3/10).")
    p.add_argument("--amount-rub", type=int, default=0, help="Amount in RUB (int).")
    p.add_argument(
        "--set-referrer-id",
        type=int,
        default=0,
        help="If provided and referred_by is empty, set referred_by to this id before applying reward.",
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="Also send bot notifications to referrer and referred user (best-effort).",
    )
    return p.parse_args()


async def run() -> None:
    args = _parse_args()

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        referred = await Users.get_or_none(id=int(args.referred_user_id))
        if not referred:
            raise SystemExit(f"User not found: {args.referred_user_id}")

        if int(getattr(referred, "referred_by", 0) or 0) == 0 and int(args.set_referrer_id or 0) > 0:
            if int(args.set_referrer_id) == int(referred.id):
                raise SystemExit("Referrer id cannot be the same as referred user id.")
            referrer = await Users.get_or_none(id=int(args.set_referrer_id))
            if not referrer:
                raise SystemExit(f"Referrer not found: {args.set_referrer_id}")
            # This is an operator action; bypasses the normal "only before is_registered" guard.
            referred.referred_by = int(args.set_referrer_id)
            await referred.save(update_fields=["referred_by"])
            logger.info("Set referred_by for user=%s -> %s", referred.id, args.set_referrer_id)

        res = await _apply_referral_first_payment_reward(
            referred_user_id=int(referred.id),
            payment_id=str(args.payment_id),
            amount_rub=int(args.amount_rub) if args.amount_rub else None,
            months=int(args.months) if args.months else 0,
            device_count=max(1, int(args.device_count or 1)),
        )
        logger.info("apply result: %s", res)

        if args.notify and res.get("applied"):
            try:
                from bloobcat.bot.notifications.general.referral import (  # local import to keep script lightweight
                    on_referral_friend_bonus,
                    on_referral_payment,
                )

                referrer = await Users.get(id=int(res["referrer_id"]))
                await on_referral_payment(
                    user=referrer,
                    referral=referred,
                    amount=int(args.amount_rub or 0),
                    bonus_days=int(res["referrer_bonus_days"]),
                    friend_bonus_days=int(res["friend_bonus_days"]),
                    months=int(res["months"]),
                    device_count=int(res["device_count"]),
                    applied_to_subscription=bool(res["applied_to_subscription"]),
                )
                await on_referral_friend_bonus(
                    user=referred,
                    referrer=referrer,
                    friend_bonus_days=int(res["friend_bonus_days"]),
                    months=int(res["months"]),
                    device_count=int(res["device_count"]),
                )
                logger.info("Notifications sent (best-effort).")
            except Exception as e:
                logger.warning("Failed to send notifications: %s", e)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())

