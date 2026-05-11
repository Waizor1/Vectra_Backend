"""Покрытие сегментной акционной системы.

Проверяем:
1. Сегмент-резолвер корректно классифицирует пользователей.
2. Эндпоинт `/subscription/campaigns/active` отдаёт правильную кампанию
   и null, когда подходящих нет.
3. При нескольких активных кампаниях побеждает priority + ближайший дедлайн.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from tortoise import Tortoise


try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover
    from _sqlite_datetime_compat import register_sqlite_datetime_compat


@pytest_asyncio.fixture(autouse=True)
async def _segments_db():
    register_sqlite_datetime_compat()
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.subscription_freezes",
                        "bloobcat.db.segment_campaigns",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    from bloobcat.db.users import Users

    had_active_tariff_fk = "active_tariff" in Users._meta.fk_fields
    fk = Users._meta.fields_map.get("active_tariff")
    original_reference = None
    original_db_constraint = None

    Users._meta.fk_fields.discard("active_tariff")
    if fk is not None:
        original_reference = fk.reference
        original_db_constraint = fk.db_constraint
        fk.reference = False
        fk.db_constraint = False

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection("default")
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe = generator._get_models_to_create(models_to_create)
        if maybe is not None:
            models_to_create = maybe
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m2m for t in tables for m2m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)

    try:
        yield
    finally:
        if had_active_tariff_fk:
            Users._meta.fk_fields.add("active_tariff")
        if fk is not None:
            if original_reference is not None:
                fk.reference = original_reference
            if original_db_constraint is not None:
                fk.db_constraint = original_db_constraint
        await Tortoise.close_connections()


async def _build_app(user_id: int) -> FastAPI:
    from bloobcat.db.users import Users
    from bloobcat.routes import subscription as subscription_module

    app = FastAPI()
    app.include_router(subscription_module.router)

    async def _override_validate() -> Users:
        return await Users.get(id=user_id)

    app.dependency_overrides[subscription_module.validate] = _override_validate
    return app


async def _create_user(**kwargs):
    from bloobcat.db.users import Users

    base = dict(
        username=f"u{kwargs.get('id', 1)}",
        full_name="Test User",
        is_registered=True,
    )
    base.update(kwargs)
    return await Users.create(**base)


async def _create_succeeded_payment(user_id: int, amount_external: int = 290) -> None:
    from bloobcat.db.payments import ProcessedPayments

    await ProcessedPayments.create(
        payment_id=f"pmt_{user_id}_{amount_external}",
        provider="platega",
        user_id=user_id,
        amount=Decimal(amount_external),
        amount_external=Decimal(amount_external),
        amount_from_balance=Decimal(0),
        status="succeeded",
        processing_state="completed",
        effect_applied=True,
    )


async def _create_campaign(**overrides):
    from bloobcat.db.segment_campaigns import SegmentCampaign

    now = datetime.now(timezone.utc)
    payload = dict(
        slug="default",
        title="Тест-акция",
        subtitle="Только три дня",
        segment="everyone",
        discount_percent=20,
        applies_to_months=[],
        accent="gold",
        cta_label="Оформить",
        cta_target="builder",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=3),
        priority=0,
        is_active=True,
    )
    payload.update(overrides)
    return await SegmentCampaign.create(**payload)


async def _create_tariff(**overrides):
    from bloobcat.db.tariff import Tariffs

    payload = dict(
        id=1,
        name="1 месяц",
        months=1,
        base_price=150,
        progressive_multiplier=0.95,
        order=1,
        is_active=True,
        devices_limit_default=1,
        devices_limit_family=30,
        final_price_default=150,
        final_price_family=2700,
        lte_enabled=True,
        lte_price_per_gb=1.5,
        lte_min_gb=0,
        lte_max_gb=500,
        lte_step_gb=1,
    )
    payload.update(overrides)
    return await Tariffs.create(**payload)


# ---------------------------------------------------------------- segments


@pytest.mark.asyncio
async def test_resolver_no_purchase_yet_for_fresh_user():
    from bloobcat.services.segment_campaigns import resolve_user_segments

    user = await _create_user(id=701, is_subscribed=False, is_trial=False)
    segments = await resolve_user_segments(user)

    assert "everyone" in segments
    assert "no_purchase_yet" in segments
    assert "trial_active" not in segments
    assert "lapsed" not in segments
    assert "loyal_renewer" not in segments


@pytest.mark.asyncio
async def test_resolver_trial_active_for_unpaid_trial_user():
    from bloobcat.services.segment_campaigns import resolve_user_segments

    user = await _create_user(id=702, is_subscribed=True, is_trial=True)
    segments = await resolve_user_segments(user)

    assert "trial_active" in segments
    assert "no_purchase_yet" not in segments


@pytest.mark.asyncio
async def test_resolver_lapsed_after_grace_period():
    from bloobcat.services.segment_campaigns import resolve_user_segments

    user = await _create_user(
        id=703,
        is_subscribed=False,
        expired_at=(datetime.now(timezone.utc) - timedelta(days=30)).date(),
    )
    await _create_succeeded_payment(user.id, amount_external=290)
    segments = await resolve_user_segments(user)

    assert "lapsed" in segments
    assert "no_purchase_yet" not in segments


@pytest.mark.asyncio
async def test_resolver_loyal_renewer_after_two_payments():
    from bloobcat.services.segment_campaigns import resolve_user_segments

    user = await _create_user(
        id=704,
        is_subscribed=True,
        expired_at=(datetime.now(timezone.utc) + timedelta(days=10)).date(),
    )
    await _create_succeeded_payment(user.id, amount_external=290)
    await _create_succeeded_payment(user.id, amount_external=749)
    segments = await resolve_user_segments(user)

    assert "loyal_renewer" in segments
    assert "lapsed" not in segments


@pytest.mark.asyncio
async def test_resolver_treats_balance_only_payments_as_unpaid():
    """Списание только с бонусного баланса не считается «первой покупкой».

    Это нужно, чтобы пользователи с реферальными бонусами всё ещё
    видели акцию для первой покупки.
    """
    from bloobcat.services.segment_campaigns import resolve_user_segments
    from bloobcat.db.payments import ProcessedPayments

    user = await _create_user(id=705, is_subscribed=False, is_trial=False)
    await ProcessedPayments.create(
        payment_id=f"balance_only_{user.id}",
        provider="platega",
        user_id=user.id,
        amount=Decimal(290),
        amount_external=Decimal(0),
        amount_from_balance=Decimal(290),
        status="succeeded",
        processing_state="completed",
        effect_applied=True,
    )
    segments = await resolve_user_segments(user)

    assert "no_purchase_yet" in segments


# ---------------------------------------------------------------- endpoint


@pytest.mark.asyncio
async def test_campaigns_active_returns_no_purchase_yet_for_new_user():
    user = await _create_user(id=801, is_subscribed=False)
    await _create_campaign(
        slug="welcome-first-buy",
        title="Первая покупка −25%",
        segment="no_purchase_yet",
        discount_percent=25,
        applies_to_months=[3, 6, 12],
        accent="cyan",
        priority=20,
    )
    await _create_campaign(
        slug="loyalty",
        title="Постоянным клиентам −10%",
        segment="loyal_renewer",
        discount_percent=10,
        priority=10,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/campaigns/active")

    assert response.status_code == 200
    payload = response.json()
    assert "no_purchase_yet" in payload["segments"]
    assert payload["campaign"] is not None
    assert payload["campaign"]["slug"] == "welcome-first-buy"
    assert payload["campaign"]["discountPercent"] == 25
    assert payload["campaign"]["appliesToMonths"] == [3, 6, 12]
    assert payload["campaign"]["accent"] == "cyan"
    assert isinstance(payload["campaign"]["endsAtMs"], int)
    assert isinstance(payload["serverNowMs"], int)


@pytest.mark.asyncio
async def test_campaigns_active_returns_null_when_user_segment_not_targeted():
    user = await _create_user(id=802, is_subscribed=False)
    await _create_campaign(
        slug="loyalty-only",
        title="Постоянным −10%",
        segment="loyal_renewer",
        discount_percent=10,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/campaigns/active")

    assert response.status_code == 200
    payload = response.json()
    assert payload["campaign"] is None


@pytest.mark.asyncio
async def test_campaigns_active_skips_inactive_or_expired_window():
    user = await _create_user(id=803, is_subscribed=False)
    now = datetime.now(timezone.utc)
    await _create_campaign(
        slug="expired",
        title="Истекшая",
        segment="everyone",
        discount_percent=30,
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
    )
    await _create_campaign(
        slug="paused",
        title="Выключенная",
        segment="everyone",
        discount_percent=40,
        is_active=False,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/campaigns/active")

    assert response.status_code == 200
    assert response.json()["campaign"] is None


@pytest.mark.asyncio
async def test_campaigns_active_picks_highest_priority_when_multiple_match():
    user = await _create_user(id=804, is_subscribed=False)
    await _create_campaign(
        slug="generic",
        title="Базовая",
        segment="everyone",
        discount_percent=10,
        priority=1,
    )
    await _create_campaign(
        slug="newcomer",
        title="Новичкам −30%",
        segment="no_purchase_yet",
        discount_percent=30,
        priority=50,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/campaigns/active")

    assert response.status_code == 200
    payload = response.json()
    assert payload["campaign"]["slug"] == "newcomer"
    assert payload["campaign"]["discountPercent"] == 30


@pytest.mark.asyncio
async def test_segment_campaign_discount_affects_subscription_quote():
    user = await _create_user(id=805, is_subscribed=False, is_trial=False)
    tariff = await _create_tariff(base_price=150, final_price_default=150)
    await _create_campaign(
        slug="first-users-99",
        title="Старт за 99 ₽",
        segment="no_purchase_yet",
        discount_percent=34,
        applies_to_months=[],
        priority=100,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 1, "lteGb": 0},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subscriptionPriceRub"] == 150
    assert payload["discountedSubscriptionPriceRub"] == 99
    assert payload["totalPriceRub"] == 99
    assert payload["discountRub"] == 51
    assert payload["discountPercent"] == 34
    assert payload["discountSource"] == "segment_campaign"
    assert payload["discountCampaignSlug"] == "first-users-99"


@pytest.mark.asyncio
async def test_segment_campaign_discount_respects_tariff_month_filter():
    user = await _create_user(id=806, is_subscribed=False, is_trial=False)
    tariff = await _create_tariff(
        id=3,
        name="3 месяца",
        months=3,
        base_price=399,
        final_price_default=399,
    )
    await _create_campaign(
        slug="one-month-only",
        title="Только 1 месяц",
        segment="no_purchase_yet",
        discount_percent=34,
        applies_to_months=[1],
        priority=100,
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 1, "lteGb": 0},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subscriptionPriceRub"] == 399
    assert payload["discountedSubscriptionPriceRub"] == 399
    assert payload["discountPercent"] == 0
    assert payload["discountSource"] is None


@pytest.mark.asyncio
async def test_personal_discount_wins_when_better_than_segment_campaign():
    from bloobcat.db.discounts import PersonalDiscount

    user = await _create_user(id=807, is_subscribed=False, is_trial=False)
    tariff = await _create_tariff(base_price=150, final_price_default=150)
    await _create_campaign(
        slug="first-users-10",
        title="Новичкам −10%",
        segment="no_purchase_yet",
        discount_percent=10,
        applies_to_months=[],
        priority=100,
    )
    personal = await PersonalDiscount.create(
        user_id=user.id,
        percent=50,
        is_permanent=False,
        remaining_uses=1,
        source="promo",
    )

    app = await _build_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 1, "lteGb": 0},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["discountedSubscriptionPriceRub"] == 75
    assert payload["discountPercent"] == 50
    assert payload["discountSource"] == "personal_discount"
    assert payload["discountCampaignSlug"] is None

    await personal.refresh_from_db()
    assert personal.remaining_uses == 1
