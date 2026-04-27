import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial

from yookassa import Payment

from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.services.platega import (
    PLATEGA_PROVIDER,
    PLATEGA_STATUS_CANCELED,
    PLATEGA_STATUS_CHARGEBACK,
    PLATEGA_STATUS_CHARGEBACKED,
    PLATEGA_STATUS_CONFIRMED,
    PlategaAPIError,
    PlategaClient,
    PlategaConfigError,
    map_platega_status_to_internal,
    normalize_platega_status,
    parse_platega_payload,
)


logger = get_logger("payment_reconcile")


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
    - this task periodically checks their provider status
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
    from bloobcat.routes.payment import (  # noqa: WPS433
        PAYMENT_PROVIDER_YOOKASSA,
        _apply_confirmed_platega_payment,
        _apply_succeeded_payment_fallback,
        _configure_yookassa_if_available,
        _metadata_from_processed_payment,
        _provider_payload_json,
        _validate_platega_amount_currency,
        _upsert_processed_payment,
    )
    from bloobcat.bot.notifications.subscription.renewal import notify_payment_canceled_yookassa  # noqa: WPS433

    for row in pendings:
        pid = str(row.payment_id or "").strip()
        if not pid:
            continue
        provider = str(getattr(row, "provider", "") or PAYMENT_PROVIDER_YOOKASSA).strip().lower()
        if provider == PLATEGA_PROVIDER:
            try:
                status_result = await PlategaClient(
                    timeout_seconds=20.0
                ).get_transaction_status(pid)
            except (PlategaConfigError, PlategaAPIError) as e:
                status_code = getattr(e, "status_code", None)
                logger.warning(
                    "Failed to fetch Platega payment %s status_code=%s",
                    pid,
                    status_code,
                )
                continue

            provider_status = normalize_platega_status(status_result.status)
            internal_status = map_platega_status_to_internal(provider_status)
            metadata = parse_platega_payload(status_result.payload)
            if isinstance(metadata.get("metadata"), dict):
                metadata = dict(metadata["metadata"])
            if not metadata:
                metadata = _metadata_from_processed_payment(row)

            if provider_status == PLATEGA_STATUS_CONFIRMED:
                try:
                    _validate_platega_amount_currency(
                        provider_amount=status_result.amount,
                        provider_currency=status_result.currency,
                        metadata=metadata,
                    )
                    user_id = int(metadata.get("user_id") or row.user_id)
                    user = await Users.get_or_none(id=user_id)
                    if user:
                        await _apply_confirmed_platega_payment(
                            payment_id=pid,
                            user=user,
                            metadata=metadata,
                            amount_external=float(status_result.amount or row.amount_external or 0),
                        )
                except Exception as e:
                    logger.error("Failed to apply Platega payment %s: %s", pid, e, exc_info=True)
                continue

            if provider_status in {
                PLATEGA_STATUS_CANCELED,
                PLATEGA_STATUS_CHARGEBACK,
                PLATEGA_STATUS_CHARGEBACKED,
            }:
                await _upsert_processed_payment(
                    payment_id=pid,
                    user_id=int(row.user_id),
                    amount=float(row.amount or 0),
                    amount_external=float(status_result.amount or row.amount_external or 0),
                    amount_from_balance=float(row.amount_from_balance or 0),
                    status=internal_status,
                    provider=PLATEGA_PROVIDER,
                    provider_payload=_provider_payload_json(
                        {
                            "metadata": metadata,
                            "provider_status": provider_status,
                        }
                    ),
                )
                continue

            continue

        if not _configure_yookassa_if_available():
            logger.info("Skipping YooKassa reconcile because credentials are not configured")
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
                logger.error("Failed to apply succeeded payment %s: %s", pid, e, exc_info=True)
            continue

        if status == "canceled":
            try:
                amount_external = float(getattr(getattr(yk_payment, "amount", None), "value", 0) or 0)
            except Exception:
                amount_external = 0.0
            # Mark canceled (idempotent)
            await _upsert_processed_payment(
                payment_id=pid,
                user_id=int(row.user_id),
                amount=float(amount_external),
                amount_external=float(amount_external),
                amount_from_balance=0.0,
                status="canceled",
            )
            # Notify only for manual payments.
            if not bool(meta.get("is_auto", False)):
                user = await Users.get_or_none(id=int(row.user_id))
                if user:
                    try:
                        await notify_payment_canceled_yookassa(user=user)
                    except Exception:
                        pass
            continue


async def run_payment_reconcile_scheduler(interval_seconds: int = 60) -> None:
    logger.info("Starting payment reconcile scheduler (interval: %ss)", interval_seconds)
    while True:
        try:
            await reconcile_pending_payments()
        except Exception as e:
            logger.error("Error in payment reconcile scheduler: %s", e, exc_info=True)
        await asyncio.sleep(interval_seconds)
