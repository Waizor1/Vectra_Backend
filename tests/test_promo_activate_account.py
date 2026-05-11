from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
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


async def _seed_base_1m_tariff(
    *,
    base_price: int = 150,
    lte_price_per_gb: float = 1.5,
) -> None:
    from bloobcat.db.tariff import Tariffs

    await Tariffs.create(
        id=201,
        name="base_1m",
        months=1,
        base_price=base_price,
        progressive_multiplier=0.961629,
        order=1,
        devices_limit_default=1,
        devices_limit_family=30,
        family_plan_enabled=False,
        final_price_default=base_price,
        lte_enabled=True,
        lte_price_per_gb=lte_price_per_gb,
    )


async def _build_promo_app(user_id: int) -> FastAPI:
    from bloobcat.db.users import Users
    from bloobcat.routes import promo as promo_module

    app = FastAPI()
    app.include_router(promo_module.router)

    async def _override_validate() -> Users:
        return await Users.get(id=user_id)

    app.dependency_overrides[promo_module.validate] = _override_validate
    return app


async def _seed_promo(
    *,
    code: str,
    effects: dict,
    monkeypatch,
    expires_at: date | None = None,
):
    from bloobcat.db.promotions import PromoCode
    from bloobcat.settings import promo_settings

    secret = SecretStr("activate-account-test-secret")
    monkeypatch.setattr(promo_settings, "hmac_secret", secret)
    code_hmac = hmac.new(
        secret.get_secret_value().encode(), code.encode(), hashlib.sha256
    ).hexdigest()
    await PromoCode.create(
        name=f"test {code}",
        code_hmac=code_hmac,
        effects=effects,
        max_activations=5,
        per_user_limit=1,
        expires_at=expires_at or (date.today() + timedelta(days=30)),
    )
    return code


@pytest.mark.asyncio
async def test_redeem_extend_days_plus_activate_account_promotes_trial_user(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users

    await _seed_base_1m_tariff(base_price=150, lte_price_per_gb=1.5)
    today = date.today()
    user = await Users.create(
        id=7001,
        username="rutracker-trial-user",
        full_name="RuTracker Trial",
        is_registered=True,
        is_trial=True,
        used_trial=True,
        hwid_limit=1,
        lte_gb_total=0,
        expired_at=today + timedelta(days=5),
        active_tariff_id=None,
    )

    code = await _seed_promo(
        code="RUTRACKER",
        effects={"extend_days": 10, "activate_account": True},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["effects"]["extend_days"] == 10
    assert payload["effects"]["activate_account"] is True

    refreshed = await Users.get(id=user.id)
    assert refreshed.is_trial is False
    assert refreshed.used_trial is True
    assert refreshed.active_tariff_id is not None
    # extended from existing expired_at (today+5) by 10 days
    assert refreshed.expired_at == today + timedelta(days=15)

    active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    assert active.name == "Промо-активация"
    assert active.months == 1
    assert active.hwid_limit == 1
    assert active.lte_gb_total == 0
    # 1m base price for 1 device
    assert active.price == 150
    # 1.5 ₽/GB enables paid LTE top-ups (price > 0)
    assert float(active.lte_price_per_gb) == pytest.approx(1.5)
    # multiplier copied from base tariff (effective)
    assert float(active.progressive_multiplier) == pytest.approx(0.961629, rel=1e-3)
    # анти-твинк должен распознавать строку как synthetic, иначе RUTRACKER на
    # триал-устройстве с дублирующим HWID получит иммунитет к санкции
    assert active.is_promo_synthetic is True


@pytest.mark.asyncio
async def test_activate_account_preserves_existing_lte_total(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users

    await _seed_base_1m_tariff()
    user = await Users.create(
        id=7002,
        username="rutracker-with-trial-lte",
        full_name="With Trial LTE",
        is_registered=True,
        is_trial=True,
        used_trial=True,
        hwid_limit=2,
        lte_gb_total=3,
        expired_at=date.today() + timedelta(days=2),
        active_tariff_id=None,
    )

    code = await _seed_promo(
        code="RUTRACKERLTE",
        effects={"extend_days": 10, "activate_account": True},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    refreshed = await Users.get(id=user.id)
    active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    # LTE total preserved from trial state
    assert refreshed.lte_gb_total == 3
    assert active.lte_gb_total == 3
    # hwid_limit preserved
    assert refreshed.hwid_limit == 2
    assert active.hwid_limit == 2


@pytest.mark.asyncio
async def test_activate_account_preserves_trial_lte_when_user_field_is_null(monkeypatch):
    """Регрессия: триал-юзер с user.lte_gb_total=NULL получает реальный лимит
    1 ГБ из tvpn_admin_settings.trial_lte_limit_gb. После активации этот лимит
    должен переноситься в синтетический ActiveTariffs, иначе RUTRACKER-промо
    обнуляет LTE у пользователя."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users
    from bloobcat.services import trial_lte as trial_lte_module

    await _seed_base_1m_tariff()

    async def _fake_trial_lte_limit() -> float:
        return 1.0

    monkeypatch.setattr(
        trial_lte_module, "read_trial_lte_limit_gb", _fake_trial_lte_limit
    )
    # Helper импортирует read_trial_lte_limit_gb напрямую — патчим и там.
    from bloobcat.services import promo_activation as promo_activation_module

    monkeypatch.setattr(
        promo_activation_module, "read_trial_lte_limit_gb", _fake_trial_lte_limit
    )

    user = await Users.create(
        id=7006,
        username="rutracker-trial-null-lte",
        full_name="Trial Null LTE",
        is_registered=True,
        is_trial=True,
        used_trial=True,
        hwid_limit=1,
        lte_gb_total=None,
        expired_at=date.today() + timedelta(days=2),
        active_tariff_id=None,
    )

    code = await _seed_promo(
        code="RUTRACKERNULL",
        effects={"extend_days": 10, "activate_account": True},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    refreshed = await Users.get(id=user.id)
    active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    # Триал-лимит 1 ГБ должен сохраниться, а не уйти в 0
    assert refreshed.lte_gb_total == 1
    assert active.lte_gb_total == 1


@pytest.mark.asyncio
async def test_activate_account_idempotent_when_user_already_has_active_tariff(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users

    await _seed_base_1m_tariff()
    today = date.today()
    user = await Users.create(
        id=7003,
        username="rutracker-already-paid",
        full_name="Already Paid",
        is_registered=True,
        is_trial=False,
        used_trial=True,
        hwid_limit=1,
        lte_gb_total=0,
        expired_at=today + timedelta(days=30),
        active_tariff_id=None,
    )
    existing = await ActiveTariffs.create(
        id="11111",
        user_id=user.id,
        name="Pre-existing",
        months=3,
        price=399,
        hwid_limit=1,
        lte_gb_total=0,
        lte_price_per_gb=2.0,
        progressive_multiplier=0.95,
    )
    user.active_tariff_id = existing.id
    await user.save()

    code = await _seed_promo(
        code="RUTRACKERIDEMP",
        effects={"extend_days": 10, "activate_account": True},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    refreshed = await Users.get(id=user.id)
    # Active tariff unchanged — no new synthetic row created
    assert refreshed.active_tariff_id == existing.id
    all_active = await ActiveTariffs.filter(user_id=user.id).all()
    assert len(all_active) == 1
    # extend_days still applied
    assert refreshed.expired_at == today + timedelta(days=40)
    # Существующий реальный тариф не должен быть помечен synthetic — иначе
    # настоящего платника по ошибке начнут считать «расширенным триалом»
    refreshed_active = await ActiveTariffs.get(id=existing.id)
    assert refreshed_active.is_promo_synthetic is False


@pytest.mark.asyncio
async def test_extend_days_only_does_not_create_active_tariff(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users

    await _seed_base_1m_tariff()
    user = await Users.create(
        id=7004,
        username="extend-only",
        full_name="Extend Only",
        is_registered=True,
        is_trial=True,
        used_trial=True,
        hwid_limit=1,
        lte_gb_total=0,
        expired_at=date.today() + timedelta(days=1),
        active_tariff_id=None,
    )

    code = await _seed_promo(
        code="EXTENDONLY",
        effects={"extend_days": 10},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    refreshed = await Users.get(id=user.id)
    # is_trial NOT cleared, active_tariff NOT created
    assert refreshed.is_trial is True
    assert refreshed.active_tariff_id is None
    assert await ActiveTariffs.filter(user_id=user.id).count() == 0


@pytest.mark.asyncio
async def test_activate_account_falls_back_to_defaults_without_base_tariff(monkeypatch):
    """When no base 1m Tariff is seeded, helper still produces a usable
    synthetic ActiveTariffs (price=0, lte_price_per_gb default 1.5)."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users

    user = await Users.create(
        id=7005,
        username="no-base-tariff",
        full_name="No Base Tariff",
        is_registered=True,
        is_trial=True,
        used_trial=True,
        hwid_limit=1,
        lte_gb_total=0,
        expired_at=date.today() + timedelta(days=1),
        active_tariff_id=None,
    )

    code = await _seed_promo(
        code="FALLBACK",
        effects={"extend_days": 10, "activate_account": True},
        monkeypatch=monkeypatch,
    )

    app = await _build_promo_app(user.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/promo/redeem", json={"code": code})

    assert response.status_code == 200, response.text
    refreshed = await Users.get(id=user.id)
    assert refreshed.active_tariff_id is not None
    active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    assert active.price == 0
    # default LTE price keeps paid LTE top-ups gated by price > 0 check
    assert float(active.lte_price_per_gb) == pytest.approx(1.5)
