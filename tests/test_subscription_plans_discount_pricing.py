from __future__ import annotations

import hashlib
import hmac
from datetime import date

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from tortoise import Tortoise

try:
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _payment_test_stubs import install_stubs

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
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
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

    from bloobcat.db.users import Users

    had_active_tariff_fk = "active_tariff" in Users._meta.fk_fields
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    original_active_tariff_reference = None
    original_active_tariff_db_constraint = None

    Users._meta.fk_fields.discard("active_tariff")
    if users_active_tariff_fk is not None:
        original_active_tariff_reference = users_active_tariff_fk.reference
        original_active_tariff_db_constraint = users_active_tariff_fk.db_constraint
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

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
        [table["table_creation_string"] for table in tables]
        + [m2m for table in tables for m2m in table["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)

    try:
        yield
    finally:
        if had_active_tariff_fk:
            Users._meta.fk_fields.add("active_tariff")
        if users_active_tariff_fk is not None:
            if original_active_tariff_reference is not None:
                users_active_tariff_fk.reference = original_active_tariff_reference
            if original_active_tariff_db_constraint is not None:
                users_active_tariff_fk.db_constraint = (
                    original_active_tariff_db_constraint
                )
        await Tortoise.close_connections()


async def _seed_standard_tariffs() -> None:
    from bloobcat.db.tariff import Tariffs

    await Tariffs.create(
        id=101,
        name="base_1m",
        months=1,
        base_price=199,
        progressive_multiplier=0.65,
        order=1,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=199,
    )
    await Tariffs.create(
        id=103,
        name="base_3m",
        months=3,
        base_price=449,
        progressive_multiplier=0.65,
        order=2,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=449,
    )
    await Tariffs.create(
        id=106,
        name="base_6m",
        months=6,
        base_price=749,
        progressive_multiplier=0.65,
        order=3,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=749,
    )
    await Tariffs.create(
        id=112,
        name="base_12m",
        months=12,
        base_price=1299,
        progressive_multiplier=0.65,
        order=4,
        devices_limit_default=1,
        devices_limit_family=30,
        final_price_default=1299,
        final_price_family=None,
        family_plan_enabled=False,
    )


def _plans_by_id(payload: list[dict]) -> dict[str, dict]:
    return {plan["id"]: plan for plan in payload}


async def _build_app_for_user(
    user_id: int,
    *,
    include_promo_router: bool = False,
) -> FastAPI:
    from bloobcat.db.users import Users
    from bloobcat.routes import promo as promo_module
    from bloobcat.routes import subscription as subscription_module

    app = FastAPI()
    app.include_router(subscription_module.router)
    if include_promo_router:
        app.include_router(promo_module.router)

    async def _override_validate() -> Users:
        return await Users.get(id=user_id)

    app.dependency_overrides[subscription_module.validate] = _override_validate
    if include_promo_router:
        app.dependency_overrides[promo_module.validate] = _override_validate
    return app


@pytest.mark.asyncio
async def test_subscription_plans_endpoint_returns_discounted_personal_prices():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9201,
        username="plans-discount",
        full_name="Plans Discount",
        is_registered=True,
    )
    await PersonalDiscount.create(
        user_id=user.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
        source="promo",
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())

    assert plans["1month"]["priceRub"] == 179
    assert plans["1month"]["originalPriceRub"] == 199
    assert plans["1month"]["personalDiscountPercent"] == 10
    assert plans["1month"]["discountText"] is None

    assert plans["3months"]["priceRub"] == 404
    assert plans["3months"]["originalPriceRub"] == 449
    assert plans["3months"]["personalDiscountPercent"] == 10
    assert plans["3months"]["discountText"] == "−25%"

    assert plans["12months"]["priceRub"] == 1169
    assert plans["12months"]["originalPriceRub"] == 1299
    assert plans["12months"]["personalDiscountPercent"] == 10
    assert plans["12months"]["discountText"] == "−46%"

    assert "12months_family" not in plans
    assert plans["12months"]["devicesMin"] == 1
    assert plans["12months"]["devicesMax"] == 30
    assert plans["12months"]["familyThreshold"] == 2
    assert plans["12months"]["lteEnabled"] is True
    assert plans["12months"]["lteAvailable"] is True
    assert plans["12months"]["ltePricePerGb"] == 1.5


@pytest.mark.asyncio
async def test_subscription_plans_apply_discount_only_to_matching_months():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9202,
        username="plans-restricted",
        full_name="Plans Restricted",
        is_registered=True,
    )
    await PersonalDiscount.create(
        user_id=user.id,
        percent=20,
        is_permanent=False,
        remaining_uses=1,
        source="promo",
        min_months=6,
        max_months=12,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/subscription/plans")

    assert response.status_code == 200
    plans = _plans_by_id(response.json())

    assert plans["1month"]["priceRub"] == 199
    assert plans["1month"]["originalPriceRub"] is None
    assert plans["1month"]["personalDiscountPercent"] is None

    assert plans["3months"]["priceRub"] == 449
    assert plans["3months"]["originalPriceRub"] is None
    assert plans["3months"]["personalDiscountPercent"] is None
    assert plans["3months"]["discountText"] == "−25%"

    assert plans["12months"]["priceRub"] == 1039
    assert plans["12months"]["originalPriceRub"] == 1299
    assert plans["12months"]["personalDiscountPercent"] == 20
    assert plans["12months"]["discountText"] == "−46%"

    assert "12months_family" not in plans


@pytest.mark.asyncio
async def test_subscription_plans_reads_discount_without_consuming_remaining_uses():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9203,
        username="plans-repeat-read",
        full_name="Plans Repeat Read",
        is_registered=True,
    )
    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=15,
        is_permanent=False,
        remaining_uses=1,
        source="promo",
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        first_response = await client.get("/subscription/plans")
        second_response = await client.get("/subscription/plans")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_plans = _plans_by_id(first_response.json())
    second_plans = _plans_by_id(second_response.json())

    assert first_plans["3months"]["priceRub"] == second_plans["3months"]["priceRub"] == 382
    assert first_plans["3months"]["originalPriceRub"] == second_plans["3months"]["originalPriceRub"] == 449

    discount_after = await PersonalDiscount.get(id=discount.id)
    assert int(discount_after.remaining_uses or 0) == 1


@pytest.mark.asyncio
async def test_subscription_plans_reflect_promo_redeem_result_without_consuming_discount(
    monkeypatch,
):
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.promotions import PromoCode
    from bloobcat.db.users import Users
    from bloobcat.settings import promo_settings

    await _seed_standard_tariffs()
    user = await Users.create(
        id=9204,
        username="plans-after-redeem",
        full_name="Plans After Redeem",
        is_registered=True,
    )

    secret = SecretStr("subscription-plans-test-secret")
    monkeypatch.setattr(promo_settings, "hmac_secret", secret)
    code = "SAVE15"
    code_hmac = hmac.new(
        secret.get_secret_value().encode(), code.encode(), hashlib.sha256
    ).hexdigest()
    await PromoCode.create(
        name="save 15",
        code_hmac=code_hmac,
        effects={"discount_percent": 15, "uses": 1, "min_months": 3, "max_months": 12},
        max_activations=5,
        per_user_limit=1,
        expires_at=date.today(),
    )

    app = await _build_app_for_user(user.id, include_promo_router=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        before_response = await client.get("/subscription/plans")
        redeem_response = await client.post("/promo/redeem", json={"code": code})
        after_response = await client.get("/subscription/plans")

    assert before_response.status_code == 200
    assert redeem_response.status_code == 200
    assert after_response.status_code == 200

    before_plans = _plans_by_id(before_response.json())
    assert before_plans["3months"]["priceRub"] == 449
    assert before_plans["3months"]["originalPriceRub"] is None

    redeem_payload = redeem_response.json()
    assert redeem_payload["success"] is True
    assert redeem_payload["effects"]["discount_percent"] == 15

    after_plans = _plans_by_id(after_response.json())
    assert after_plans["1month"]["priceRub"] == 199
    assert after_plans["1month"]["originalPriceRub"] is None

    assert after_plans["3months"]["priceRub"] == 382
    assert after_plans["3months"]["originalPriceRub"] == 449
    assert after_plans["3months"]["personalDiscountPercent"] == 15
    assert after_plans["12months"]["priceRub"] == 1104
    assert after_plans["12months"]["originalPriceRub"] == 1299

    discount = await PersonalDiscount.get(user_id=user.id, source="promo")
    assert int(discount.remaining_uses or 0) == 1


@pytest.mark.asyncio
async def test_subscription_quote_returns_backend_pricing_for_devices_and_lte():
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    tariff = await Tariffs.get(id=103)
    tariff.lte_enabled = True
    tariff.lte_price_per_gb = 10
    tariff.lte_min_gb = 0
    tariff.lte_max_gb = 100
    tariff.lte_step_gb = 5
    await tariff.save(update_fields=["lte_enabled", "lte_price_per_gb", "lte_min_gb", "lte_max_gb", "lte_step_gb"])
    user = await Users.create(
        id=9205,
        username="quote-builder",
        full_name="Quote Builder",
        is_registered=True,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 10, "lteGb": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tariffId"] == tariff.id
    assert payload["months"] == 3
    assert payload["deviceCount"] == 10
    assert payload["tariffKind"] == "family"
    assert payload["lteGb"] == 10
    assert payload["ltePriceRub"] == 100
    assert payload["subscriptionPriceRub"] == tariff.calculate_price(10)
    expected_device_discount = max(0, tariff.calculate_price(1) * 10 - tariff.calculate_price(10))
    assert payload["deviceDiscountRub"] == expected_device_discount
    assert payload["deviceDiscountPercent"] == round(expected_device_discount / (tariff.calculate_price(1) * 10) * 100)
    one_month_tariff = await Tariffs.get(id=101)
    expected_duration_discount = max(0, one_month_tariff.calculate_price(10) * 3 - tariff.calculate_price(10))
    assert payload["durationDiscountRub"] == expected_duration_discount
    assert payload["totalDiscountRub"] == expected_device_discount + expected_duration_discount
    assert payload["totalPriceRub"] == payload["subscriptionPriceRub"] + 100
    assert payload["copy"] == "Стоимость обновлена и будет проверена перед оплатой"
    assert "backend" not in payload["copy"].lower()
    assert payload["validation"]["devicesMax"] == 30
    assert payload["validation"]["familyThreshold"] == 2
    assert payload["validation"]["lteStepGb"] == 5


@pytest.mark.asyncio
async def test_subscription_quote_validates_device_and_lte_limits():
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    await _seed_standard_tariffs()
    tariff = await Tariffs.get(id=101)
    tariff.lte_enabled = False
    await tariff.save(update_fields=["lte_enabled"])
    user = await Users.create(
        id=9206,
        username="quote-limits",
        full_name="Quote Limits",
        is_registered=True,
    )

    app = await _build_app_for_user(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        too_many = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 31, "lteGb": 0},
        )
        lte_unavailable = await client.post(
            "/subscription/quote",
            json={"tariffId": tariff.id, "deviceCount": 1, "lteGb": 5},
        )

    assert too_many.status_code == 400
    assert "от 1 до 30" in too_many.text
    assert lte_unavailable.status_code == 400
    assert "LTE" in lte_unavailable.text


@pytest.mark.asyncio
async def test_admin_pricing_compute_returns_preview_and_blocks_invalid_limits():
    from bloobcat.services.admin_integration import compute_tariff_effective_pricing

    valid = await compute_tariff_effective_pricing(
        tariff_id=None,
        patch={
            "price_per_device": 300,
            "devices_max": 30,
            "anchor_device_count": 30,
            "anchor_total_price": 2500,
            "lte_enabled": True,
            "lte_price_per_gb": 12,
            "lte_min_gb": 0,
            "lte_max_gb": 200,
            "lte_step_gb": 5,
        },
    )

    assert valid["ok"] is True
    assert valid["computed"]["base_price"] == 300
    assert valid["computed"]["devices_limit_default"] == 1
    assert valid["computed"]["devices_limit_family"] == 30
    assert valid["computed"]["family_plan_enabled"] is False
    assert {row["deviceCount"] for row in valid["preview"]} == {1, 2, 5, 10, 30}
    assert valid["preview"][0]["tariffKind"] == "base"
    assert valid["preview"][-1]["tariffKind"] == "family"

    invalid = await compute_tariff_effective_pricing(
        tariff_id=None,
        patch={"price_per_device": 300, "devices_max": 31},
    )
    assert invalid["ok"] is False
    assert invalid["blockingErrors"]

    invalid_price_and_lte = await compute_tariff_effective_pricing(
        tariff_id=None,
        patch={"price_per_device": 0, "devices_max": 30, "lte_step_gb": 0},
    )
    assert invalid_price_and_lte["ok"] is False
    assert {
        item["field"] for item in invalid_price_and_lte["blockingErrors"]
    } >= {"price_per_device", "lte_step_gb"}

    base_price_wins_over_legacy_default = await compute_tariff_effective_pricing(
        tariff_id=None,
        patch={"base_price": 410, "final_price_default": 999, "devices_max": 30},
    )
    assert base_price_wins_over_legacy_default["computed"]["base_price"] == 410
    assert base_price_wins_over_legacy_default["computed"]["final_price_default"] == 410
