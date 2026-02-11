import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from tortoise import Tortoise
from tortoise.expressions import F

# Ensure the project root is importable when running as a script (so `import bloobcat` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.payments import ProcessedPayments  # noqa: E402
from bloobcat.db.partner_earnings import PartnerEarnings  # noqa: E402
from bloobcat.db.partner_qr import PartnerQr  # noqa: E402
from bloobcat.db.users import Users  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402

logger = get_logger("scripts.backfill_partner_earnings")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill partner earnings (RUB cashback) from processed_payments.\n"
            "Safe to re-run: idempotent by partner_earnings.payment_id unique constraint."
        )
    )
    p.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Look back this many days in processed_payments (default: 30).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max processed payments to scan (default: 5000).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually create partner_earnings and increment partners balance (default: dry-run).",
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="Also send Telegram notifications to partners (best-effort). Use with care.",
    )
    return p.parse_args()


def _round_rub(value: float) -> int:
    # Keep consistent with payment flow: integer RUB.
    return int(round(float(value)))


async def _resolve_qr_from_utm(utm: str | None) -> tuple[str, PartnerQr | None]:
    raw = (utm or "").strip()
    if not raw.startswith("qr_"):
        return "referral_link", None
    token = raw[3:]
    if not token:
        return "qr", None
    qr = None
    try:
        qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
        qr = await PartnerQr.get_or_none(id=qr_uuid)
    except Exception:
        qr = None
    if not qr:
        qr = await PartnerQr.get_or_none(slug=token)
    return "qr", qr


async def _send_partner_notify_best_effort(
    *,
    partner: Users,
    referral: Users,
    amount_total_rub: int,
    reward_rub: int,
    percent: int,
    source: str,
    qr: PartnerQr | None,
) -> None:
    try:
        from bloobcat.bot.notifications.partner.earning import (  # local import keeps script light
            notify_partner_earning,
        )

        await notify_partner_earning(
            partner=partner,
            referral=referral,
            amount_total_rub=int(amount_total_rub),
            reward_rub=int(reward_rub),
            percent=int(percent),
            source=str(source),
            qr_title=(getattr(qr, "title", None) if qr else None),
        )
    except Exception as e:
        logger.warning("Failed to send partner notification to %s: %s", partner.id, e)


async def run() -> None:
    args = _parse_args()

    since_days = max(0, int(args.since_days or 0))
    limit = max(1, int(args.limit or 1))
    apply_changes = bool(args.apply)
    notify = bool(args.notify)

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
        logger.info(
            "Scanning processed_payments since=%s (days=%s), limit=%s, apply=%s, notify=%s",
            since_dt.isoformat(),
            since_days,
            limit,
            apply_changes,
            notify,
        )

        payments = (
            await ProcessedPayments.filter(processed_at__gte=since_dt)
            .filter(status__iexact="succeeded")
            .order_by("-processed_at")
            .limit(limit)
        )

        scanned = 0
        created = 0
        skipped_existing = 0
        skipped_not_partner = 0
        skipped_no_referrer = 0
        errors = 0

        for p in payments:
            scanned += 1
            pid = str(getattr(p, "payment_id", "") or "").strip()
            if not pid:
                continue

            try:
                exists = await PartnerEarnings.get_or_none(payment_id=pid)
                if exists:
                    skipped_existing += 1
                    continue

                user = await Users.get_or_none(id=int(p.user_id))
                if not user:
                    continue

                referrer_id = int(getattr(user, "referred_by", 0) or 0)
                if not referrer_id:
                    skipped_no_referrer += 1
                    continue

                partner = await Users.get_or_none(id=referrer_id)
                if not partner or not bool(getattr(partner, "is_partner", False)):
                    skipped_not_partner += 1
                    continue

                # Partner percent
                try:
                    percent = int(partner.referral_percent()) if hasattr(partner, "referral_percent") else int(getattr(partner, "custom_referral_percent", 0) or 0)
                except Exception:
                    percent = int(getattr(partner, "custom_referral_percent", 0) or 0)
                percent = max(0, percent)
                if percent <= 0:
                    skipped_not_partner += 1
                    continue

                # Amount in RUB (processed_payments.amount is Decimal).
                amount_val = getattr(p, "amount", None)
                if isinstance(amount_val, Decimal):
                    amount_total_rub = _round_rub(float(amount_val))
                else:
                    amount_total_rub = _round_rub(float(amount_val or 0))

                reward_rub = _round_rub(float(amount_total_rub) * float(percent) / 100.0)
                if reward_rub <= 0:
                    skipped_not_partner += 1
                    continue

                source, qr = await _resolve_qr_from_utm(getattr(user, "utm", None))

                if not apply_changes:
                    logger.info(
                        "[dry-run] would create earning: payment_id=%s partner=%s referral=%s reward=%s amount=%s source=%s qr=%s",
                        pid,
                        partner.id,
                        user.id,
                        reward_rub,
                        amount_total_rub,
                        source,
                        (getattr(qr, "id", None) if qr else None),
                    )
                    created += 1
                    continue

                await PartnerEarnings.create(
                    payment_id=str(pid),
                    partner=partner,
                    referral_id=int(user.id),
                    qr_code=qr,
                    source=str(source),
                    amount_total_rub=int(amount_total_rub),
                    reward_rub=int(reward_rub),
                    percent=int(percent),
                )
                await Users.filter(id=partner.id).update(balance=F("balance") + int(reward_rub))
                created += 1

                if notify:
                    await _send_partner_notify_best_effort(
                        partner=partner,
                        referral=user,
                        amount_total_rub=amount_total_rub,
                        reward_rub=reward_rub,
                        percent=percent,
                        source=source,
                        qr=qr,
                    )

            except Exception as e:
                errors += 1
                logger.warning("Failed to process payment_id=%s: %s", pid, e)

        logger.info(
            "Done. scanned=%s created=%s existing=%s no_referrer=%s not_partner=%s errors=%s apply=%s",
            scanned,
            created,
            skipped_existing,
            skipped_no_referrer,
            skipped_not_partner,
            errors,
            apply_changes,
        )
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())

