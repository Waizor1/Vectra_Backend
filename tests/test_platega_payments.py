import json
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from starlette.requests import Request
from tortoise import Tortoise

from tests._payment_test_stubs import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


def test_payment_defaults_to_platega_manual_and_yookassa_is_optional(monkeypatch):
    from bloobcat.settings import PaymentSettings, YookassaSettings

    for key in (
        "PAYMENT_PROVIDER",
        "PAYMENT_AUTO_RENEWAL_MODE",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    payment_settings = PaymentSettings()
    yookassa_settings = YookassaSettings()

    assert payment_settings.provider == "platega"
    assert payment_settings.auto_renewal_mode == "disabled"
    assert yookassa_settings.shop_id is None
    assert yookassa_settings.secret_key is None
    assert yookassa_settings.webhook_secret is None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
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
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
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
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _make_platega_request(headers: dict[str, str], body: dict) -> Request:
    raw_body = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pay/webhook/platega",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
        },
        receive,
    )


async def _seed_user_and_tariff(*, balance: int = 0):
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    user = await Users.create(
        id=123456,
        username="platega_user",
        full_name="Platega User",
        balance=balance,
        is_registered=True,
    )
    tariff = await Tariffs.create(
        id=10,
        name="Month",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
    )
    return user, tariff


@pytest.mark.asyncio
async def test_platega_create_payment_returns_redirect_and_reuses_client_request_id(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes import payment as payment_route
    from bloobcat.services.platega import PlategaCreateResult

    user, tariff = await _seed_user_and_tariff()
    created: list[dict] = []

    class FakePlategaClient:
        async def create_transaction(self, **kwargs):
            created.append(kwargs)
            return PlategaCreateResult(
                transaction_id="platega-tx-1",
                status="PENDING",
                redirect_url="https://pay.platega.test/tx-1",
                raw={"transactionId": "platega-tx-1"},
            )

    monkeypatch.setattr(payment_route.payment_settings, "provider", "platega")
    monkeypatch.setattr(payment_route.payment_settings, "auto_renewal_mode", "disabled")
    monkeypatch.setattr(payment_route.yookassa_settings, "shop_id", None)
    monkeypatch.setattr(payment_route.yookassa_settings, "secret_key", None)
    monkeypatch.setattr(payment_route, "PlategaClient", lambda *a, **kw: FakePlategaClient())

    first = await payment_route.pay(
        tariff_id=tariff.id,
        email="user@example.com",
        device_count=2,
        lte_gb=0,
        client_request_id="client-req-1",
        user=user,
    )
    second = await payment_route.pay(
        tariff_id=tariff.id,
        email="user@example.com",
        device_count=2,
        lte_gb=0,
        client_request_id="client-req-1",
        user=user,
    )

    assert len(created) == 1
    assert created[0]["amount"] == 1900.0
    assert created[0]["currency"] == "RUB"
    assert created[0]["description"] == "Оплата подписки пользователя 123456 (Тариф: Month)"
    assert created[0]["return_url"].startswith("https://t.me/")
    assert created[0]["failed_url"] == created[0]["return_url"]
    sent_payload = json.loads(created[0]["payload"])
    assert sent_payload["metadata"]["payment_provider"] == "platega"
    assert sent_payload["metadata"]["client_request_id"] == "client-req-1"
    assert first == {
        "redirect_to": "https://pay.platega.test/tx-1",
        "payment_id": "platega-tx-1",
        "provider": "platega",
    }
    assert second == first

    row = await ProcessedPayments.get(payment_id="platega-tx-1")
    assert row.provider == "platega"
    assert row.client_request_id == "client-req-1"
    assert row.payment_url == "https://pay.platega.test/tx-1"
    stored_payload = json.loads(row.provider_payload)
    assert stored_payload["metadata"]["payment_provider"] == "platega"
    assert stored_payload["metadata"]["expected_currency"] == "RUB"


@pytest.mark.asyncio
async def test_yookassa_provider_without_credentials_fails_closed(monkeypatch):
    from fastapi import HTTPException

    from bloobcat.routes import payment as payment_route

    user, tariff = await _seed_user_and_tariff()
    monkeypatch.setattr(payment_route.payment_settings, "provider", "yookassa")
    monkeypatch.setattr(payment_route.yookassa_settings, "shop_id", None)
    monkeypatch.setattr(payment_route.yookassa_settings, "secret_key", None)

    with pytest.raises(HTTPException) as exc_info:
        await payment_route.pay(
            tariff_id=tariff.id,
            email="user@example.com",
            device_count=1,
            lte_gb=0,
            client_request_id="yookassa-no-creds",
            user=user,
        )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_platega_status_confirmed_applies_fallback_without_renew_id(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_route
    from bloobcat.services.platega import PlategaStatusResult

    user, tariff = await _seed_user_and_tariff()
    metadata = {
        "user_id": user.id,
        "month": 1,
        "amount_from_balance": 0,
        "tariff_id": tariff.id,
        "device_count": 1,
        "tariff_kind": "base",
        "expected_amount": 1000.0,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    await ProcessedPayments.create(
        payment_id="platega-tx-status",
        provider="platega",
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    class FakePlategaClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_transaction_status(self, transaction_id: str):
            assert transaction_id == "platega-tx-status"
            return PlategaStatusResult(
                transaction_id=transaction_id,
                status="CONFIRMED",
                amount=1000.0,
                currency="RUB",
                payload=json.dumps({"metadata": metadata}),
                raw={},
            )

    monkeypatch.setattr(payment_route, "PlategaClient", FakePlategaClient)

    response = await payment_route.get_payment_status("platega-tx-status", user=user)

    assert response["provider"] == "platega"
    assert response["provider_status"] == "CONFIRMED"
    assert response["yookassa_status"] == "succeeded"
    assert response["is_paid"] is True
    assert response["entitlements_ready"] is True

    refreshed = await Users.get(id=user.id)
    assert refreshed.renew_id is None
    assert refreshed.expired_at is not None
    assert refreshed.expired_at > date.today()

    row = await ProcessedPayments.get(payment_id="platega-tx-status")
    assert row.provider == "platega"
    assert row.status == "succeeded"
    assert row.effect_applied is True


@pytest.mark.asyncio
async def test_platega_webhook_rejects_invalid_headers(monkeypatch):
    from fastapi import HTTPException

    from bloobcat.routes import payment as payment_route

    monkeypatch.setattr(payment_route.platega_settings, "merchant_id", "merchant-1")
    monkeypatch.setattr(
        payment_route.platega_settings,
        "secret_key",
        type("Secret", (), {"get_secret_value": lambda self: "secret-1"})(),
    )
    request = await _make_platega_request(
        {"X-MerchantId": "merchant-1", "X-Secret": "wrong"},
        {"id": "tx", "status": "CONFIRMED", "amount": 1000, "currency": "RUB"},
    )

    with pytest.raises(HTTPException) as exc:
        await payment_route.platega_webhook(request)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_platega_webhook_confirmed_is_idempotent(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_route

    user, tariff = await _seed_user_and_tariff()
    metadata = {
        "user_id": user.id,
        "month": 1,
        "amount_from_balance": 0,
        "tariff_id": tariff.id,
        "device_count": 1,
        "tariff_kind": "base",
        "expected_amount": 1000.0,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }

    monkeypatch.setattr(payment_route.platega_settings, "merchant_id", "merchant-1")
    monkeypatch.setattr(
        payment_route.platega_settings,
        "secret_key",
        type("Secret", (), {"get_secret_value": lambda self: "secret-1"})(),
    )

    body = {
        "id": "platega-tx-webhook",
        "status": "CONFIRMED",
        "amount": 1000,
        "currency": "RUB",
        "paymentMethod": 2,
        "payload": json.dumps({"metadata": metadata}),
    }

    request = await _make_platega_request(
        {"X-MerchantId": "merchant-1", "X-Secret": "secret-1"},
        body,
    )
    assert await payment_route.platega_webhook(request) == {"status": "ok"}
    first_user = await Users.get(id=user.id)
    first_expired_at = first_user.expired_at

    duplicate_request = await _make_platega_request(
        {"X-MerchantId": "merchant-1", "X-Secret": "secret-1"},
        body,
    )
    assert await payment_route.platega_webhook(duplicate_request) == {"status": "ok"}

    refreshed = await Users.get(id=user.id)
    assert refreshed.expired_at == first_expired_at
    row = await ProcessedPayments.get(payment_id="platega-tx-webhook")
    assert row.provider == "platega"
    assert row.status == "succeeded"
    assert row.effect_applied is True


@pytest.mark.asyncio
async def test_platega_reconcile_processes_pending_without_touching_yookassa(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.services.platega import PlategaStatusResult
    from bloobcat.tasks import payment_reconcile

    user, tariff = await _seed_user_and_tariff()
    metadata = {
        "user_id": user.id,
        "month": 1,
        "amount_from_balance": 0,
        "tariff_id": tariff.id,
        "device_count": 1,
        "tariff_kind": "base",
        "expected_amount": 1000.0,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    platega_row = await ProcessedPayments.create(
        payment_id="platega-tx-reconcile",
        provider="platega",
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )
    platega_row.processed_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    await platega_row.save(update_fields=["processed_at"])

    yookassa_row = await ProcessedPayments.create(
        payment_id="yookassa-pending",
        provider="yookassa",
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="pending",
    )

    class FakePlategaClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_transaction_status(self, transaction_id: str):
            assert transaction_id == "platega-tx-reconcile"
            return PlategaStatusResult(
                transaction_id=transaction_id,
                status="CONFIRMED",
                amount=1000.0,
                currency="RUB",
                payload=json.dumps({"metadata": metadata}),
                raw={},
            )

    monkeypatch.setattr(payment_reconcile, "PlategaClient", FakePlategaClient)
    monkeypatch.setattr(
        payment_reconcile,
        "_fetch_yookassa_payment",
        pytest.fail,
    )

    await payment_reconcile.reconcile_pending_payments(batch_limit=10)

    refreshed_user = await Users.get(id=user.id)
    assert refreshed_user.expired_at is not None
    assert refreshed_user.expired_at > date.today()

    platega_row = await ProcessedPayments.get(payment_id="platega-tx-reconcile")
    assert platega_row.provider == "platega"
    assert platega_row.status == "succeeded"
    assert platega_row.effect_applied is True

    yookassa_row = await ProcessedPayments.get(payment_id="yookassa-pending")
    assert yookassa_row.provider == "yookassa"
    assert yookassa_row.status == "pending"
