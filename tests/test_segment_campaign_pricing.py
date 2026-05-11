"""Сегментная акция реально влияет на цену в quote/plans/pay.

Покрываем:
1. /subscription/plans — карточки тарифов получают пониженную цену и
   поля campaignDiscountRub / campaignDiscountPercent / campaignSlug,
   когда у пользователя активна подходящая кампания.
2. /subscription/plans — кампания применяется только к тем длительностям,
   которые перечислены в applies_to_months. Прочие остаются как раньше.
3. /subscription/quote — возвращает discountedSubscriptionPriceRub с
   учётом кампании, totalPriceRub становится меньше, и в ответе
   появляются campaignDiscountRub/Percent/Slug.
4. Кампания не применяется, если пользователь не в её сегменте.
5. Кампания не «съедает» персональную скидку: если личная скидка
   больше — побеждает личная, кампания обнуляется в ответе.
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
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover
    from _payment_test_stubs import install_stubs

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover
    from _sqlite_datetime_compat import register_sqlite_datetime_compat


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore_stubs = install_stubs()
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
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.subscription_freezes",
                        "bloobcat.db.promotions",
                        "bloobcat.db.admins",
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


async def _seed_standard_tariffs() -> None:
    from bloobcat.db.tariff import Tariffs

    await Tariffs.create(
        id=101,
        name="base_1m",
        months=1,
        base_price=290,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=290,
    )
    await Tariffs.create(
        id=103,
        name="base_3m",
        months=3,
        base_price=290,
        progressive_multiplier=0.9,
        order=2,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=749,
    )
    await Tariffs.create(
        id=112,
        name="base_12m",
        months=12,
        base_price=2190,
        progressive_multiplier=0.9,
        order=4,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=2190,
    )


async def _create_campaign(**overrides):
    from bloobcat.db.segment_campaigns import SegmentCampaign

    now = datetime.now(timezone.utc)
    payload = dict(
        slug="default-test",
        title="Тест-акция",
        subtitle="Только три дня",
        segment="everyone",
        discount_percent=20,
        applies_to_months=[],
        accent="gold",
        cta_label="Активировать",
        cta_target="builder",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=3),
        priority=0,
        is_active=True,
    )
    payload.update(overrides)
    return await SegmentCampaign.create(**payload)


async def _build_app_for_user(user_id: int) -> FastAPI:
    from bloobcat.db.users import Users
    from bloobcat.routes import subscription as subscription_module

    app = FastAPI()
    app.include_router(subscription_module.router)

    async def _override_validate() -> Users:
        return await Users.get(id=user_id)

    app.dependency_overrides[subscription_module.validate] = _override_validate
    return app


def _plans_by_id(payload: list[dict]) -> dict[str, dict]:
    return {plan["id"]: plan for plan in payload}


@pytest.mark.asyncio
async def test_subscription_plans_apply_campaign_discount_to_matching_months():
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9301,
        username="plans-campaign",
        full_name="Plans Campaign",
        is_registered=True,
    )
    await _create_campaign(
        slug="first-buy-1m-99",
        segment="no_purchase_yet",
        discount_percent=66,
        applies_to_months=[1],
        priority=50,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())

    # 290 * (1 - 0.66) = 98.6 → округление до 99
    assert plans["1month"]["priceRub"] == 99
    assert plans["1month"]["originalPriceRub"] == 290
    assert plans["1month"]["campaignDiscountPercent"] == 66
    assert plans["1month"]["campaignDiscountRub"] == 191
    assert plans["1month"]["campaignSlug"] == "first-buy-1m-99"
    # personalDiscountPercent должен быть None: личной скидки нет,
    # а кампанию мы отдаём отдельным полем.
    assert plans["1month"]["personalDiscountPercent"] is None

    # 3m и 12m не входят в applies_to_months → цены прежние
    assert plans["3months"]["priceRub"] == 749
    assert plans["3months"]["campaignDiscountPercent"] is None
    assert plans["3months"]["campaignSlug"] is None
    assert plans["12months"]["priceRub"] == 2190
    assert plans["12months"]["campaignDiscountPercent"] is None


@pytest.mark.asyncio
async def test_subscription_plans_skip_campaign_for_wrong_segment():
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9302,
        username="plans-wrong-segment",
        full_name="Plans Wrong Segment",
        is_registered=True,
    )
    # Кампания только для loyal_renewer, а пользователь — no_purchase_yet.
    await _create_campaign(
        slug="loyal-only-30",
        segment="loyal_renewer",
        discount_percent=30,
        applies_to_months=[1, 3, 12],
        priority=50,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())
    assert plans["1month"]["priceRub"] == 290
    assert plans["1month"]["campaignDiscountPercent"] is None
    assert plans["3months"]["priceRub"] == 749
    assert plans["3months"]["campaignDiscountPercent"] is None


@pytest.mark.asyncio
async def test_subscription_quote_endpoint_reflects_campaign_discount():
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9303,
        username="quote-campaign",
        full_name="Quote Campaign",
        is_registered=True,
    )
    await _create_campaign(
        slug="welcome-50",
        segment="no_purchase_yet",
        discount_percent=50,
        applies_to_months=[1],
        priority=10,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/subscription/quote",
            json={"tariffId": 101, "deviceCount": 1, "lteGb": 0},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["subscriptionPriceRub"] == 290
    assert body["discountedSubscriptionPriceRub"] == 145
    assert body["discountRub"] == 145
    assert body["discountPercent"] == 50
    assert body["totalPriceRub"] == 145
    assert body["campaignDiscountRub"] == 145
    assert body["campaignDiscountPercent"] == 50
    assert body["campaignSlug"] == "welcome-50"


@pytest.mark.asyncio
async def test_campaign_loses_to_larger_personal_discount():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9304,
        username="plans-best-discount",
        full_name="Plans Best Discount",
        is_registered=True,
    )
    await PersonalDiscount.create(
        user_id=user.id,
        percent=60,
        is_permanent=True,
        remaining_uses=1,
        source="loyalty",
    )
    await _create_campaign(
        slug="welcome-20",
        segment="no_purchase_yet",
        discount_percent=20,
        applies_to_months=[1],
        priority=10,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())
    # 290 * 0.4 = 116 — личная скидка 60% побеждает кампанию 20%.
    assert plans["1month"]["priceRub"] == 116
    assert plans["1month"]["personalDiscountPercent"] == 60
    assert plans["1month"]["campaignDiscountPercent"] is None
    assert plans["1month"]["campaignSlug"] is None


@pytest.mark.asyncio
async def test_campaign_beats_smaller_personal_discount_and_does_not_consume_it():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9305,
        username="plans-campaign-wins",
        full_name="Plans Campaign Wins",
        is_registered=True,
    )
    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
        source="promo",
    )
    await _create_campaign(
        slug="welcome-40",
        segment="no_purchase_yet",
        discount_percent=40,
        applies_to_months=[1],
        priority=10,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())
    # 290 * 0.6 = 174 — кампания 40% побеждает личную 10%.
    assert plans["1month"]["priceRub"] == 174
    assert plans["1month"]["campaignDiscountPercent"] == 40
    assert plans["1month"]["campaignSlug"] == "welcome-40"
    # Личная скидка отдаётся как None, чтобы фронт не показывал её
    # одновременно с акцией.
    assert plans["1month"]["personalDiscountPercent"] is None

    # Чтение тарифов не должно тратить разовую персональную скидку.
    discount_after = await PersonalDiscount.get(id=discount.id)
    assert int(discount_after.remaining_uses or 0) == 1
