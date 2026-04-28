import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial

from yookassa import Configuration, Payment

from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import yookassa_settings
from bloobcat.services.platega import (
    PLATEGA_PROVIDER,
    PLATEGA_STATUS_CANCELED,
    PLATEGA_STATUS_CHARGEBACK,
    PLATEGA_STATUS_CHARGEBACKED,
    PLATEGA_STATUS_CONFIRMED,
    PlategaAPIError,
    PlategaClient,
    map_platega_status_to_internal,
    normalize_platega_status,
)


logger = get_logger("payment_reconcile")


def _configure_yookassa_if_available() -> bool:
    shop_id = str(getattr(yookassa_settings, "shop_id", "") or "").strip()
    secret_key = (
        yookassa_settings.secret_key.get_secret_value()
        if getattr(yookassa_settings, "secret_key", None)
        else ""
    ).strip()
    if not shop_id or not secret_key:
        return False
    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key
    return True


async def _fetch_yookassa_payment(payment_id: str):
    # YooKassa SDK is sync; run it in a thread with a timeout.
    return await asyncio.wait_for(
        asyncio.to_thread(partial(Payment.find_one, payment_id)),
        timeout=20.0,
    )


async def reconcile_pending_payments(batch_limit: int = 50) -> None:
    """
    Ensures manual payments are eventually applied even if webhook delivery fails.

    Strategy:
    - we persist "pending" records in `processed_payments` at payment creation time
    - this task periodically checks their YooKassa status
    - when status is final -> apply subscription (succeeded) or mark canceled
    """
    # Avoid checking too fresh payments: give webhooks a chance first.
    # Tortoise + Postgres typically operate with tz-aware datetimes.
    # Use UTC-aware threshold to avoid "naive vs aware" issues that could break the query.
    threshold = datetime.now(timezone.utc) - timedelta(seconds=45)

    pendings = (
        await ProcessedPayments.filter(status="pending", processed_at__lt=threshold)
        .order_by("processed_at")
        .limit(batch_limit)
    )
    if not pendings:
        return

    # Import lazily to avoid import cycles at startup.
    from bloobcat.routes.payment import (
        _apply_confirmed_platega_payment,
        _apply_succeeded_payment_fallback,
        _metadata_from_processed_payment,
        _send_manual_payment_canceled_notifications_if_needed,
        _upsert_processed_payment,
    )  # noqa: WPS433

    for row in pendings:
        pid = str(row.payment_id or "").strip()
        if not pid:
            continue
        provider = str(getattr(row, "provider", "") or "yookassa").strip().lower()
        if provider == PLATEGA_PROVIDER:
            try:
                platega_status = await PlategaClient(timeout_seconds=20.0).get_transaction_status(pid)
            except PlategaAPIError as e:
                logger.warning("Failed to fetch Platega payment %s: %s", pid, e)
                continue

            provider_status = normalize_platega_status(platega_status.status)
            internal_status = map_platega_status_to_internal(provider_status)
            metadata = _metadata_from_processed_payment(row)

            if provider_status == PLATEGA_STATUS_CONFIRMED:
                user_id = int(metadata.get("user_id") or row.user_id)
                user = await Users.get_or_none(id=user_id)
                if not user:
                    continue
                try:
                    await _apply_confirmed_platega_payment(
                        payment_id=pid,
                        user=user,
                        metadata=metadata,
                        amount_external=float(platega_status.amount or row.amount_external or 0),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to apply confirmed Platega payment %s: %s",
                        pid,
                        e,
                        exc_info=True,
                    )
                continue

            if provider_status == PLATEGA_STATUS_CANCELED:
                amount_external = float(platega_status.amount or row.amount_external or 0)
                try:
                    await _upsert_processed_payment(
                        payment_id=pid,
                        user_id=int(row.user_id),
                        amount=float(amount_external) + float(row.amount_from_balance or 0),
                        amount_external=float(amount_external),
                        amount_from_balance=float(row.amount_from_balance or 0),
                        status="canceled",
                        provider=PLATEGA_PROVIDER,
                        provider_payload=getattr(row, "provider_payload", None),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to upsert canceled Platega payment %s during reconcile: %s",
                        pid,
                        e,
                        exc_info=True,
                    )
                    continue
                if not bool(metadata.get("is_auto", False)):
                    user = await Users.get_or_none(id=int(row.user_id))
                    if user:
                        try:
                            await _send_manual_payment_canceled_notifications_if_needed(
                                user=user,
                                payment_id=pid,
                                amount_external=amount_external,
                                method=PLATEGA_PROVIDER,
                            )
                        except Exception:
                            pass
                continue

            if provider_status in {PLATEGA_STATUS_CHARGEBACK, PLATEGA_STATUS_CHARGEBACKED}:
                try:
                    await _upsert_processed_payment(
                        payment_id=pid,
                        user_id=int(row.user_id),
                        amount=float(platega_status.amount or row.amount or 0),
                        amount_external=float(platega_status.amount or row.amount_external or 0),
                        amount_from_balance=float(row.amount_from_balance or 0),
                        status="refunded",
                        provider=PLATEGA_PROVIDER,
                        provider_payload=getattr(row, "provider_payload", None),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to upsert chargebacked Platega payment %s during reconcile: %s",
                        pid,
                        e,
                        exc_info=True,
                    )
                continue

            if internal_status != "pending":
                logger.warning(
                    "Unhandled Platega status during reconcile payment_id=%s status=%s",
                    pid,
                    provider_status,
                )
            continue

        if not _configure_yookassa_if_available():
            logger.warning("Skipping YooKassa reconcile because provider credentials are not configured")
            continue

        try:
            yk_payment = await _fetch_yookassa_payment(pid)
        except Exception as e:
            logger.warning("Failed to fetch YooKassa payment %s: %s", pid, e)
            continue

        status = str(getattr(yk_payment, "status", "") or "").strip().lower()
        meta = getattr(yk_payment, "metadata", None)
        meta = meta if isinstance(meta, dict) else {}

        if status == "succeeded":
            try:
                user_id = int(meta.get("user_id") or row.user_id)
            except Exception:
                user_id = int(row.user_id)
            user = await Users.get_or_none(id=user_id)
            if not user:
                continue
            try:
                await _apply_succeeded_payment_fallback(yk_payment, user, meta)
            except Exception as e:
                logger.error(
                    "Failed to apply succeeded payment %s: %s", pid, e, exc_info=True
                )
            continue

        if status == "canceled":
            try:
                amount_external = float(
                    getattr(getattr(yk_payment, "amount", None), "value", 0) or 0
                )
            except Exception:
                amount_external = 0.0
            # Mark canceled (idempotent)
            try:
                await _upsert_processed_payment(
                    payment_id=pid,
                    user_id=int(row.user_id),
                    amount=float(amount_external),
                    amount_external=float(amount_external),
                    amount_from_balance=0.0,
                    status="canceled",
                )
            except Exception as e:
                logger.error(
                    "Failed to upsert canceled payment %s during reconcile: %s",
                    pid,
                    e,
                    exc_info=True,
                )
                continue
            # Notify only for manual payments.
            if not bool(meta.get("is_auto", False)):
                user = await Users.get_or_none(id=int(row.user_id))
                if user:
                    try:
                        await _send_manual_payment_canceled_notifications_if_needed(
                            user=user,
                            payment_id=pid,
                            amount_external=amount_external,
                        )
                    except Exception:
                        pass
            continue


async def run_payment_reconcile_scheduler(interval_seconds: int = 60) -> None:
    logger.info(
        "Starting payment reconcile scheduler (interval: %ss)", interval_seconds
    )
    while True:
        try:
            await reconcile_pending_payments()
        except Exception as e:
            logger.error("Error in payment reconcile scheduler: %s", e, exc_info=True)
        await asyncio.sleep(interval_seconds)
