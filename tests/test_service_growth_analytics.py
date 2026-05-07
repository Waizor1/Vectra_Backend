from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._payment_test_stubs import install_stubs
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _payment_test_stubs import install_stubs
    from _sqlite_datetime_compat import register_sqlite_datetime_compat


BYTES_IN_GB = 1024**3


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore_stubs = install_stubs()
    notifications_pkg = sys.modules.get("bloobcat.bot.notifications")
    if notifications_pkg is not None:
        notifications_pkg.__path__ = [
            str(
                Path(__file__).resolve().parents[1]
                / "bloobcat"
                / "bot"
                / "notifications"
            )
        ]
    try:
        yield
    finally:
        restore_stubs()


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    register_sqlite_datetime_compat()
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.payments",
                        "bloobcat.db.notifications",
                        "bloobcat.db.analytics",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection("default")
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe_models = generator._get_models_to_create(models_to_create)
        if maybe_models is not None:
            models_to_create = maybe_models
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


class _FakeNodesApi:
    def __init__(self, items_by_node: dict[str, list[dict]]) -> None:
        self.items_by_node = items_by_node

    async def get_nodes(self):
        return {
            "response": [
                {"uuid": "node-main", "name": "MAIN-DE"},
                {"uuid": "node-lte", "name": "CHTF-LTE"},
            ]
        }

    async def get_node_user_usage_by_range(self, uuid_: str, start: str, end: str):
        assert start
        assert end
        return {"response": self.items_by_node.get(uuid_, [])}


class _FakeRemnaClient:
    def __init__(self, items_by_node: dict[str, list[dict]]) -> None:
        self.nodes = _FakeNodesApi(items_by_node)
        self.closed = False

    async def close(self):
        self.closed = True


async def _create_user(user_id: int, **kwargs):
    from bloobcat.db.users import Users

    defaults = {
        "username": f"user{user_id}",
        "full_name": f"User {user_id}",
        "is_registered": True,
    }
    defaults.update(kwargs)
    return await Users.create(id=user_id, **defaults)


async def _create_succeeded_payment(payment_id: str, user_id: int, metadata: dict):
    from bloobcat.db.payments import ProcessedPayments

    return await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=100,
        amount_external=80,
        amount_from_balance=20,
        status="succeeded",
        effect_applied=True,
        provider_payload=json.dumps({"metadata": metadata}),
        processed_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_payment_events_split_subscription_and_lte_revenue():
    from bloobcat.db.analytics import AnalyticsPaymentEvents
    from bloobcat.tasks.service_growth_analytics import sync_payment_events_once

    await _create_user(7101)
    await _create_succeeded_payment(
        "pay_sub_lte_1",
        7101,
        {
            "month": 1,
            "device_count": 3,
            "tariff_kind": "base",
            "lte_gb": 5,
            "lte_cost": 15,
        },
    )

    changed = await sync_payment_events_once()

    assert changed == 1
    event = await AnalyticsPaymentEvents.get(payment_id="pay_sub_lte_1")
    assert event.kind == "subscription"
    assert float(event.subscription_revenue_rub) == 85.0
    assert float(event.lte_revenue_rub) == 15.0
    assert event.lte_gb_purchased == 5


@pytest.mark.asyncio
async def test_collector_excludes_trial_traffic_from_paid_totals_and_flags_top_user(
    monkeypatch,
):
    from bloobcat.db.analytics import (
        AnalyticsServiceDaily,
        AnalyticsTrialDaily,
        AnalyticsTrialRiskFlags,
    )
    from bloobcat.tasks import service_growth_analytics as analytics

    monkeypatch.setattr(analytics.remnawave_settings, "lte_node_marker", "CHTF")

    today = datetime.now(analytics.MSK_TZ).date()
    trial_uuid = uuid.uuid4()
    paid_uuid = uuid.uuid4()
    await _create_user(
        7201,
        is_trial=True,
        used_trial=True,
        remnawave_uuid=trial_uuid,
        trial_started_at=datetime.now(timezone.utc),
        expired_at=today + timedelta(days=7),
    )
    await _create_user(7202, remnawave_uuid=paid_uuid, expired_at=today + timedelta(days=30))
    await _create_user(
        7203,
        is_trial=False,
        used_trial=True,
        trial_started_at=datetime.now(timezone.utc),
        expired_at=today + timedelta(days=30),
    )
    await _create_succeeded_payment(
        "pay_growth_1",
        7202,
        {
            "month": 1,
            "device_count": 2,
            "tariff_kind": "base",
            "lte_gb": 5,
            "lte_cost": 10,
        },
    )

    fake_client = _FakeRemnaClient(
        {
            "node-main": [
                {
                    "date": today.isoformat(),
                    "userUuid": str(paid_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 8 * BYTES_IN_GB,
                },
                {
                    "date": today.isoformat(),
                    "userUuid": str(trial_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 2 * BYTES_IN_GB,
                },
            ],
            "node-lte": [
                {
                    "date": today.isoformat(),
                    "userUuid": str(paid_uuid),
                    "nodeName": "CHTF-LTE",
                    "total": 4 * BYTES_IN_GB,
                },
                {
                    "date": today.isoformat(),
                    "userUuid": str(trial_uuid),
                    "nodeName": "CHTF-LTE",
                    "total": 1 * BYTES_IN_GB,
                },
            ],
        }
    )

    result = await analytics.collect_service_growth_analytics_once(
        client=fake_client,
        send_alerts=False,
    )

    assert result["status"] == "ok"
    main = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_MAIN_PAID)
    lte = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_LTE_PAID)
    all_paid = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_ALL_PAID)
    assert main.traffic_gb == 8.0
    assert lte.traffic_gb == 4.0
    assert all_paid.traffic_gb == 12.0
    assert float(main.subscription_revenue_rub) == 90.0
    assert float(main.lte_revenue_rub) == 0.0
    assert float(main.amount_external_rub) == 72.0
    assert float(main.amount_from_balance_rub) == 18.0
    assert float(lte.subscription_revenue_rub) == 0.0
    assert float(lte.lte_revenue_rub) == 10.0
    assert float(lte.amount_external_rub) == 8.0
    assert float(lte.amount_from_balance_rub) == 2.0
    assert lte.lte_gb_purchased == 5.0
    assert float(all_paid.subscription_revenue_rub) == 90.0
    assert float(all_paid.lte_revenue_rub) == 10.0

    trial_daily = await AnalyticsTrialDaily.get(day=today)
    assert trial_daily.new_trials == 2
    assert trial_daily.active_trials == 1
    assert trial_daily.traffic_gb == 3.0
    assert trial_daily.top_user_id == 7201
    assert trial_daily.top_user_traffic_gb == 3.0

    flags = await AnalyticsTrialRiskFlags.filter(user_id=7201, day=today)
    assert len(flags) == 1
    assert flags[0].reason == "trial_traffic_share_spike"
    assert flags[0].status == "new"


@pytest.mark.asyncio
async def test_collector_is_idempotent_for_payment_events_and_trial_flags(monkeypatch):
    from bloobcat.db.analytics import AnalyticsPaymentEvents, AnalyticsTrialRiskFlags
    from bloobcat.tasks import service_growth_analytics as analytics

    monkeypatch.setattr(analytics.remnawave_settings, "lte_node_marker", "CHTF")

    today = datetime.now(analytics.MSK_TZ).date()
    trial_uuid = uuid.uuid4()
    await _create_user(
        7301,
        is_trial=True,
        used_trial=True,
        remnawave_uuid=trial_uuid,
        trial_started_at=datetime.now(timezone.utc),
        expired_at=today + timedelta(days=7),
    )
    await _create_succeeded_payment("pay_growth_idempotent", 7301, {"lte_topup": True, "lte_gb_delta": 3})
    fake_client = _FakeRemnaClient(
        {
            "node-main": [
                {
                    "date": today.isoformat(),
                    "userUuid": str(trial_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 11 * BYTES_IN_GB,
                }
            ]
        }
    )

    await analytics.collect_service_growth_analytics_once(client=fake_client, send_alerts=False)
    await analytics.collect_service_growth_analytics_once(client=fake_client, send_alerts=False)

    assert await AnalyticsPaymentEvents.filter(payment_id="pay_growth_idempotent").count() == 1
    assert await AnalyticsTrialRiskFlags.filter(user_id=7301, day=today).count() == 1


@pytest.mark.asyncio
async def test_collector_excludes_foreign_tenant_traffic_from_shared_panel(monkeypatch):
    from bloobcat.db.analytics import AnalyticsServiceDaily, AnalyticsTrialDaily
    from bloobcat.tasks import service_growth_analytics as analytics

    monkeypatch.setattr(analytics.remnawave_settings, "lte_node_marker", "CHTF")

    today = datetime.now(analytics.MSK_TZ).date()
    vectra_paid_uuid = uuid.uuid4()
    vectra_trial_uuid = uuid.uuid4()
    foreign_uuid = uuid.uuid4()  # belongs to another tenant on the same panel

    await _create_user(
        7401,
        is_trial=True,
        used_trial=True,
        remnawave_uuid=vectra_trial_uuid,
        trial_started_at=datetime.now(timezone.utc),
        expired_at=today + timedelta(days=7),
    )
    await _create_user(
        7402,
        remnawave_uuid=vectra_paid_uuid,
        expired_at=today + timedelta(days=30),
    )
    # Foreign user is intentionally NOT created in Users.

    fake_client = _FakeRemnaClient(
        {
            "node-main": [
                {
                    "date": today.isoformat(),
                    "userUuid": str(vectra_paid_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 5 * BYTES_IN_GB,
                },
                {
                    "date": today.isoformat(),
                    "userUuid": str(vectra_trial_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 2 * BYTES_IN_GB,
                },
                {
                    "date": today.isoformat(),
                    "userUuid": str(foreign_uuid),
                    "nodeName": "MAIN-DE",
                    "total": 100 * BYTES_IN_GB,  # huge foreign-tenant traffic
                },
            ],
            "node-lte": [
                {
                    "date": today.isoformat(),
                    "userUuid": str(vectra_paid_uuid),
                    "nodeName": "CHTF-LTE",
                    "total": 3 * BYTES_IN_GB,
                },
                {
                    "date": today.isoformat(),
                    "userUuid": str(foreign_uuid),
                    "nodeName": "CHTF-LTE",
                    "total": 50 * BYTES_IN_GB,
                },
            ],
        }
    )

    result = await analytics.collect_service_growth_analytics_once(
        client=fake_client,
        send_alerts=False,
    )
    assert result["status"] == "ok"

    main = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_MAIN_PAID)
    lte = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_LTE_PAID)
    all_paid = await AnalyticsServiceDaily.get(day=today, product=analytics.PRODUCT_ALL_PAID)
    # Vectra paid main = 5 GB only (foreign 100 GB excluded; trial 2 GB excluded
    # from paid totals via existing trial subtraction).
    assert main.traffic_gb == 5.0
    # Vectra paid LTE = 3 GB only (foreign 50 GB excluded).
    assert lte.traffic_gb == 3.0
    assert all_paid.traffic_gb == 8.0

    trial_daily = await AnalyticsTrialDaily.get(day=today)
    # Trial bytes = 2 GB (foreign tenant did not contaminate trial counters either).
    assert trial_daily.traffic_gb == 2.0
    assert trial_daily.top_user_id == 7401
    assert trial_daily.top_user_traffic_gb == 2.0
