from __future__ import annotations

import base64
import types
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from bloobcat.services import web_auth


def test_web_auth_exception_response_uses_public_safe_message():
    error = web_auth.WebAuthError(
        "registration_sync_pending",
        message="Failed to initialize user account.",
        status_code=503,
    )

    response = web_auth.web_auth_exception_response(error)

    assert response.status_code == 503
    assert response.detail == {
        "code": "registration_sync_pending",
        "message": "Аккаунт ещё настраивается. Попробуйте снова через несколько секунд.",
    }
    assert "Failed to initialize" not in response.detail["message"]


@pytest.mark.asyncio
async def test_oauth_start_uses_pkce_nonce_and_hashed_state(monkeypatch):
    created_rows: list[dict[str, object]] = []

    class _FakeOAuthState:
        @classmethod
        async def create(cls, **kwargs):
            created_rows.append(kwargs)
            return types.SimpleNamespace(**kwargs)

    provider_config = web_auth.ProviderConfig(
        provider="google",
        client_id="google-client-id",
        client_secret="google-secret",
        auth_url="https://accounts.example/auth",
        token_url="https://accounts.example/token",
        jwks_url="https://accounts.example/jwks",
        issuer="https://accounts.example",
        userinfo_url="https://accounts.example/userinfo",
        scope="openid email profile",
    )

    monkeypatch.setattr(web_auth, "AuthOAuthState", _FakeOAuthState)
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: provider_config)
    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "oauth_google_enabled", True)
    monkeypatch.setattr(web_auth.oauth_settings, "enabled_providers", ["google"])

    authorization_url = await web_auth.create_oauth_authorization_url(
        provider="google",
        mode="login",
        return_to="/welcome?from=test",
    )

    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.example"
    assert params["client_id"] == ["google-client-id"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"][0]
    assert params["nonce"][0]
    assert params["code_challenge"][0]

    assert len(created_rows) == 1
    row = created_rows[0]
    assert row["provider"] == "google"
    assert row["mode"] == "login"
    assert row["return_to"] == "/welcome?from=test"
    assert row["nonce"] == params["nonce"][0]
    assert row["state_hash"] != params["state"][0]
    assert len(str(row["state_hash"])) == 64
    assert "pkce_verifier" in row


@pytest.mark.asyncio
async def test_complete_telegram_link_moves_empty_web_identities_to_existing_telegram_user(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None, using_db=None):
            calls.append(("source_save", update_fields))

    source_user = _User(id=web_auth.WEB_USER_ID_FLOOR + 10, auth_token_version=0)
    target_user = _User(id=123456, auth_token_version=2)
    telegram_user = types.SimpleNamespace(id=123456, first_name="Tg", last_name="User")

    class _UpdateQuery:
        def __init__(self, name: str):
            self.name = name

        def exclude(self, **kwargs):
            calls.append((f"{self.name}_exclude", kwargs))
            return self

        def using_db(self, _conn):
            calls.append((f"{self.name}_using_db", True))
            return self

        async def update(self, **kwargs):
            calls.append((f"{self.name}_update", kwargs))
            return 1

    class _FakeIdentity:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("identity_filter", kwargs))
            return _UpdateQuery("identity")

    class _FakePasswordCredential:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("password_filter", kwargs))
            return _UpdateQuery("password")

    async def _consume_link_request(user, token):
        calls.append(("consume", (user.id, token)))
        return types.SimpleNamespace(id=1)

    async def _get_or_none(**kwargs):
        assert kwargs == {"id": 123456}
        return target_user

    class _UserLockQuery:
        def using_db(self, _conn):
            return self

        async def get(self, **kwargs):
            if kwargs == {"id": source_user.id}:
                return source_user
            if kwargs == {"id": target_user.id}:
                return target_user
            raise AssertionError(kwargs)

    class _FakeUsers:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return await _get_or_none(**kwargs)

        @classmethod
        def select_for_update(cls):
            return _UserLockQuery()

    class _FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _has_material_data(user_id: int, conn=None) -> bool:
        assert user_id == source_user.id
        return False

    async def _ensure_telegram_identity(user, tg_user):
        calls.append(("ensure_telegram", (user.id, tg_user.id)))

    monkeypatch.setattr(web_auth, "_consume_link_request", _consume_link_request)
    monkeypatch.setattr(web_auth, "Users", _FakeUsers)
    monkeypatch.setattr(web_auth, "in_transaction", lambda: _FakeTransaction())
    monkeypatch.setattr(web_auth, "user_has_material_data", _has_material_data)
    monkeypatch.setattr(web_auth, "AuthIdentity", _FakeIdentity)
    monkeypatch.setattr(web_auth, "AuthPasswordCredential", _FakePasswordCredential)
    monkeypatch.setattr(web_auth, "ensure_telegram_identity", _ensure_telegram_identity)

    result_user, merged = await web_auth.complete_telegram_link(
        source_user,
        "link-token",
        telegram_user,
    )

    assert result_user is target_user
    assert merged is True
    assert source_user.auth_token_version == 1
    assert ("consume", (source_user.id, "link-token")) in calls
    assert ("identity_update", {"user_id": target_user.id}) in calls
    assert ("password_update", {"user_id": target_user.id}) in calls
    assert ("ensure_telegram", (target_user.id, telegram_user.id)) in calls
    assert ("source_save", ["auth_token_version"]) in calls


@pytest.mark.asyncio
async def test_complete_telegram_link_blocks_auto_merge_when_source_has_material_data(monkeypatch):
    source_user = types.SimpleNamespace(id=web_auth.WEB_USER_ID_FLOOR + 11)
    target_user = types.SimpleNamespace(id=555)
    telegram_user = types.SimpleNamespace(id=555, first_name="Tg", last_name=None)

    async def _consume_link_request(_user, _token):
        return types.SimpleNamespace(id=1)

    async def _get_or_none(**kwargs):
        assert kwargs == {"id": 555}
        return target_user

    async def _has_material_data(user_id: int) -> bool:
        assert user_id == source_user.id
        return True

    monkeypatch.setattr(web_auth, "_consume_link_request", _consume_link_request)
    monkeypatch.setattr(web_auth.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(web_auth, "user_has_material_data", _has_material_data)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.complete_telegram_link(source_user, "link-token", telegram_user)

    assert exc_info.value.code == "merge_requires_support"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_complete_registration_raises_for_telegram_when_remnawave_ensure_is_still_unavailable():
    class _User(types.SimpleNamespace):
        async def _ensure_remnawave_user(self):
            return False

    user = _User(id=123456, remnawave_uuid=None)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.complete_registration_for_user(user)

    assert exc_info.value.code == "registration_sync_pending"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_registration_can_defer_remnawave_for_explicit_telegram_registration(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            calls.append(("save", tuple(update_fields or ())))

        async def _ensure_remnawave_user(self):
            calls.append(("ensure", None))
            return False

    def _schedule(cls, user_id):
        calls.append(("schedule", user_id))

    async def _grant_trial(cls, user_id, trial_until):
        calls.append(("atomic_grant", (user_id, trial_until)))
        return True

    monkeypatch.setattr(web_auth.Users, "_schedule_remnawave_ensure", classmethod(_schedule))
    monkeypatch.setattr(web_auth.Users, "_grant_trial_if_unclaimed", classmethod(_grant_trial))
    monkeypatch.setattr(web_auth, "issue_access_token_for_user", lambda user: ("token-tg", 3600))

    user = _User(
        id=123456,
        remnawave_uuid=None,
        expired_at=None,
        is_trial=False,
        used_trial=False,
    )

    token, ttl = await web_auth.complete_registration_for_user(user, defer_remnawave=True)

    assert (token, ttl) == ("token-tg", 3600)
    assert user.is_trial is True
    assert user.used_trial is True
    assert user.expired_at is not None
    assert ("atomic_grant", (user.id, user.expired_at)) in calls
    assert ("schedule", user.id) in calls
    assert ("ensure", None) not in calls


@pytest.mark.asyncio
async def test_grant_trial_if_eligible_skips_notification_when_parallel_grant_wins(
    monkeypatch,
):
    trial_until = date.today() + timedelta(days=web_auth.app_settings.trial_days)
    calls: list[tuple[str, object]] = []
    notifications: list[int] = []

    class _TrialUpdateQuery:
        async def update(self, **kwargs):
            calls.append(("atomic_update", kwargs))
            return 0

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            calls.append(("stale_save", tuple(update_fields or ())))

    user = _User(
        id=123456,
        expired_at=None,
        is_trial=False,
        used_trial=False,
    )
    refreshed_user = types.SimpleNamespace(
        id=user.id,
        expired_at=trial_until,
        is_trial=True,
        used_trial=True,
    )

    def _filter(*args, **kwargs):
        calls.append(("filter", kwargs))
        return _TrialUpdateQuery()

    async def _get_or_none(*args, **kwargs):
        assert kwargs == {"id": user.id}
        return refreshed_user

    async def _notify_trial_granted(notified_user):
        notifications.append(int(notified_user.id))

    from bloobcat.bot.notifications.trial import granted as trial_granted_mod

    monkeypatch.setattr(web_auth.Users, "filter", _filter)
    monkeypatch.setattr(web_auth.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(
        trial_granted_mod,
        "notify_trial_granted",
        _notify_trial_granted,
    )

    granted = await web_auth.grant_trial_if_eligible(user)

    assert granted is False
    assert user.is_trial is True
    assert user.used_trial is True
    assert user.expired_at == trial_until
    assert notifications == []
    assert ("stale_save", ("is_trial", "used_trial", "expired_at")) not in calls


@pytest.mark.asyncio
async def test_complete_registration_grants_web_trial_without_waiting_for_remnawave(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            calls.append(("save", tuple(update_fields or ())))

        async def _ensure_remnawave_user(self):
            calls.append(("ensure", None))
            return False

    def _schedule(cls, user_id):
        calls.append(("schedule", user_id))

    async def _grant_trial(cls, user_id, trial_until):
        calls.append(("atomic_grant", (user_id, trial_until)))
        return True

    monkeypatch.setattr(web_auth.Users, "_schedule_remnawave_ensure", classmethod(_schedule))
    monkeypatch.setattr(web_auth.Users, "_grant_trial_if_unclaimed", classmethod(_grant_trial))
    monkeypatch.setattr(web_auth, "issue_access_token_for_user", lambda user: ("token-web", 3600))

    user = _User(
        id=web_auth.WEB_USER_ID_FLOOR + 12,
        remnawave_uuid=None,
        expired_at=None,
        is_trial=False,
        used_trial=False,
    )

    token, ttl = await web_auth.complete_registration_for_user(user)

    assert (token, ttl) == ("token-web", 3600)
    assert user.is_trial is True
    assert user.used_trial is True
    assert user.expired_at is not None
    assert ("atomic_grant", (user.id, user.expired_at)) in calls
    assert ("schedule", user.id) in calls
    assert ("ensure", None) not in calls


@pytest.mark.asyncio
async def test_unlink_identity_rejects_last_working_login_method(monkeypatch):
    class _IdentityQuery:
        def __init__(self, *, exists: bool = False, count: int = 0):
            self._exists = exists
            self._count = count

        async def exists(self):
            return self._exists

        def exclude(self, **kwargs):
            assert kwargs == {"provider": "password"}
            return _IdentityQuery(count=1)

        async def count(self):
            return self._count

    class _FakeIdentity:
        @classmethod
        def filter(cls, **kwargs):
            if kwargs == {"user_id": 42, "provider": "google"}:
                return _IdentityQuery(exists=True)
            assert kwargs == {"user_id": 42}
            return _IdentityQuery(count=1)

    class _FakePasswordCredential:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs == {"user_id": 42, "email_verified": True}
            return _IdentityQuery(exists=False)

    monkeypatch.setattr(web_auth, "AuthIdentity", _FakeIdentity)
    monkeypatch.setattr(web_auth, "AuthPasswordCredential", _FakePasswordCredential)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.unlink_identity(types.SimpleNamespace(id=42), "google")

    assert exc_info.value.code == "last_identity"
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_unlink_identity_rejects_password_when_it_is_last_verified_method(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _IdentityQuery:
        def __init__(self, *, exists: bool = False, count: int = 0, deleted: int = 0):
            self._exists = exists
            self._count = count
            self._deleted = deleted

        async def exists(self):
            return self._exists

        def exclude(self, **kwargs):
            assert kwargs == {"provider": "password"}
            return _IdentityQuery(count=0)

        async def count(self):
            return self._count

        async def delete(self):
            calls.append(("identity_delete", self._deleted))
            return self._deleted

    class _PasswordQuery:
        def __init__(self, *, exists: bool = False, deleted: int = 0):
            self._exists = exists
            self._deleted = deleted

        async def exists(self):
            return self._exists

        async def delete(self):
            calls.append(("password_delete", self._deleted))
            return self._deleted

    class _FakeIdentity:
        @classmethod
        def filter(cls, **kwargs):
            if kwargs == {"user_id": 42, "provider": "password"}:
                return _IdentityQuery(exists=True, deleted=1)
            assert kwargs == {"user_id": 42}
            return _IdentityQuery(count=0)

    class _FakePasswordCredential:
        @classmethod
        def filter(cls, **kwargs):
            if kwargs == {"user_id": 42, "email_verified": True}:
                return _PasswordQuery(exists=True)
            assert kwargs == {"user_id": 42}
            return _PasswordQuery(exists=True, deleted=1)

    monkeypatch.setattr(web_auth, "AuthIdentity", _FakeIdentity)
    monkeypatch.setattr(web_auth, "AuthPasswordCredential", _FakePasswordCredential)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.unlink_identity(types.SimpleNamespace(id=42), "password")

    assert exc_info.value.code == "last_identity"
    assert exc_info.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_password_email_rate_limit_raises_generic_rate_limited_error(monkeypatch):
    audit_calls: list[dict[str, object]] = []

    class _FakeLimiter:
        async def is_allowed(self, key: str):
            assert key.startswith("password_login:")
            return False, 42

    async def _audit(**kwargs):
        audit_calls.append(kwargs)

    monkeypatch.setattr(web_auth, "password_email_limiter", _FakeLimiter())
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.enforce_password_email_rate_limit(
            "user@example.com",
            action="password_login",
        )

    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.status_code == 429
    assert audit_calls[0]["reason"] == "email_rate_limited"


@pytest.mark.asyncio
async def test_password_register_requires_email_delivery_before_creating_user(monkeypatch):
    create_called = False

    async def _rate_limit(*_args, **_kwargs):
        return None

    async def _create_web_user(**_kwargs):
        nonlocal create_called
        create_called = True
        raise AssertionError("register_password_user must not create a dead-end account when SMTP is unavailable")

    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "password_auth_enabled", True)
    monkeypatch.setattr(web_auth.smtp_settings, "host", "")
    monkeypatch.setattr(web_auth.smtp_settings, "from_email", "")
    monkeypatch.setattr(web_auth.resend_settings, "api_key", None)
    monkeypatch.setattr(web_auth.resend_settings, "from_email", "")
    monkeypatch.setattr(web_auth, "enforce_password_email_rate_limit", _rate_limit)
    monkeypatch.setattr(web_auth, "create_web_user", _create_web_user)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.register_password_user("new@example.com", "password-123")

    assert exc_info.value.code == "password_email_delivery_disabled"
    assert exc_info.value.status_code == 503
    assert create_called is False


@pytest.mark.asyncio
async def test_send_auth_email_uses_resend_when_configured(monkeypatch):
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        status_code = 202

    class _FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return _FakeResponse()

    monkeypatch.setattr(web_auth.resend_settings, "api_key", SecretStr("re_test_key"))
    monkeypatch.setattr(web_auth.resend_settings, "from_email", "onboarding@resend.dev")
    monkeypatch.setattr(web_auth.resend_settings, "from_name", "Vectra Connect")
    monkeypatch.setattr(web_auth.resend_settings, "base_url", "https://api.resend.com")
    monkeypatch.setattr(web_auth.resend_settings, "timeout_seconds", 7.5)
    monkeypatch.setattr(web_auth.smtp_settings, "host", "smtp.example.test")
    monkeypatch.setattr(web_auth.smtp_settings, "from_email", "smtp@example.test")
    monkeypatch.setattr(web_auth.httpx, "AsyncClient", _FakeAsyncClient)

    sent = await web_auth.send_auth_email("user@example.com", "Hello", "Line 1\n\nLine 2")

    assert sent is True
    assert len(requests) == 1
    request = requests[0]
    assert request["url"] == "https://api.resend.com/emails"
    assert request["headers"] == {
        "Authorization": "Bearer re_test_key",
        "Content-Type": "application/json",
        "User-Agent": "vectra-connect-backend/1.0",
    }
    assert request["json"] == {
        "from": "Vectra Connect <onboarding@resend.dev>",
        "to": ["user@example.com"],
        "subject": "Hello",
        "text": "Line 1\n\nLine 2",
        "html": "<p>Line 1</p>\n<p>Line 2</p>",
    }
    assert request["timeout"] == 7.5


@pytest.mark.asyncio
async def test_password_register_hashes_password_off_event_loop(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _FakePasswordCredential:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {"email_normalized": "new@example.com"}
            return None

        @classmethod
        async def create(cls, **kwargs):
            calls.append(("credential_create", kwargs["password_hash"]))
            return types.SimpleNamespace(user_id=kwargs["user"].id)

    async def _to_thread(func, *args, **kwargs):
        calls.append(("to_thread", func.__name__))
        assert args == ("password-123",)
        return "hashed-password"

    async def _rate_limit(*_args, **_kwargs):
        return None

    async def _create_web_user(**_kwargs):
        return types.SimpleNamespace(id=web_auth.WEB_USER_ID_FLOOR + 31, full_name="New")

    async def _ensure_identity(*_args, **_kwargs):
        return None

    async def _send_verification(*_args, **_kwargs):
        return True

    async def _audit(**_kwargs):
        return None

    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "password_auth_enabled", True)
    monkeypatch.setattr(web_auth.smtp_settings, "host", "smtp.example.test")
    monkeypatch.setattr(web_auth.smtp_settings, "from_email", "noreply@example.test")
    monkeypatch.setattr(web_auth, "AuthPasswordCredential", _FakePasswordCredential)
    monkeypatch.setattr(web_auth, "enforce_password_email_rate_limit", _rate_limit)
    monkeypatch.setattr(web_auth, "create_web_user", _create_web_user)
    monkeypatch.setattr(web_auth, "ensure_identity_for_user", _ensure_identity)
    monkeypatch.setattr(web_auth, "_send_verification_email", _send_verification)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)
    monkeypatch.setattr(web_auth.asyncio, "to_thread", _to_thread)

    response = await web_auth.register_password_user("New@Example.COM", "password-123")

    assert response == {"ok": True, "emailVerificationRequired": True, "emailSent": True}
    assert ("to_thread", "hash_password") in calls
    assert ("credential_create", "hashed-password") in calls


@pytest.mark.asyncio
async def test_smtp_auth_email_runs_blocking_smtp_in_thread(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def _to_thread(func, *args, **kwargs):
        calls.append(("to_thread", func.__name__))
        assert args == ("user@example.com", "Hello", "Body")
        return True

    monkeypatch.setattr(web_auth.asyncio, "to_thread", _to_thread)

    assert await web_auth.send_auth_email_via_smtp("user@example.com", "Hello", "Body") is True
    assert calls == [("to_thread", "_send_auth_email_via_smtp_sync")]


@pytest.mark.asyncio
async def test_oauth_id_token_decode_runs_jwks_lookup_in_thread(monkeypatch):
    config = web_auth.ProviderConfig(
        provider="google",
        client_id="google-client-id",
        client_secret="google-secret",
        auth_url="https://accounts.example/auth",
        token_url="https://accounts.example/token",
        jwks_url="https://accounts.example/jwks",
        issuer="https://accounts.example",
        userinfo_url=None,
        scope="openid email profile",
    )
    state_row = types.SimpleNamespace(nonce="nonce", pkce_verifier="verifier")
    calls: list[tuple[str, object]] = []

    async def _exchange(*_args, **_kwargs):
        return {"id_token": "id-token"}

    async def _to_thread(func, *args, **kwargs):
        calls.append(("to_thread", func.__name__))
        assert args == (config, "id-token", "nonce")
        return {"sub": "google-sub", "nonce": "nonce", "email": "user@example.com"}

    monkeypatch.setattr(web_auth, "_exchange_oauth_code", _exchange)
    monkeypatch.setattr(web_auth.asyncio, "to_thread", _to_thread)

    profile = await web_auth.resolve_provider_profile(config, state_row, "code")

    assert profile.subject == "google-sub"
    assert calls == [("to_thread", "_decode_id_token")]


def test_password_email_delivery_enabled_with_resend(monkeypatch):
    monkeypatch.setattr(web_auth.smtp_settings, "host", "")
    monkeypatch.setattr(web_auth.smtp_settings, "from_email", "")
    monkeypatch.setattr(web_auth.resend_settings, "api_key", SecretStr("re_test_key"))
    monkeypatch.setattr(web_auth.resend_settings, "from_email", "onboarding@resend.dev")

    assert web_auth.is_password_email_delivery_enabled() is True


@pytest.mark.asyncio
async def test_password_register_resends_verification_for_existing_unverified_credential(monkeypatch):
    sent: dict[str, str] = {}
    saved_fields: list[list[str]] = []
    old_hash = "old-token-hash"

    class _ExistingCredential(types.SimpleNamespace):
        async def save(self, update_fields=None):
            saved_fields.append(list(update_fields or []))

    existing = _ExistingCredential(
        user_id=web_auth.WEB_USER_ID_FLOOR + 30,
        email_verified=False,
        verification_token_hash=old_hash,
        verification_expires_at=None,
    )

    class _FakePasswordCredential:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {"email_normalized": "new@example.com"}
            return existing

    async def _rate_limit(*_args, **_kwargs):
        return None

    async def _send_verification(email: str, token: str) -> bool:
        sent["email"] = email
        sent["token"] = token
        return True

    async def _audit(**_kwargs):
        return None

    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "password_auth_enabled", True)
    monkeypatch.setattr(web_auth.smtp_settings, "host", "smtp.example.test")
    monkeypatch.setattr(web_auth.smtp_settings, "from_email", "noreply@example.test")
    monkeypatch.setattr(web_auth, "AuthPasswordCredential", _FakePasswordCredential)
    monkeypatch.setattr(web_auth, "enforce_password_email_rate_limit", _rate_limit)
    monkeypatch.setattr(web_auth, "_send_verification_email", _send_verification)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)

    response = await web_auth.register_password_user("New@Example.COM", "password-123")

    assert response == {"ok": True, "emailVerificationRequired": True, "emailSent": True}
    assert sent["email"] == "new@example.com"
    assert sent["token"]
    assert existing.verification_token_hash != old_hash
    assert existing.verification_expires_at is not None
    assert saved_fields == [["verification_token_hash", "verification_expires_at", "updated_at"]]


@pytest.mark.asyncio
async def test_telegram_oauth_provider_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "oauth_telegram_enabled", False)
    monkeypatch.setattr(web_auth.oauth_settings, "enabled_providers", ["telegram"])
    monkeypatch.setattr(web_auth.oauth_settings, "telegram_client_id", "tg-client")
    monkeypatch.setattr(
        web_auth.oauth_settings,
        "telegram_client_secret",
        types.SimpleNamespace(get_secret_value=lambda: "tg-secret"),
    )

    assert web_auth.provider_is_enabled("telegram") is False


@pytest.mark.asyncio
async def test_telegram_oauth_start_uses_oidc_pkce_nonce(monkeypatch):
    created_rows: list[dict[str, object]] = []

    class _FakeOAuthState:
        @classmethod
        async def create(cls, **kwargs):
            created_rows.append(kwargs)
            return types.SimpleNamespace(**kwargs)

    monkeypatch.setattr(web_auth, "AuthOAuthState", _FakeOAuthState)
    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "oauth_telegram_enabled", True)
    monkeypatch.setattr(web_auth.oauth_settings, "enabled_providers", ["telegram"])
    monkeypatch.setattr(web_auth.oauth_settings, "telegram_client_id", "tg-client")
    monkeypatch.setattr(
        web_auth.oauth_settings,
        "telegram_client_secret",
        types.SimpleNamespace(get_secret_value=lambda: "tg-secret"),
    )

    authorization_url = await web_auth.create_oauth_authorization_url(
        provider="telegram",
        mode="login",
        return_to="/account/security",
    )

    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "oauth.telegram.org"
    assert params["client_id"] == ["tg-client"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"][0]
    assert params["nonce"][0]
    assert created_rows[0]["provider"] == "telegram"
    assert created_rows[0]["return_to"] == "/account/security"


@pytest.mark.asyncio
async def test_telegram_oauth_token_exchange_uses_basic_auth_pkce_and_client_id(monkeypatch):
    captured: dict[str, object] = {}
    config = web_auth.ProviderConfig(
        provider="telegram",
        client_id="tg-client",
        client_secret="tg-secret",
        auth_url="https://oauth.telegram.org/auth",
        token_url="https://oauth.telegram.org/token",
        jwks_url="https://oauth.telegram.org/.well-known/jwks.json",
        issuer="https://oauth.telegram.org",
        userinfo_url=None,
        scope="openid profile",
    )
    state_row = types.SimpleNamespace(pkce_verifier="pkce-verifier")

    class _Response:
        status_code = 200

        def json(self):
            return {"id_token": "id-token"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, headers=None):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(web_auth.httpx, "AsyncClient", _Client)

    payload = await web_auth._exchange_oauth_code(config, state_row, "oauth-code")

    expected_basic = base64.b64encode(b"tg-client:tg-secret").decode("ascii")
    assert payload == {"id_token": "id-token"}
    assert captured["url"] == "https://oauth.telegram.org/token"
    assert captured["data"] == {
        "grant_type": "authorization_code",
        "code": "oauth-code",
        "redirect_uri": web_auth.oauth_callback_url("telegram"),
        "code_verifier": "pkce-verifier",
        "client_id": "tg-client",
    }
    assert captured["headers"]["Authorization"] == f"Basic {expected_basic}"


@pytest.mark.asyncio
async def test_telegram_oauth_profile_requires_numeric_id(monkeypatch):
    config = web_auth.ProviderConfig(
        provider="telegram",
        client_id="tg-client",
        client_secret="tg-secret",
        auth_url="https://oauth.telegram.org/auth",
        token_url="https://oauth.telegram.org/token",
        jwks_url="https://oauth.telegram.org/.well-known/jwks.json",
        issuer="https://oauth.telegram.org",
        userinfo_url=None,
        scope="openid profile",
    )
    state_row = types.SimpleNamespace(nonce="nonce", pkce_verifier="verifier")

    async def _exchange(*_args, **_kwargs):
        return {"id_token": "token"}

    monkeypatch.setattr(web_auth, "_exchange_oauth_code", _exchange)
    monkeypatch.setattr(
        web_auth,
        "_decode_id_token",
        lambda *_args: {"sub": "not-numeric", "nonce": "nonce"},
    )

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.resolve_provider_profile(config, state_row, "code")

    assert exc_info.value.code == "missing_telegram_id"
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_telegram_oauth_profile_does_not_treat_sub_as_telegram_id(monkeypatch):
    config = web_auth.ProviderConfig(
        provider="telegram",
        client_id="tg-client",
        client_secret="tg-secret",
        auth_url="https://oauth.telegram.org/auth",
        token_url="https://oauth.telegram.org/token",
        jwks_url="https://oauth.telegram.org/.well-known/jwks.json",
        issuer="https://oauth.telegram.org",
        userinfo_url=None,
        scope="openid profile",
    )
    state_row = types.SimpleNamespace(nonce="nonce", pkce_verifier="verifier")

    async def _exchange(*_args, **_kwargs):
        return {"id_token": "token"}

    monkeypatch.setattr(web_auth, "_exchange_oauth_code", _exchange)
    monkeypatch.setattr(
        web_auth,
        "_decode_id_token",
        lambda *_args: {"sub": "123456789", "nonce": "nonce"},
    )

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.resolve_provider_profile(config, state_row, "code")

    assert exc_info.value.code == "missing_telegram_id"
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_telegram_oauth_link_returns_canonical_telegram_ticket(monkeypatch):
    source_user = types.SimpleNamespace(id=web_auth.WEB_USER_ID_FLOOR + 20)
    target_user = types.SimpleNamespace(id=123456, auth_token_version=0)
    state_row = types.SimpleNamespace(mode="link", linking_user_id=source_user.id, return_to="/account/security")
    profile = web_auth.ProviderProfile(provider="telegram", subject="123456", display_name="Telegram User")

    async def _consume(provider, state):
        assert provider == "telegram"
        assert state == "state"
        return state_row

    async def _profile(_config, _state_row, code):
        assert code == "code"
        return profile

    async def _get_or_none(**kwargs):
        assert kwargs == {"id": source_user.id}
        return source_user

    async def _merge(user, telegram_user):
        assert user is source_user
        assert telegram_user.id == 123456
        return target_user, True

    async def _audit(**_kwargs):
        return None

    async def _ticket(user):
        assert user is target_user
        return "ticket-telegram"

    monkeypatch.setattr(web_auth, "provider_is_enabled", lambda provider: provider == "telegram")
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: types.SimpleNamespace(provider=provider))
    monkeypatch.setattr(web_auth, "_consume_oauth_state", _consume)
    monkeypatch.setattr(web_auth, "resolve_provider_profile", _profile)
    monkeypatch.setattr(web_auth.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(web_auth, "merge_source_user_into_telegram_user", _merge)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)
    monkeypatch.setattr(web_auth, "create_login_ticket", _ticket)

    ticket, return_to = await web_auth.handle_oauth_callback("telegram", "code", "state")

    assert ticket == "ticket-telegram"
    assert return_to == "/account/security"


@pytest.mark.asyncio
async def test_web_oauth_created_user_sends_admin_registration_log(monkeypatch):
    user = types.SimpleNamespace(id=web_auth.WEB_USER_ID_FLOOR + 42)
    state_row = types.SimpleNamespace(
        mode="login",
        linking_user_id=None,
        return_to="/connect",
        start_param=None,
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="google-subject",
        email="user@example.com",
        email_verified=True,
        display_name="Web User",
    )
    calls: list[tuple[str, object]] = []

    async def _consume(provider, state):
        assert provider == "google"
        assert state == "state"
        return state_row

    async def _profile(_config, _state_row, code):
        assert code == "code"
        return profile

    async def _get_or_create(resolved_profile):
        assert resolved_profile is profile
        return user, True

    async def _notify(created_user, resolved_profile, provider, *, start_param=None):
        calls.append(("notify", (created_user.id, resolved_profile.provider, provider, start_param)))

    async def _audit(**kwargs):
        calls.append(("audit", kwargs["reason"]))

    async def _ticket(created_user):
        assert created_user is user
        return "ticket-web"

    monkeypatch.setattr(web_auth, "provider_is_enabled", lambda provider: provider == "google")
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: types.SimpleNamespace(provider=provider))
    monkeypatch.setattr(web_auth, "_consume_oauth_state", _consume)
    monkeypatch.setattr(web_auth, "resolve_provider_profile", _profile)
    monkeypatch.setattr(web_auth, "get_or_create_user_for_oauth_profile", _get_or_create)
    monkeypatch.setattr(web_auth, "notify_web_oauth_registration", _notify)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)
    monkeypatch.setattr(web_auth, "create_login_ticket", _ticket)

    ticket, return_to = await web_auth.handle_oauth_callback("google", "code", "state")

    assert ticket == "ticket-web"
    assert return_to == "/connect"
    assert ("notify", (user.id, "google", "google", None)) in calls
    assert ("audit", "created") in calls


@pytest.mark.asyncio
async def test_apply_web_referral_attribution_saves_utm_when_referrer_invalid(monkeypatch):
    """A start_param can carry a campaign tag (utm) even when the referenced
    referrer no longer exists — e.g. partner QR was deleted, or the partner
    user was removed but the live rutracker link still carries the token.
    Marketing attribution should still capture the tag; only the referred_by
    foreign key requires a valid referrer behind it.
    """
    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 200,
        referred_by=0,
        utm=None,
    )
    save_calls: list[dict[str, list[str]]] = []

    async def _save(**kwargs):
        save_calls.append(kwargs)

    async def _has_material_data(user_id):
        return False

    async def _resolve(start_param, **kwargs):
        # Partner QR that resolved a utm but no live owner (referrer_id=0).
        return 0, "qr_rt_launch_2026_05_hero"

    async def _get_or_none(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("Users.get_or_none must not run when referrer_id=0")

    user.save = _save
    monkeypatch.setattr(web_auth, "user_has_material_data", _has_material_data)
    monkeypatch.setattr(web_auth, "resolve_referral_from_start_param", _resolve)
    monkeypatch.setattr(web_auth.Users, "get_or_none", _get_or_none)

    await web_auth.apply_web_referral_attribution(user, "qr_rt_launch_2026_05_hero")

    # UTM is persisted; referred_by stays unset.
    assert user.utm == "qr_rt_launch_2026_05_hero"
    assert user.referred_by == 0
    assert save_calls == [{"update_fields": ["utm"]}]


@pytest.mark.asyncio
async def test_apply_web_referral_attribution_skips_self_referral_entirely(monkeypatch):
    """A self-referral attempt (start_param resolves to user.id) is a gaming
    attempt — neither the referrer link nor the utm should be bound."""
    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 201,
        referred_by=0,
        utm=None,
    )

    async def _save(**_kwargs):
        raise AssertionError("self-referral must not persist any fields")

    async def _has_material_data(_user_id):
        return False

    async def _resolve(_start_param, **_kwargs):
        return user.id, "qr_self_token"

    user.save = _save
    monkeypatch.setattr(web_auth, "user_has_material_data", _has_material_data)
    monkeypatch.setattr(web_auth, "resolve_referral_from_start_param", _resolve)

    await web_auth.apply_web_referral_attribution(user, "qr_self_token")

    assert user.utm is None
    assert user.referred_by == 0


@pytest.mark.asyncio
async def test_oauth_start_persists_start_param_on_state_row(monkeypatch):
    created_rows: list[dict[str, object]] = []

    class _FakeOAuthState:
        @classmethod
        async def create(cls, **kwargs):
            created_rows.append(kwargs)
            return types.SimpleNamespace(**kwargs)

    provider_config = web_auth.ProviderConfig(
        provider="google",
        client_id="google-client-id",
        client_secret="google-secret",
        auth_url="https://accounts.example/auth",
        token_url="https://accounts.example/token",
        jwks_url="https://accounts.example/jwks",
        issuer="https://accounts.example",
        userinfo_url="https://accounts.example/userinfo",
        scope="openid email profile",
    )

    monkeypatch.setattr(web_auth, "AuthOAuthState", _FakeOAuthState)
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: provider_config)
    monkeypatch.setattr(web_auth.web_auth_settings, "web_auth_enabled", True)
    monkeypatch.setattr(web_auth.web_auth_settings, "oauth_google_enabled", True)
    monkeypatch.setattr(web_auth.oauth_settings, "enabled_providers", ["google"])

    await web_auth.create_oauth_authorization_url(
        provider="google",
        mode="login",
        return_to="/welcome",
        start_param="qr_rt_launch_2026_05_hero",
    )

    assert len(created_rows) == 1
    assert created_rows[0]["start_param"] == "qr_rt_launch_2026_05_hero"

    # Empty / whitespace-only start_param is normalized to None (the DB column
    # is nullable, so we never write empty strings).
    await web_auth.create_oauth_authorization_url(
        provider="google",
        mode="login",
        return_to="/welcome",
        start_param="   ",
    )
    assert created_rows[1]["start_param"] is None

    # Overlong input is silently truncated to the DB column limit (256) so a
    # crafted long ?start= cannot blow up the OAuth start endpoint.
    await web_auth.create_oauth_authorization_url(
        provider="google",
        mode="login",
        return_to="/welcome",
        start_param="x" * 1000,
    )
    assert len(created_rows[2]["start_param"]) == 256

    # Internal whitespace (newlines, tabs from URL-decoded %0A/%09, runs of
    # spaces) is collapsed so it cannot fragment the Telegram <code> rendering
    # in the admin notification text.
    await web_auth.create_oauth_authorization_url(
        provider="google",
        mode="login",
        return_to="/welcome",
        start_param="qr_rt\n\tlaunch  \r\n2026",
    )
    assert created_rows[3]["start_param"] == "qr_rt launch 2026"


@pytest.mark.asyncio
async def test_web_oauth_callback_applies_attribution_before_notifying(monkeypatch):
    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 99,
        utm=None,
        referred_by=0,
    )

    async def _refresh(self, fields=None):
        # Simulate the post-attribution DB read: pretend apply_web_referral_attribution
        # persisted utm=qr_rt_launch_2026_05_hero and referred_by=42.
        self.utm = "qr_rt_launch_2026_05_hero"
        self.referred_by = 42

    user.refresh_from_db = types.MethodType(_refresh, user)

    state_row = types.SimpleNamespace(
        mode="login",
        linking_user_id=None,
        return_to="/connect",
        start_param="qr_rt_launch_2026_05_hero",
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="google-subject",
        email="zenaelsukov27@example.com",
        email_verified=True,
        display_name="Женя Елсуков",
    )
    timeline: list[str] = []
    attribution_calls: list[tuple[object, str | None]] = []
    notify_args: list[tuple[object, ...]] = []

    async def _consume(provider, state):
        return state_row

    async def _profile(_config, _state_row, code):
        return profile

    async def _get_or_create(resolved_profile):
        return user, True

    async def _apply(target_user, raw_start_param):
        attribution_calls.append((target_user.id, raw_start_param))
        timeline.append("attribution")

    async def _notify(created_user, resolved_profile, provider, *, start_param=None):
        notify_args.append((created_user.id, created_user.utm, created_user.referred_by, provider, start_param))
        timeline.append("notify")

    async def _audit(**kwargs):
        timeline.append(f"audit:{kwargs['reason']}")

    async def _ticket(created_user):
        return "ticket-web"

    monkeypatch.setattr(web_auth, "provider_is_enabled", lambda provider: provider == "google")
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: types.SimpleNamespace(provider=provider))
    monkeypatch.setattr(web_auth, "_consume_oauth_state", _consume)
    monkeypatch.setattr(web_auth, "resolve_provider_profile", _profile)
    monkeypatch.setattr(web_auth, "get_or_create_user_for_oauth_profile", _get_or_create)
    monkeypatch.setattr(web_auth, "apply_web_referral_attribution", _apply)
    monkeypatch.setattr(web_auth, "notify_web_oauth_registration", _notify)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)
    monkeypatch.setattr(web_auth, "create_login_ticket", _ticket)

    await web_auth.handle_oauth_callback("google", "code", "state")

    assert attribution_calls == [(user.id, "qr_rt_launch_2026_05_hero")]
    # Attribution must run before the admin notification — otherwise the
    # notification still sees the pre-attribution state.
    assert timeline.index("attribution") < timeline.index("notify")
    # And the notification must observe the refreshed utm/referred_by values
    # AND receive the raw captured start_param so the operator can distinguish
    # "no UTM" from "UTM didn't resolve".
    assert notify_args == [
        (user.id, "qr_rt_launch_2026_05_hero", 42, "google", "qr_rt_launch_2026_05_hero"),
    ]


@pytest.mark.asyncio
async def test_web_oauth_callback_notifies_even_when_attribution_raises(monkeypatch):
    """Attribution errors must never block login or the admin notification.
    The post-attribution refresh still runs so the notification cannot read
    half-written in-memory fields left behind by a partial save."""
    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 100,
        utm=None,
        referred_by=0,
    )
    refresh_calls: list[tuple[str, ...]] = []

    async def _refresh(self, fields=None):
        refresh_calls.append(tuple(fields or ()))

    user.refresh_from_db = types.MethodType(_refresh, user)

    state_row = types.SimpleNamespace(
        mode="login",
        linking_user_id=None,
        return_to="/connect",
        start_param="qr_rt_launch_2026_05_hero",
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="google-subject",
        email="user@example.com",
        email_verified=True,
        display_name="User",
    )
    notify_args: list[tuple[object, ...]] = []

    async def _consume(provider, state):
        return state_row

    async def _profile(_config, _state_row, code):
        return profile

    async def _get_or_create(resolved_profile):
        return user, True

    async def _apply(target_user, raw_start_param):
        raise RuntimeError("simulated transient DB outage")

    async def _notify(created_user, resolved_profile, provider, *, start_param=None):
        notify_args.append((created_user.id, provider, start_param))

    async def _audit(**kwargs):
        return None

    async def _ticket(created_user):
        return "ticket-web"

    monkeypatch.setattr(web_auth, "provider_is_enabled", lambda provider: provider == "google")
    monkeypatch.setattr(web_auth, "get_provider_config", lambda provider: types.SimpleNamespace(provider=provider))
    monkeypatch.setattr(web_auth, "_consume_oauth_state", _consume)
    monkeypatch.setattr(web_auth, "resolve_provider_profile", _profile)
    monkeypatch.setattr(web_auth, "get_or_create_user_for_oauth_profile", _get_or_create)
    monkeypatch.setattr(web_auth, "apply_web_referral_attribution", _apply)
    monkeypatch.setattr(web_auth, "notify_web_oauth_registration", _notify)
    monkeypatch.setattr(web_auth, "audit_auth_event", _audit)
    monkeypatch.setattr(web_auth, "create_login_ticket", _ticket)

    ticket, _ = await web_auth.handle_oauth_callback("google", "code", "state")

    assert ticket == "ticket-web"
    assert notify_args == [(user.id, "google", "qr_rt_launch_2026_05_hero")]
    # The refresh must run even when attribution raised, so the notification
    # never sees in-memory mutations left behind by a partial save.
    assert refresh_calls == [("utm", "referred_by")]


@pytest.mark.asyncio
async def test_notify_web_oauth_registration_includes_utm_and_referrer(monkeypatch):
    sent: list[str] = []

    async def _send(text):
        sent.append(text)
        return True

    # Other test modules permanently replace `sys.modules["bloobcat.bot.notifications.admin"]`
    # with a synthetic stub (see tests/_payment_test_stubs.py). Patch the live
    # `sys.modules` entry — that is exactly what `notify_web_oauth_registration`'s
    # in-function `from … import …` resolves through.
    import sys as _sys
    admin_mod = _sys.modules["bloobcat.bot.notifications.admin"]
    monkeypatch.setattr(admin_mod, "send_admin_message", _send, raising=False)

    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 1,
        utm="qr_rt_launch_2026_05_hero",
        referred_by=42,
        full_name="Test User",
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="g",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
    )

    await web_auth.notify_web_oauth_registration(
        user, profile, "google", start_param="qr_rt_launch_2026_05_hero"
    )

    assert len(sent) == 1
    body = sent[0]
    assert "Новая web-регистрация" in body
    assert "qr_rt_launch_2026_05_hero" in body
    assert "📊 UTM" in body
    assert "🤝 Реферер" in body
    assert "<code>42</code>" in body
    # When start_param matches the bound UTM (the common qr_<token> case) the
    # raw start_param line is suppressed to avoid redundant duplication.
    assert "🏷️ start_param" not in body


@pytest.mark.asyncio
async def test_notify_web_oauth_registration_surfaces_unresolved_start_param(monkeypatch):
    """If a start_param was captured but didn't resolve to a UTM (deleted QR,
    invalid token) the raw start_param must still be visible to the operator —
    otherwise it reads as 'arrived organically' and we silently lose campaign
    attribution insight."""
    sent: list[str] = []

    async def _send(text):
        sent.append(text)
        return True

    import sys as _sys
    admin_mod = _sys.modules["bloobcat.bot.notifications.admin"]
    monkeypatch.setattr(admin_mod, "send_admin_message", _send, raising=False)

    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 3,
        utm=None,
        referred_by=0,
        full_name="Test User",
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="g",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
    )

    await web_auth.notify_web_oauth_registration(
        user, profile, "google", start_param="qr_deleted_token_2026"
    )

    body = sent[0]
    assert "📊 UTM: —" in body
    assert "🏷️ start_param: <code>qr_deleted_token_2026</code>" in body


@pytest.mark.asyncio
async def test_notify_web_oauth_registration_shows_dash_when_utm_missing(monkeypatch):
    """No UTM in user record → notification renders an explicit '—' so the
    operator can distinguish 'arrived without UTM' from a stale missing field."""
    sent: list[str] = []

    async def _send(text):
        sent.append(text)
        return True

    # Other test modules permanently replace `sys.modules["bloobcat.bot.notifications.admin"]`
    # with a synthetic stub (see tests/_payment_test_stubs.py). Patch the live
    # `sys.modules` entry — that is exactly what `notify_web_oauth_registration`'s
    # in-function `from … import …` resolves through.
    import sys as _sys
    admin_mod = _sys.modules["bloobcat.bot.notifications.admin"]
    monkeypatch.setattr(admin_mod, "send_admin_message", _send, raising=False)

    user = types.SimpleNamespace(
        id=web_auth.WEB_USER_ID_FLOOR + 2,
        utm=None,
        referred_by=0,
        full_name="Test User",
    )
    profile = web_auth.ProviderProfile(
        provider="google",
        subject="g",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
    )

    await web_auth.notify_web_oauth_registration(user, profile, "google")

    body = sent[0]
    assert "📊 UTM: —" in body
    # No referrer line when referred_by is empty — keeps the message compact.
    assert "🤝 Реферер" not in body
    # No start_param line when nothing was captured — distinguishes
    # "truly arrived without a tag" from the unresolved-start_param case.
    assert "🏷️ start_param" not in body


@pytest.mark.asyncio
async def test_consume_login_ticket_is_atomic(monkeypatch):
    class _TicketQuery:
        async def update(self, **kwargs):
            assert "consumed_at" in kwargs
            return 0

    class _FakeLoginTicket:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs["consumed_at__isnull"] is True
            return _TicketQuery()

        @classmethod
        async def get_or_none(cls, **_kwargs):
            return types.SimpleNamespace(consumed_at=None)

    monkeypatch.setattr(web_auth, "AuthLoginTicket", _FakeLoginTicket)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.exchange_login_ticket("ticket")

    assert exc_info.value.code == "invalid_ticket"


@pytest.mark.asyncio
async def test_material_data_blocks_partner_and_promo_state(monkeypatch):
    async def _user_partner(**kwargs):
        assert kwargs == {"id": 42}
        return types.SimpleNamespace(id=42, is_partner=True)

    monkeypatch.setattr(web_auth.Users, "get_or_none", _user_partner)
    assert await web_auth.user_has_material_data(42) is True

    async def _empty_user(**_kwargs):
        return types.SimpleNamespace(id=42)

    class _ExistsQuery:
        def __init__(self, exists=False):
            self._exists = exists

        async def exists(self):
            return self._exists

    class _NoRows:
        @classmethod
        def filter(cls, **_kwargs):
            return _ExistsQuery(False)

    class _PromoRows:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs == {"user_id": 42}
            return _ExistsQuery(True)

    monkeypatch.setattr(web_auth.Users, "get_or_none", _empty_user)
    for attr in [
        "ActiveTariffs",
        "ProcessedPayments",
        "FamilyMembers",
        "FamilyInvites",
        "FamilyDevices",
        "PartnerEarnings",
        "PartnerQr",
        "PartnerWithdrawals",
        "ReferralRewards",
        "ReferralLevelRewards",
        "PersonalDiscount",
        "PrizeWheelHistory",
        "SubscriptionFreezes",
        "RemnaWaveRetryJobs",
    ]:
        monkeypatch.setattr(web_auth, attr, _NoRows)
    monkeypatch.setattr(web_auth, "PromoUsage", _PromoRows)

    assert await web_auth.user_has_material_data(42) is True
