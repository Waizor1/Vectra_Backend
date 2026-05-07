from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from tortoise.exceptions import IntegrityError

from bloobcat.db.analytics import (
    AnalyticsPaymentEvents,
    AnalyticsServiceDaily,
    AnalyticsTrialDaily,
    AnalyticsTrialRiskFlags,
)
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import app_settings, remnawave_settings


logger = get_logger("tasks.service_growth_analytics")

BYTES_IN_GB = 1024**3
MSK_TZ = timezone(timedelta(hours=3))
PRODUCT_MAIN_PAID = "main_paid"
PRODUCT_LTE_PAID = "lte_paid"
PRODUCT_ALL_PAID = "all_paid"
TRIAL_FLAG_STATUS_NEW = "new"

_collector_lock = asyncio.Lock()
_last_nightly_reconcile_day: date | None = None


@dataclass(slots=True)
class PaymentBuckets:
    subscription_revenue: Decimal = Decimal("0")
    lte_revenue: Decimal = Decimal("0")
    amount_external: Decimal = Decimal("0")
    amount_from_balance: Decimal = Decimal("0")
    lte_gb_purchased: float = 0.0
    payments_count: int = 0
    paying_users: set[int] | None = None

    def add_user(self, user_id: int) -> None:
        if self.paying_users is None:
            self.paying_users = set()
        self.paying_users.add(int(user_id))

    @property
    def paying_users_count(self) -> int:
        return len(self.paying_users or set())

    @property
    def revenue_total(self) -> Decimal:
        return self.subscription_revenue + self.lte_revenue


@dataclass(frozen=True, slots=True)
class TrialAbuseSettings:
    warning_gb: float
    critical_gb: float
    spike_share_pct: float
    spike_min_gb: float


def _format_range_start(start_date: date) -> str:
    start_dt = datetime.combine(start_date, time.min, tzinfo=MSK_TZ)
    return start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _format_range_end(end_dt: datetime) -> str:
    return end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=MSK_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _safe_parse_date(raw: Any) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except Exception:
        return None


def _safe_int(raw: Any, default: int | None = None) -> int | None:
    if raw in (None, ""):
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _safe_float(raw: Any, default: float = 0.0) -> float:
    if raw in (None, ""):
        return default
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _provider_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payment_metadata(row: ProcessedPayments) -> dict[str, Any]:
    payload = _provider_payload(getattr(row, "provider_payload", None))
    metadata = payload.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _payment_kind(metadata: dict[str, Any]) -> str:
    if bool(metadata.get("lte_topup")):
        return "lte_topup"
    if str(metadata.get("is_auto", "")).strip().lower() in {"1", "true", "yes"}:
        return "auto_renewal"
    return "subscription"


def _normalize_payment_event(row: ProcessedPayments) -> dict[str, Any]:
    metadata = _payment_metadata(row)
    amount_external = _money(getattr(row, "amount_external", 0))
    amount_from_balance = _money(getattr(row, "amount_from_balance", 0))
    amount_total = amount_external + amount_from_balance
    if amount_total <= 0:
        amount_total = _money(getattr(row, "amount", 0))
        amount_external = amount_total

    kind = _payment_kind(metadata)
    if kind == "lte_topup":
        lte_gb_purchased = _safe_float(metadata.get("lte_gb_delta"), 0.0)
        lte_revenue = amount_total
    else:
        lte_gb_purchased = _safe_float(metadata.get("lte_gb"), 0.0)
        lte_cost = _money(metadata.get("lte_cost"))
        if lte_cost <= 0 and lte_gb_purchased > 0:
            lte_cost = _money(
                lte_gb_purchased * _safe_float(metadata.get("lte_price_per_gb"), 0.0)
            )
        lte_revenue = min(amount_total, max(Decimal("0.00"), lte_cost))

    subscription_revenue = max(Decimal("0.00"), amount_total - lte_revenue)

    return {
        "payment_id": str(row.payment_id),
        "user_id": int(row.user_id),
        "paid_at": row.processed_at,
        "provider": getattr(row, "provider", None),
        "kind": kind,
        "tariff_kind": metadata.get("tariff_kind"),
        "months": _safe_int(metadata.get("month")),
        "device_count": _safe_int(metadata.get("device_count")),
        "subscription_revenue_rub": subscription_revenue,
        "lte_revenue_rub": lte_revenue,
        "amount_external_rub": amount_external,
        "amount_from_balance_rub": amount_from_balance,
        "lte_gb_purchased": max(0.0, float(lte_gb_purchased)),
    }


async def _upsert_payment_event(payload: dict[str, Any]) -> bool:
    existing = await AnalyticsPaymentEvents.get_or_none(payment_id=payload["payment_id"])
    if existing is None:
        try:
            await AnalyticsPaymentEvents.create(**payload)
            return True
        except IntegrityError:
            existing = await AnalyticsPaymentEvents.get_or_none(
                payment_id=payload["payment_id"]
            )
            if existing is None:
                raise

    for key, value in payload.items():
        setattr(existing, key, value)
    await existing.save(
        update_fields=[
            "user_id",
            "paid_at",
            "provider",
            "kind",
            "tariff_kind",
            "months",
            "device_count",
            "subscription_revenue_rub",
            "lte_revenue_rub",
            "amount_external_rub",
            "amount_from_balance_rub",
            "lte_gb_purchased",
            "updated_at",
        ]
    )
    return False


async def sync_payment_events_once(lookback_days: int = 14, batch_limit: int = 2000) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))
    rows = (
        await ProcessedPayments.filter(status="succeeded", processed_at__gte=since)
        .order_by("-processed_at")
        .limit(max(1, int(batch_limit)))
    )
    changed = 0
    for row in rows:
        payload = _normalize_payment_event(row)
        created = await _upsert_payment_event(payload)
        changed += 1 if created else 0
    return changed


async def _read_trial_abuse_settings() -> TrialAbuseSettings:
    fallback = TrialAbuseSettings(
        warning_gb=max(0.0, float(getattr(app_settings, "trial_abuse_warning_gb", 3.0))),
        critical_gb=max(0.0, float(getattr(app_settings, "trial_abuse_critical_gb", 10.0))),
        spike_share_pct=max(
            0.0,
            float(getattr(app_settings, "trial_abuse_spike_share_pct", 50.0)),
        ),
        spike_min_gb=max(0.0, float(getattr(app_settings, "trial_abuse_spike_min_gb", 2.0))),
    )
    try:
        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            """
            SELECT
                trial_abuse_warning_gb,
                trial_abuse_critical_gb,
                trial_abuse_spike_share_pct,
                trial_abuse_spike_min_gb
            FROM tvpn_admin_settings
            LIMIT 1
            """
        )
        if not rows:
            return fallback
        row = rows[0]
        return TrialAbuseSettings(
            warning_gb=max(0.0, _safe_float(row.get("trial_abuse_warning_gb"), fallback.warning_gb)),
            critical_gb=max(0.0, _safe_float(row.get("trial_abuse_critical_gb"), fallback.critical_gb)),
            spike_share_pct=max(
                0.0,
                _safe_float(row.get("trial_abuse_spike_share_pct"), fallback.spike_share_pct),
            ),
            spike_min_gb=max(0.0, _safe_float(row.get("trial_abuse_spike_min_gb"), fallback.spike_min_gb)),
        )
    except Exception as exc:
        logger.debug("Trial abuse settings unavailable, using fallback: {}", exc)
        return fallback


async def _active_trial_uuid_map() -> dict[str, int]:
    users = await Users.filter(is_trial=True, remnawave_uuid__not_isnull=True)
    result: dict[str, int] = {}
    for user in users:
        if user.remnawave_uuid:
            result[str(user.remnawave_uuid).lower()] = int(user.id)
    return result


async def _vectra_uuid_set() -> set[str]:
    """Lowercased RemnaWave UUIDs of all Vectra users.

    The RemnaWave panel may host other tenants whose users we must not count
    in service-growth aggregates. Filtering raw node usage rows by this set
    keeps `analytics_service_daily` scoped to Vectra-owned subscribers only.
    """
    uuids: set[str] = set()
    rows = await Users.filter(remnawave_uuid__not_isnull=True).only("remnawave_uuid")
    for row in rows:
        if row.remnawave_uuid:
            uuids.add(str(row.remnawave_uuid).lower())
    return uuids


async def _fetch_remnawave_usage(
    *,
    client: RemnaWaveClient,
    start_day: date,
    end_dt: datetime,
    timeout_seconds: float,
) -> tuple[
    dict[tuple[date, str], int],
    dict[tuple[date, str], int],
    dict[date, dict[int, int]],
]:
    marker = str(remnawave_settings.lte_node_marker or "").upper()
    nodes_resp = await asyncio.wait_for(client.nodes.get_nodes(), timeout=timeout_seconds)
    nodes = nodes_resp.get("response") or []
    trial_by_uuid = await _active_trial_uuid_map()
    vectra_uuids = await _vectra_uuid_set()
    start_str = _format_range_start(start_day)
    end_str = _format_range_end(end_dt)

    total_by_product: dict[tuple[date, str], int] = defaultdict(int)
    trial_by_product: dict[tuple[date, str], int] = defaultdict(int)
    trial_by_user: dict[date, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    sem = asyncio.Semaphore(4)

    async def load_node(node: dict[str, Any]) -> None:
        node_uuid = str(node.get("uuid") or "")
        if not node_uuid:
            return
        node_name = str(node.get("name") or "")
        async with sem:
            resp = await asyncio.wait_for(
                client.nodes.get_node_user_usage_by_range(node_uuid, start_str, end_str),
                timeout=timeout_seconds,
            )
        items = resp.get("response") or []
        for item in items:
            day = _safe_parse_date(item.get("date"))
            if day is None or day < start_day:
                continue
            user_uuid = str(item.get("userUuid") or "").lower()
            # Skip users that don't belong to Vectra (other tenants on the
            # shared panel). Fail-closed: if the local UUID set is empty we
            # under-report rather than aggregate foreign traffic.
            if user_uuid not in vectra_uuids:
                continue
            item_node_name = str(item.get("nodeName") or node_name)
            product = (
                PRODUCT_LTE_PAID
                if marker and marker in item_node_name.upper()
                else PRODUCT_MAIN_PAID
            )
            total_bytes = max(0, int(_safe_float(item.get("total"), 0.0)))
            total_by_product[(day, product)] += total_bytes

            trial_user_id = trial_by_uuid.get(user_uuid)
            if trial_user_id is not None:
                trial_by_product[(day, product)] += total_bytes
                trial_by_user[day][trial_user_id] += total_bytes

    await asyncio.gather(*(load_node(node) for node in nodes))
    return total_by_product, trial_by_product, trial_by_user


def _add_payment_bucket(
    buckets: dict[tuple[date, str], PaymentBuckets],
    day: date,
    product: str,
    event: AnalyticsPaymentEvents,
    *,
    subscription_revenue: Decimal | None = None,
    lte_revenue: Decimal | None = None,
    amount_external: Decimal | None = None,
    amount_from_balance: Decimal | None = None,
    lte_gb_purchased: float | None = None,
) -> None:
    bucket = buckets[(day, product)]
    bucket.subscription_revenue += (
        _money(event.subscription_revenue_rub)
        if subscription_revenue is None
        else subscription_revenue
    )
    bucket.lte_revenue += _money(event.lte_revenue_rub) if lte_revenue is None else lte_revenue
    bucket.amount_external += (
        _money(event.amount_external_rub) if amount_external is None else amount_external
    )
    bucket.amount_from_balance += (
        _money(event.amount_from_balance_rub)
        if amount_from_balance is None
        else amount_from_balance
    )
    bucket.lte_gb_purchased += (
        float(event.lte_gb_purchased or 0)
        if lte_gb_purchased is None
        else float(lte_gb_purchased)
    )
    bucket.payments_count += 1
    bucket.add_user(int(event.user_id))


def _allocated_payment_sources(
    event: AnalyticsPaymentEvents,
    product_revenue: Decimal,
    total_revenue: Decimal,
) -> tuple[Decimal, Decimal]:
    if product_revenue <= 0 or total_revenue <= 0:
        return Decimal("0.00"), Decimal("0.00")
    ratio = product_revenue / total_revenue
    return (
        (_money(event.amount_external_rub) * ratio).quantize(Decimal("0.01")),
        (_money(event.amount_from_balance_rub) * ratio).quantize(Decimal("0.01")),
    )


async def _payment_buckets(start_day: date, end_day: date) -> dict[tuple[date, str], PaymentBuckets]:
    start_dt, _ = _day_bounds_utc(start_day)
    _, end_dt = _day_bounds_utc(end_day)
    rows = await AnalyticsPaymentEvents.filter(paid_at__gte=start_dt, paid_at__lt=end_dt)
    buckets: dict[tuple[date, str], PaymentBuckets] = defaultdict(PaymentBuckets)
    for row in rows:
        paid_at = row.paid_at
        if paid_at.tzinfo is None:
            paid_at = paid_at.replace(tzinfo=timezone.utc)
        day = paid_at.astimezone(MSK_TZ).date()
        subscription_revenue = _money(row.subscription_revenue_rub)
        lte_revenue = _money(row.lte_revenue_rub)
        lte_gb_purchased = float(row.lte_gb_purchased or 0)
        total_revenue = subscription_revenue + lte_revenue
        if subscription_revenue > 0:
            amount_external, amount_from_balance = _allocated_payment_sources(
                row,
                subscription_revenue,
                total_revenue,
            )
            _add_payment_bucket(
                buckets,
                day,
                PRODUCT_MAIN_PAID,
                row,
                subscription_revenue=subscription_revenue,
                lte_revenue=Decimal("0.00"),
                amount_external=amount_external,
                amount_from_balance=amount_from_balance,
                lte_gb_purchased=0.0,
            )
        if lte_revenue > 0 or lte_gb_purchased > 0:
            amount_external, amount_from_balance = _allocated_payment_sources(
                row,
                lte_revenue,
                total_revenue,
            )
            _add_payment_bucket(
                buckets,
                day,
                PRODUCT_LTE_PAID,
                row,
                subscription_revenue=Decimal("0.00"),
                lte_revenue=lte_revenue,
                amount_external=amount_external,
                amount_from_balance=amount_from_balance,
                lte_gb_purchased=lte_gb_purchased,
            )
        _add_payment_bucket(buckets, day, PRODUCT_ALL_PAID, row)
    return buckets


async def _upsert_service_daily(
    *,
    day: date,
    product: str,
    traffic_bytes: int,
    payments: PaymentBuckets,
) -> None:
    traffic_gb = max(0.0, float(traffic_bytes) / BYTES_IN_GB)
    rub_per_gb = float(payments.revenue_total) / traffic_gb if traffic_gb > 0 else 0.0
    payload = {
        "traffic_bytes": max(0, int(traffic_bytes)),
        "traffic_gb": traffic_gb,
        "subscription_revenue_rub": payments.subscription_revenue,
        "lte_revenue_rub": payments.lte_revenue,
        "amount_external_rub": payments.amount_external,
        "amount_from_balance_rub": payments.amount_from_balance,
        "lte_gb_purchased": payments.lte_gb_purchased,
        "payments_count": payments.payments_count,
        "paying_users": payments.paying_users_count,
        "rub_per_gb": rub_per_gb,
    }
    row = await AnalyticsServiceDaily.get_or_none(day=day, product=product)
    if row is None:
        try:
            await AnalyticsServiceDaily.create(day=day, product=product, **payload)
            return
        except IntegrityError:
            row = await AnalyticsServiceDaily.get_or_none(day=day, product=product)
            if row is None:
                raise
    for key, value in payload.items():
        setattr(row, key, value)
    await row.save(update_fields=[*payload.keys(), "last_calculated_at"])


async def _new_trials_for_day(day: date) -> int:
    start_dt, end_dt = _day_bounds_utc(day)
    return await Users.filter(
        used_trial=True,
        trial_started_at__gte=start_dt,
        trial_started_at__lt=end_dt,
    ).count()


async def _active_trials_for_day(day: date) -> int:
    _, end_dt = _day_bounds_utc(day)
    return await Users.filter(
        is_trial=True,
        used_trial=True,
        trial_started_at__lt=end_dt,
        expired_at__gte=day,
    ).count()


async def _upsert_trial_flag(
    *,
    user_id: int,
    day: date,
    traffic_gb: float,
    share_pct: float,
    reason: str,
    severity: str,
    send_alert: bool,
) -> bool:
    row = await AnalyticsTrialRiskFlags.get_or_none(
        user_id=user_id,
        day=day,
        reason=reason,
    )
    created = False
    if row is None:
        try:
            row = await AnalyticsTrialRiskFlags.create(
                user_id=user_id,
                day=day,
                traffic_gb=traffic_gb,
                share_pct=share_pct,
                reason=reason,
                severity=severity,
                status=TRIAL_FLAG_STATUS_NEW,
            )
            created = True
        except IntegrityError:
            row = await AnalyticsTrialRiskFlags.get(
                user_id=user_id,
                day=day,
                reason=reason,
            )
    else:
        row.traffic_gb = traffic_gb
        row.share_pct = share_pct
        row.severity = severity
        await row.save(update_fields=["traffic_gb", "share_pct", "severity", "updated_at"])

    if created and send_alert:
        try:
            from bloobcat.bot.notifications.admin import send_admin_message

            await send_admin_message(
                "⚠️ Trial traffic risk\n"
                f"User: <code>{user_id}</code>\n"
                f"Date: {day.isoformat()}\n"
                f"Traffic: {traffic_gb:.2f} GB\n"
                f"Share: {share_pct:.1f}%\n"
                f"Reason: {reason}\n"
                f"Severity: {severity}",
            )
        except Exception as exc:
            logger.warning("Failed to send trial abuse alert: {}", exc)
    return created


async def _refresh_trial_daily(
    *,
    start_day: date,
    end_day: date,
    trial_by_user: dict[date, dict[int, int]],
    send_alerts: bool,
) -> None:
    settings = await _read_trial_abuse_settings()
    current = start_day
    while current <= end_day:
        by_user = trial_by_user.get(current, {})
        traffic_bytes = sum(by_user.values())
        traffic_gb = traffic_bytes / BYTES_IN_GB if traffic_bytes > 0 else 0.0
        top_user_id = None
        top_user_bytes = 0
        if by_user:
            top_user_id, top_user_bytes = max(by_user.items(), key=lambda item: item[1])
        top_user_gb = top_user_bytes / BYTES_IN_GB if top_user_bytes > 0 else 0.0

        for user_id, user_bytes in by_user.items():
            user_gb = user_bytes / BYTES_IN_GB
            share_pct = (user_bytes / traffic_bytes * 100.0) if traffic_bytes > 0 else 0.0
            severity = None
            reason = None
            if settings.critical_gb > 0 and user_gb >= settings.critical_gb:
                severity = "critical"
                reason = "daily_traffic_critical"
            elif settings.warning_gb > 0 and user_gb >= settings.warning_gb:
                severity = "warning"
                reason = "daily_traffic_warning"
            if (
                settings.spike_min_gb > 0
                and user_gb >= settings.spike_min_gb
                and share_pct >= settings.spike_share_pct
            ):
                severity = "critical" if severity == "critical" else "warning"
                reason = "trial_traffic_share_spike"
            if severity and reason:
                await _upsert_trial_flag(
                    user_id=int(user_id),
                    day=current,
                    traffic_gb=user_gb,
                    share_pct=share_pct,
                    reason=reason,
                    severity=severity,
                    send_alert=send_alerts,
                )

        flagged_count = await AnalyticsTrialRiskFlags.filter(
            day=current,
            status=TRIAL_FLAG_STATUS_NEW,
        ).count()
        payload = {
            "new_trials": await _new_trials_for_day(current),
            "active_trials": await _active_trials_for_day(current),
            "traffic_bytes": traffic_bytes,
            "traffic_gb": traffic_gb,
            "top_user_id": top_user_id,
            "top_user_traffic_gb": top_user_gb,
            "flagged_users_count": flagged_count,
        }
        row = await AnalyticsTrialDaily.get_or_none(day=current)
        if row is None:
            try:
                await AnalyticsTrialDaily.create(day=current, **payload)
            except IntegrityError:
                row = await AnalyticsTrialDaily.get(day=current)
                for key, value in payload.items():
                    setattr(row, key, value)
                await row.save(update_fields=[*payload.keys(), "last_calculated_at"])
        else:
            for key, value in payload.items():
                setattr(row, key, value)
            await row.save(update_fields=[*payload.keys(), "last_calculated_at"])
        current += timedelta(days=1)


async def collect_service_growth_analytics_once(
    *,
    lookback_hours: int = 48,
    reconcile_days: int | None = None,
    client: RemnaWaveClient | None = None,
    send_alerts: bool = True,
) -> dict[str, Any]:
    if _collector_lock.locked():
        return {"status": "skipped", "reason": "collector_already_running"}

    async with _collector_lock:
        now_utc = datetime.now(timezone.utc)
        now_msk = now_utc.astimezone(MSK_TZ)
        if reconcile_days is not None:
            start_day = now_msk.date() - timedelta(days=max(1, int(reconcile_days)) - 1)
        else:
            start_day = (
                now_msk - timedelta(hours=max(1, int(lookback_hours)))
            ).date()
        end_day = now_msk.date()

        await sync_payment_events_once(lookback_days=max(14, (end_day - start_day).days + 2))

        owns_client = client is None
        if client is None:
            client = RemnaWaveClient(
                remnawave_settings.url,
                remnawave_settings.token.get_secret_value(),
            )

        try:
            total_by_product, trial_by_product, trial_by_user = await _fetch_remnawave_usage(
                client=client,
                start_day=start_day,
                end_dt=now_utc,
                timeout_seconds=float(
                    getattr(app_settings, "analytics_remnawave_timeout_seconds", 30)
                    or 30
                ),
            )
        finally:
            if owns_client:
                await client.close()

        payments = await _payment_buckets(start_day, end_day)
        current = start_day
        while current <= end_day:
            paid_main_bytes = max(
                0,
                total_by_product.get((current, PRODUCT_MAIN_PAID), 0)
                - trial_by_product.get((current, PRODUCT_MAIN_PAID), 0),
            )
            paid_lte_bytes = max(
                0,
                total_by_product.get((current, PRODUCT_LTE_PAID), 0)
                - trial_by_product.get((current, PRODUCT_LTE_PAID), 0),
            )
            await _upsert_service_daily(
                day=current,
                product=PRODUCT_MAIN_PAID,
                traffic_bytes=paid_main_bytes,
                payments=payments.get((current, PRODUCT_MAIN_PAID), PaymentBuckets()),
            )
            await _upsert_service_daily(
                day=current,
                product=PRODUCT_LTE_PAID,
                traffic_bytes=paid_lte_bytes,
                payments=payments.get((current, PRODUCT_LTE_PAID), PaymentBuckets()),
            )
            await _upsert_service_daily(
                day=current,
                product=PRODUCT_ALL_PAID,
                traffic_bytes=paid_main_bytes + paid_lte_bytes,
                payments=payments.get((current, PRODUCT_ALL_PAID), PaymentBuckets()),
            )
            current += timedelta(days=1)

        await _refresh_trial_daily(
            start_day=start_day,
            end_day=end_day,
            trial_by_user=trial_by_user,
            send_alerts=send_alerts,
        )

        return {
            "status": "ok",
            "start_day": start_day.isoformat(),
            "end_day": end_day.isoformat(),
        }


async def run_service_growth_analytics_scheduler(interval_seconds: int | None = None) -> None:
    global _last_nightly_reconcile_day
    paid_interval = int(getattr(app_settings, "analytics_paid_interval_seconds", 3600) or 3600)
    trial_interval = int(getattr(app_settings, "analytics_trial_interval_seconds", 1800) or 1800)
    interval = int(interval_seconds if interval_seconds is not None else min(paid_interval, trial_interval))
    interval = max(300, interval)
    logger.info("Starting service-growth analytics scheduler (interval: {}s)", interval)
    while True:
        try:
            now_msk = datetime.now(MSK_TZ)
            reconcile_hour = int(
                getattr(app_settings, "analytics_nightly_reconcile_hour_msk", 3) or 3
            )
            reconcile_days = None
            if (
                now_msk.hour == reconcile_hour
                and _last_nightly_reconcile_day != now_msk.date()
            ):
                reconcile_days = 7
                _last_nightly_reconcile_day = now_msk.date()
            result = await collect_service_growth_analytics_once(
                reconcile_days=reconcile_days,
            )
            logger.info("Service-growth analytics collector result: {}", result)
        except Exception:
            logger.exception("Service-growth analytics scheduler iteration failed")
        await asyncio.sleep(interval)
