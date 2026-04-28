import importlib
import types
from datetime import date, timedelta
import inspect
from typing import Any, cast, get_type_hints

import pytest
from fastapi import FastAPI, Request

from bloobcat.funcs import referral_attribution as referral_attr_module
from bloobcat.routes import auth as auth_module
from bloobcat.db import users as users_module
from bloobcat.db.users import Users


def test_auth_telegram_route_uses_plain_request_annotation_for_fastapi_startup():
    request_parameter = inspect.signature(auth_module.auth_telegram).parameters[
        "request"
    ]
    type_hints = get_type_hints(auth_module.auth_telegram)

    assert type_hints["request"] is Request
    assert request_parameter.default is None

    app = FastAPI()
    app.include_router(auth_module.router)

    assert any(route.path == "/auth/telegram" for route in app.routes)


@pytest.mark.asyncio
async def test_resolve_referral_start_param_numeric_non_partner_returns_plain_referral(
    monkeypatch,
):
    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 321
        return types.SimpleNamespace(id=321, is_partner=False)

    monkeypatch.setattr(referral_attr_module.Users, "get_or_none", _get_or_none)

    referred_by, utm = await referral_attr_module.resolve_referral_from_start_param(
        "321"
    )

    assert referred_by == 321
    assert utm is None


@pytest.mark.asyncio
async def test_resolve_referral_start_param_numeric_partner_marks_partner_source(
    monkeypatch,
):
    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 654
        return types.SimpleNamespace(id=654, is_partner=True)

    monkeypatch.setattr(referral_attr_module.Users, "get_or_none", _get_or_none)

    referred_by, utm = await referral_attr_module.resolve_referral_from_start_param(
        "654"
    )

    assert referred_by == 654
    assert utm == referral_attr_module.PARTNER_SOURCE_UTM


@pytest.mark.asyncio
async def test_resolve_referral_start_param_partner_dash_marks_partner_source(
    monkeypatch,
):
    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 777
        return types.SimpleNamespace(id=777, is_partner=True)

    monkeypatch.setattr(referral_attr_module.Users, "get_or_none", _get_or_none)

    referred_by, utm = await referral_attr_module.resolve_referral_from_start_param(
        "partner-777"
    )

    assert referred_by == 777
    assert utm == referral_attr_module.PARTNER_SOURCE_UTM


@pytest.mark.asyncio
async def test_resolve_referral_start_param_qr_keeps_qr_source_marker(monkeypatch):
    filter_calls: list[dict[str, object]] = []
    update_calls: list[dict[str, object]] = []

    class _FilterResult:
        async def update(self, **kwargs):
            update_calls.append(kwargs)
            return 1

    async def _get_or_none(**kwargs):
        if kwargs.get("slug") == "sluggy":
            return types.SimpleNamespace(owner_id=9001)
        return None

    monkeypatch.setattr(referral_attr_module.PartnerQr, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        referral_attr_module.PartnerQr,
        "filter",
        lambda **kwargs: filter_calls.append(kwargs) or _FilterResult(),
    )

    referred_by, utm = await referral_attr_module.resolve_referral_from_start_param(
        "qr_sluggy"
    )

    assert referred_by == 9001
    assert utm == "qr_sluggy"
    assert filter_calls == []
    assert update_calls == []


@pytest.mark.asyncio
async def test_auth_telegram_qr_start_param_counts_partner_qr_view(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=229, username="u", first_name="Qr", last_name=None
        ),
        start_param="qr_sluggy",
    )
    created_user = types.SimpleNamespace(id=229)
    filter_calls: list[dict[str, object]] = []
    update_calls: list[dict[str, object]] = []

    class _FilterResult:
        async def update(self, **kwargs):
            update_calls.append(kwargs)
            return 1

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 229
        assert kwargs["referred_by"] == 9001
        assert kwargs["utm"] == "qr_sluggy"
        return created_user, True

    async def _qr_get_or_none(**kwargs):
        if kwargs.get("slug") == "sluggy":
            return types.SimpleNamespace(id="qr-id", owner_id=9001)
        return None

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(referral_attr_module.PartnerQr, "get_or_none", _qr_get_or_none)
    monkeypatch.setattr(
        referral_attr_module.PartnerQr,
        "filter",
        lambda **kwargs: filter_calls.append(kwargs) or _FilterResult(),
    )
    monkeypatch.setattr(
        auth_module, "issue_access_token_for_user", lambda user: (f"token-{user.id}", 3600)
    )

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-229"
    assert result.expiresIn == 3600
    assert filter_calls == [{"id": "qr-id"}]
    assert len(update_calls) == 1
    assert "views_count" in update_calls[0]


@pytest.mark.asyncio
async def test_auth_telegram_without_intent_returns_requires_registration(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=111, username="u", first_name="A", last_name=None)
    )

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 111
        return None

    async def _should_not_create(*args, **kwargs):
        raise AssertionError(
            "Users.get_user should not be called without registration intent"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is True
    assert result.accessToken == ""
    assert result.expiresIn == 0


@pytest.mark.asyncio
async def test_auth_telegram_with_start_param_creates_user(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=222, username="u", first_name="B", last_name=None
        ),
        start_param="qr_abc",
    )
    created_user = types.SimpleNamespace(id=222)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 222
        assert kwargs["ensure_remnawave"] is True
        return created_user, True

    async def _qr_get_or_none(**kwargs):
        return None

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(referral_attr_module.PartnerQr, "get_or_none", _qr_get_or_none)
    monkeypatch.setattr(
        auth_module, "issue_access_token_for_user", lambda user: (f"token-{user.id}", 3600)
    )

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-222"
    assert result.expiresIn == 3600


@pytest.mark.asyncio
async def test_auth_telegram_with_partner_start_param_marks_partner_source(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=223, username="u", first_name="Partner", last_name=None
        ),
        start_param="partner-901",
    )
    created_user = types.SimpleNamespace(id=223)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 223
        assert kwargs["referred_by"] == 901
        assert kwargs["utm"] == referral_attr_module.PARTNER_SOURCE_UTM
        return created_user, True

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 901
        return types.SimpleNamespace(id=901, is_partner=True)

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(referral_attr_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        auth_module, "issue_access_token_for_user", lambda user: (f"token-{user.id}", 3600)
    )

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-223"
    assert result.expiresIn == 3600


@pytest.mark.asyncio
async def test_auth_telegram_with_register_intent_completes_remnawave_before_token(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=444, username="u", first_name="D", last_name=None)
    )
    created_user = types.SimpleNamespace(id=444, is_registered=True)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 444
        assert kwargs["ensure_remnawave"] is False
        return created_user, True

    async def _complete_registration(user):
        assert user is created_user
        return f"token-{user.id}", 900

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(auth_module, "complete_registration_for_user", _complete_registration)

    payload = auth_module.TelegramAuthRequest(
        initData="ok", startParam="qr_abc", registerIntent=True
    )
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-444"
    assert result.expiresIn == 900


@pytest.mark.asyncio
async def test_auth_telegram_with_register_intent_does_not_mutate_activation_registration_flag(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=446, username="u", first_name="D", last_name=None)
    )
    created_user = types.SimpleNamespace(id=446, is_registered=False)
    save_calls: list[dict[str, list[str]]] = []

    async def _save(*args, **kwargs):
        save_calls.append(kwargs)

    created_user.save = _save

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 446
        assert kwargs["ensure_remnawave"] is False
        return created_user, True

    async def _complete_registration(user):
        assert user is created_user
        return f"token-{user.id}", 900

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(auth_module, "complete_registration_for_user", _complete_registration)

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=True)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert created_user.is_registered is False
    assert save_calls == []


@pytest.mark.asyncio
async def test_auth_telegram_register_intent_returns_public_pending_when_sync_fails(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=447, username="u", first_name="D", last_name=None)
    )
    created_user = types.SimpleNamespace(id=447, is_registered=True)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 447
        assert kwargs["ensure_remnawave"] is False
        return created_user, True

    async def _complete_registration(_user):
        raise auth_module.WebAuthError(
            "registration_sync_pending",
            message="Failed to initialize user account.",
            status_code=503,
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(auth_module, "complete_registration_for_user", _complete_registration)

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=True)
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.auth_telegram(payload)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "registration_sync_pending",
        "message": "Аккаунт ещё настраивается. Попробуйте снова через несколько секунд.",
    }


@pytest.mark.asyncio
async def test_auth_telegram_register_path_returns_503_when_user_creation_fails(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=445, username="u", first_name="D", last_name=None
        ),
        start_param="qr_abc",
    )

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 445
        return None, False

    async def _qr_get_or_none(**kwargs):
        return None

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(referral_attr_module.PartnerQr, "get_or_none", _qr_get_or_none)

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.auth_telegram(payload)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service temporarily unavailable"


@pytest.mark.asyncio
async def test_auth_telegram_with_unknown_start_param_still_requires_registration(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=224, username="u", first_name="B", last_name=None)
    )

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 224
        return None

    async def _should_not_create(*args, **kwargs):
        raise AssertionError(
            "Users.get_user should not be called for non-whitelisted start_param"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)

    payload = auth_module.TelegramAuthRequest(
        initData="ok", startParam="campaign-abc", registerIntent=False
    )
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is True
    assert result.accessToken == ""
    assert result.expiresIn == 0


@pytest.mark.asyncio
async def test_auth_telegram_uses_init_data_start_param_when_payload_missing(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=225, username="u", first_name="B", last_name=None
        ),
        start_param="qr_abc",
    )
    created_user = types.SimpleNamespace(id=225)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 225
        return created_user, True

    async def _qr_get_or_none(**kwargs):
        return None

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(referral_attr_module.PartnerQr, "get_or_none", _qr_get_or_none)
    monkeypatch.setattr(
        auth_module, "issue_access_token_for_user", lambda user: (f"token-{user.id}", 3600)
    )

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-225"
    assert result.expiresIn == 3600


@pytest.mark.asyncio
async def test_auth_telegram_mismatch_payload_cannot_override_signed_start_param(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=226, username="u", first_name="B", last_name=None
        ),
        start_param="123",
    )

    async def _get_user_should_not_run(**kwargs):
        raise AssertionError(
            "Users.get_user must not be called on signed/payload mismatch"
        )

    def _warning(message, *args, **kwargs):
        warning_messages.append(message if not args else message.format(*args))

    warning_messages: list[str] = []
    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user_should_not_run)
    monkeypatch.setattr(auth_module.logger, "warning", _warning)

    payload = auth_module.TelegramAuthRequest(
        initData="ok", startParam="qr_abc", registerIntent=False
    )
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.auth_telegram(payload)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid start_param"
    assert any("start_param mismatch" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_auth_telegram_local_dev_auth_rejects_empty_allowlist(monkeypatch):
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "http://localhost:5173"})
    )

    def _should_not_parse(*args, **kwargs):
        raise AssertionError(
            "safe_parse_webapp_init_data must not run for rejected local dev auth"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", _should_not_parse)
    monkeypatch.setattr(auth_module.local_dev_auth_settings, "enabled", True)
    monkeypatch.setattr(auth_module.local_dev_auth_settings, "allowed_telegram_ids", [])

    payload = auth_module.TelegramAuthRequest(initData="dev-local-tg:555")
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.auth_telegram(payload, request=request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Local dev auth forbidden for this Telegram user"


@pytest.mark.asyncio
async def test_auth_telegram_mismatch_payload_fail_closes_with_register_intent(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(
            id=228, username="u", first_name="B", last_name=None
        ),
        start_param="123",
    )

    async def _get_user_should_not_run(**kwargs):
        raise AssertionError(
            "Users.get_user must not be called on signed/payload mismatch"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user_should_not_run)

    payload = auth_module.TelegramAuthRequest(
        initData="ok", startParam="qr_abc", registerIntent=True
    )
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.auth_telegram(payload)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid start_param"


@pytest.mark.asyncio
async def test_auth_telegram_payload_only_start_param_does_not_trigger_registration_exception(
    monkeypatch,
):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=227, username="u", first_name="B", last_name=None)
    )

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 227
        return None

    async def _get_user_should_not_run(*args, **kwargs):
        raise AssertionError(
            "Unsigned payload startParam must not trigger Users.get_user"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user_should_not_run)

    payload = auth_module.TelegramAuthRequest(
        initData="ok", startParam="qr_abc", registerIntent=False
    )
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is True
    assert result.accessToken == ""
    assert result.expiresIn == 0


@pytest.mark.asyncio
async def test_auth_telegram_without_intent_uses_existing_user(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=333, username="u", first_name="C", last_name=None)
    )
    existing_user = types.SimpleNamespace(id=333)

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 333
        return existing_user

    async def _should_not_create(*args, **kwargs):
        raise AssertionError(
            "Users.get_user should not be called when user already exists"
        )

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)
    monkeypatch.setattr(
        auth_module, "issue_access_token_for_user", lambda user: (f"token-{user.id}", 1800)
    )

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is False
    assert result.accessToken == "token-333"
    assert result.expiresIn == 1800


@pytest.mark.asyncio
async def test_get_user_referral_bind_uses_atomic_guard(monkeypatch):
    telegram_user = types.SimpleNamespace(
        id=555, username="u", first_name="E", last_name=None
    )
    save_calls = []
    filter_calls = []

    class _FilterResult:
        async def update(self, **kwargs):
            filter_calls.append(kwargs)
            return 1

    class _StubUser:
        id = 555
        username = "u"
        full_name = "E"
        utm = None
        referred_by = None
        is_registered = False
        remnawave_uuid = "uuid"

        async def save(self, *args, **kwargs):
            save_calls.append(kwargs)

        async def count_referrals(self):
            return None

    async def _update_or_create(**kwargs):
        return _StubUser(), False

    async def _get_or_none(**kwargs):
        return (
            types.SimpleNamespace(id=999, full_name="Ref")
            if kwargs.get("id") == 999
            else None
        )

    monkeypatch.setattr(Users, "update_or_create", _update_or_create)
    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        Users,
        "filter",
        classmethod(
            lambda cls, **kwargs: _FilterResult() if kwargs else _FilterResult()
        ),
    )

    user, is_new = await Users.get_user(
        telegram_user=cast(Any, telegram_user),
        referred_by=999,
        utm=None,
        ensure_remnawave=True,
    )

    assert user is not None
    assert is_new is False
    assert user.referred_by == 999
    assert len(filter_calls) == 1
    assert filter_calls[0]["referred_by"] == 999
    assert save_calls == []


@pytest.mark.asyncio
async def test_get_user_partner_source_overrides_existing_utm_on_first_bind(
    monkeypatch,
):
    telegram_user = types.SimpleNamespace(
        id=558, username="u", first_name="Partner", last_name=None
    )
    save_calls = []
    filter_calls = []

    class _FilterResult:
        async def update(self, **kwargs):
            filter_calls.append(kwargs)
            return 1

    class _StubUser:
        id = 558
        username = "u"
        full_name = "Partner"
        utm = "campaign_old"
        referred_by = None
        is_registered = False
        remnawave_uuid = "uuid"

        async def save(self, *args, **kwargs):
            save_calls.append(kwargs)

        async def count_referrals(self):
            return None

    async def _update_or_create(**kwargs):
        return _StubUser(), False

    async def _get_or_none(**kwargs):
        return (
            types.SimpleNamespace(id=777, full_name="Partner Ref", is_partner=True)
            if kwargs.get("id") == 777
            else None
        )

    monkeypatch.setattr(Users, "update_or_create", _update_or_create)
    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        Users,
        "filter",
        classmethod(
            lambda cls, **kwargs: _FilterResult() if kwargs else _FilterResult()
        ),
    )

    user, is_new = await Users.get_user(
        telegram_user=cast(Any, telegram_user),
        referred_by=777,
        utm=referral_attr_module.PARTNER_SOURCE_UTM,
        ensure_remnawave=True,
    )

    assert user is not None
    assert is_new is False
    assert user.referred_by == 777
    assert user.utm == referral_attr_module.PARTNER_SOURCE_UTM
    assert len(filter_calls) == 1
    assert filter_calls[0]["referred_by"] == 777
    assert save_calls == [{"update_fields": ["utm"]}]


@pytest.mark.asyncio
async def test_get_user_race_does_not_overwrite_referred_by_with_full_save(monkeypatch):
    telegram_user = types.SimpleNamespace(
        id=556, username="u", first_name="F", last_name=None
    )
    save_calls = []
    filter_conditions = []

    class _FilterResult:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

        async def update(self, **kwargs):
            filter_conditions.append(self._kwargs)
            return 0

    class _StubUser:
        id = 556
        username = "u"
        full_name = "F"
        utm = None
        referred_by = None
        is_registered = False
        remnawave_uuid = "uuid"

        async def save(self, *args, **kwargs):
            save_calls.append(kwargs)

        async def count_referrals(self):
            return None

    async def _update_or_create(**kwargs):
        return _StubUser(), False

    async def _get_or_none(**kwargs):
        return (
            types.SimpleNamespace(id=998, full_name="Ref")
            if kwargs.get("id") == 998
            else None
        )

    monkeypatch.setattr(Users, "update_or_create", _update_or_create)
    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        Users, "filter", classmethod(lambda cls, **kwargs: _FilterResult(**kwargs))
    )

    user, _ = await Users.get_user(
        telegram_user=cast(Any, telegram_user),
        referred_by=998,
        utm="utm_source",
        ensure_remnawave=True,
    )

    assert user is not None
    assert user.referred_by is None
    assert filter_conditions[0]["id"] == 556
    assert filter_conditions[0]["referred_by__isnull"] is True
    assert filter_conditions[0]["is_registered"] is False
    assert len(save_calls) == 1
    assert save_calls[0]["update_fields"] == ["utm"]


@pytest.mark.asyncio
async def test_ensure_remnawave_user_create_uses_partial_save_and_keeps_concurrent_referral(
    monkeypatch,
):
    user = Users(id=557, username="u", full_name="G")

    db_row = {
        "referred_by": None,
        "remnawave_uuid": None,
        "is_trial": False,
        "used_trial": True,
        "expired_at": date.today() + timedelta(days=5),
        "hwid_limit": 0,
    }
    save_calls: list[dict[str, Any]] = []

    class _StaleCurrentUser:
        id = 557
        email = None
        active_tariff_id = None
        lte_gb_total = None

        def __init__(self):
            self.referred_by = db_row["referred_by"]
            self.remnawave_uuid = db_row["remnawave_uuid"]
            self.is_trial = db_row["is_trial"]
            self.used_trial = db_row["used_trial"]
            self.expired_at = db_row["expired_at"]
            self.hwid_limit = db_row["hwid_limit"]

        def name(self):
            return "G"

        async def save(self, *args, **kwargs):
            save_calls.append(kwargs)
            update_fields = kwargs.get("update_fields")
            if update_fields:
                for field in update_fields:
                    db_row[field] = getattr(self, field)
            else:
                db_row["referred_by"] = self.referred_by
                db_row["remnawave_uuid"] = self.remnawave_uuid
                db_row["is_trial"] = self.is_trial
                db_row["used_trial"] = self.used_trial
                db_row["expired_at"] = self.expired_at
                db_row["hwid_limit"] = self.hwid_limit

    stale_current_user = _StaleCurrentUser()

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 557
        return stale_current_user

    class _FakeRemnaWaveUsers:
        async def create_user(self, **kwargs):
            # Concurrent auth request binds referral after stale snapshot was loaded.
            db_row["referred_by"] = 998
            return {"response": {"uuid": "new-uuid-557"}}

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeRemnaWaveUsers()

        async def close(self):
            return None

    class _FakeToken:
        def get_secret_value(self):
            return "token"

    fake_remnawave_settings = types.SimpleNamespace(
        url="https://remnawave.local",
        token=_FakeToken(),
        default_internal_squad_uuid=None,
        lte_internal_squad_uuid=None,
        default_external_squad_uuid=None,
    )

    remnawave_client_module = importlib.import_module(
        "bloobcat.routes.remnawave.client"
    )

    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        remnawave_client_module, "RemnaWaveClient", _FakeRemnaWaveClient
    )
    monkeypatch.setattr(users_module, "remnawave_settings", fake_remnawave_settings)

    created = await user._ensure_remnawave_user()

    assert created is True
    assert user.remnawave_uuid == "new-uuid-557"
    assert db_row["referred_by"] == 998
    assert db_row["hwid_limit"] == 1
    assert len(save_calls) == 1
    assert save_calls[0]["update_fields"] == [
        "remnawave_uuid",
        "is_trial",
        "used_trial",
        "expired_at",
        "hwid_limit",
    ]
