from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import smtplib
import types
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import bcrypt
import httpx
import jwt
from fastapi import HTTPException, Request, status
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.auth import (
    AuthAuditEvent,
    AuthIdentity,
    AuthLinkRequest,
    AuthLoginTicket,
    AuthOAuthState,
    AuthPasswordCredential,
)
from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.family_devices import FamilyDevices
from bloobcat.db.family_invites import FamilyInvites
from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.db.partner_withdrawals import PartnerWithdrawals
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.prize_wheel import PrizeWheelHistory
from bloobcat.db.promotions import PromoUsage
from bloobcat.db.referral_rewards import ReferralLevelRewards, ReferralRewards
from bloobcat.db.remnawave_retry_jobs import RemnaWaveRetryJobs
from bloobcat.db.subscription_freezes import SubscriptionFreezes
from bloobcat.db.users import Users, get_family_url
from bloobcat.funcs.auth_tokens import create_access_token, decode_access_token
from bloobcat.logger import get_logger
from bloobcat.middleware.rate_limit import RateLimiter
from bloobcat.settings import (
    auth_settings,
    app_settings,
    oauth_settings,
    resend_settings,
    script_settings,
    smtp_settings,
    telegram_settings,
    web_auth_settings,
)

logger = get_logger("web_auth")

Provider = Literal["google", "apple", "yandex", "telegram", "password"]
OAuthProvider = Literal["google", "apple", "yandex", "telegram"]
OAuthMode = Literal["login", "link"]

SUPPORTED_OAUTH_PROVIDERS: tuple[OAuthProvider, ...] = ("google", "apple", "yandex", "telegram")
WEB_USER_ID_FLOOR = 8_000_000_000_000_000
WEB_USER_ID_CEILING = 9_007_199_254_740_000  # below JS Number.MAX_SAFE_INTEGER
OAUTH_STATE_TTL_SECONDS = 10 * 60
LOGIN_TICKET_TTL_SECONDS = 5 * 60
LINK_TOKEN_TTL_SECONDS = 10 * 60
EMAIL_TOKEN_TTL_SECONDS = 30 * 60
RESET_TOKEN_TTL_SECONDS = 30 * 60
PASSWORD_EMAIL_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
password_email_limiter = RateLimiter(
    requests_per_minute=8,
    window_seconds=PASSWORD_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
    namespace="password_email",
)

GENERIC_PASSWORD_RESPONSE = {
    "ok": True,
    "emailVerificationRequired": True,
}


@dataclass(frozen=True)
class ProviderProfile:
    provider: Provider
    subject: str
    email: str | None = None
    email_verified: bool = False
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    provider: OAuthProvider
    client_id: str
    client_secret: str | None
    auth_url: str
    token_url: str
    jwks_url: str | None
    issuer: str | None
    userinfo_url: str | None
    scope: str
    response_mode: str | None = None


class WebAuthError(Exception):
    def __init__(self, code: str, message: str | None = None, status_code: int = 400):
        super().__init__(message or code)
        self.code = code
        self.status_code = status_code


PUBLIC_WEB_AUTH_ERROR_MESSAGES: dict[str, str] = {
    "registration_sync_pending": "Аккаунт ещё настраивается. Попробуйте снова через несколько секунд.",
    "password_email_delivery_disabled": "Регистрация и сброс пароля по email временно недоступны.",
    "web_auth_disabled": "Вход через сайт пока настраивается.",
    "password_auth_disabled": "Вход по email пока недоступен.",
    "rate_limited": "Слишком много попыток. Попробуйте позже.",
    "invalid_credentials": "Неверный email или пароль.",
    "invalid_verification_token": "Ссылка подтверждения недействительна или устарела.",
    "invalid_reset_token": "Ссылка сброса пароля недействительна или устарела.",
    "last_identity": "Нельзя отключить последний способ входа.",
    "identity_not_found": "Этот способ входа уже не подключён.",
    "cannot_unlink_primary_telegram": "Основной Telegram-вход нельзя отключить.",
    "merge_requires_support": "Автоматически объединить эти аккаунты нельзя. Напишите в поддержку.",
}


def public_web_auth_error_message(error: WebAuthError) -> str:
    return PUBLIC_WEB_AUTH_ERROR_MESSAGES.get(
        error.code,
        "Не удалось выполнить действие. Попробуйте позже.",
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _secret_key_bytes() -> bytes:
    return auth_settings.jwt_secret.get_secret_value().encode("utf-8")


def hash_secret(value: str) -> str:
    return hmac.new(_secret_key_bytes(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_public_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    value = email.strip().lower()
    if not value or "@" not in value or len(value) > 255:
        return None
    return value


def is_web_user_id(user_id: int | str | None) -> bool:
    try:
        return int(user_id) >= WEB_USER_ID_FLOOR
    except (TypeError, ValueError):
        return False


def _safe_return_to(return_to: str | None) -> str:
    raw = (return_to or "/").strip() or "/"
    if len(raw) > 512:
        return "/"
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        frontend = (oauth_settings.frontend_app_url or "").rstrip("/")
        if frontend and raw.startswith(f"{frontend}/"):
            suffix = raw[len(frontend) :]
            return suffix or "/"
        return "/"
    if not raw.startswith("/"):
        return "/"
    return raw


def get_client_ip_hash(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    host = request.client.host or ""
    if not host:
        return None
    return hash_secret(host)


async def enforce_password_email_rate_limit(
    normalized_email: str,
    *,
    action: str,
    request: Request | None = None,
) -> None:
    key = f"{action}:{hash_secret(normalized_email)}"
    allowed, wait_time = await password_email_limiter.is_allowed(key)
    if allowed:
        return
    await audit_auth_event(
        action=action,
        result="rate_limited",
        provider="password",
        reason="email_rate_limited",
        request=request,
    )
    raise WebAuthError(
        "rate_limited",
        f"Too many attempts. Try again in {wait_time or 1} seconds.",
        status_code=429,
    )


async def audit_auth_event(
    *,
    action: str,
    result: str,
    provider: str | None = None,
    user_id: int | None = None,
    reason: str | None = None,
    request: Request | None = None,
) -> None:
    try:
        await AuthAuditEvent.create(
            user_id=user_id,
            provider=provider,
            action=action[:64],
            result=result[:32],
            reason=(reason or "")[:128] or None,
            ip_hash=get_client_ip_hash(request),
        )
    except Exception as exc:  # non-critical diagnostics only
        logger.debug("auth audit event skipped: %s", exc)


def provider_is_enabled(provider: OAuthProvider) -> bool:
    if not web_auth_settings.web_auth_enabled:
        return False
    enabled = {p.strip().lower() for p in (oauth_settings.enabled_providers or [])}
    if provider not in enabled:
        return False
    provider_flags = {
        "google": web_auth_settings.oauth_google_enabled,
        "apple": web_auth_settings.oauth_apple_enabled,
        "yandex": web_auth_settings.oauth_yandex_enabled,
        "telegram": web_auth_settings.oauth_telegram_enabled,
    }
    return bool(provider_flags.get(provider)) and get_provider_config(provider) is not None


def get_enabled_oauth_providers() -> list[dict[str, str]]:
    labels = {"google": "Google", "apple": "Apple", "yandex": "Yandex", "telegram": "Telegram"}
    return [
        {"provider": provider, "label": labels[provider]}
        for provider in SUPPORTED_OAUTH_PROVIDERS
        if provider_is_enabled(provider)
    ]


def _public_base_url() -> str:
    return (oauth_settings.public_base_url or script_settings.api_url).rstrip("/")


def oauth_callback_url(provider: OAuthProvider) -> str:
    return f"{_public_base_url()}/auth/oauth/{provider}/callback"


def frontend_callback_url(ticket: str | None = None, error: str | None = None, return_to: str | None = None) -> str:
    base = (oauth_settings.frontend_app_url or "").rstrip("/") or "/"
    query: dict[str, str] = {}
    if ticket:
        query["ticket"] = ticket
    if error:
        query["error"] = error
    if return_to:
        query["returnTo"] = _safe_return_to(return_to)
    qs = urlencode(query)
    return f"{base}/auth/callback{('?' + qs) if qs else ''}"


def _clean_private_key(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if "BEGIN PRIVATE KEY" in value:
        return value.replace("\\n", "\n")
    return value


def _apple_client_secret() -> str | None:
    if oauth_settings.apple_client_secret:
        return oauth_settings.apple_client_secret.get_secret_value()
    private_key = _clean_private_key(
        oauth_settings.apple_private_key.get_secret_value()
        if oauth_settings.apple_private_key
        else None
    )
    if not (
        oauth_settings.apple_client_id
        and oauth_settings.apple_team_id
        and oauth_settings.apple_key_id
        and private_key
    ):
        return None
    issued_at = now_utc()
    payload = {
        "iss": oauth_settings.apple_team_id,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(days=30)).timestamp()),
        "aud": "https://appleid.apple.com",
        "sub": oauth_settings.apple_client_id,
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": oauth_settings.apple_key_id},
    )


def get_provider_config(provider: OAuthProvider) -> ProviderConfig | None:
    if provider == "google":
        if not (oauth_settings.google_client_id and oauth_settings.google_client_secret):
            return None
        return ProviderConfig(
            provider="google",
            client_id=oauth_settings.google_client_id,
            client_secret=oauth_settings.google_client_secret.get_secret_value(),
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            jwks_url="https://www.googleapis.com/oauth2/v3/certs",
            issuer="https://accounts.google.com",
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            scope="openid email profile",
        )
    if provider == "apple":
        client_secret = _apple_client_secret()
        if not (oauth_settings.apple_client_id and client_secret):
            return None
        return ProviderConfig(
            provider="apple",
            client_id=oauth_settings.apple_client_id,
            client_secret=client_secret,
            auth_url="https://appleid.apple.com/auth/authorize",
            token_url="https://appleid.apple.com/auth/token",
            jwks_url="https://appleid.apple.com/auth/keys",
            issuer="https://appleid.apple.com",
            userinfo_url=None,
            scope="name email",
            response_mode="form_post",
        )
    if provider == "yandex":
        if not (oauth_settings.yandex_client_id and oauth_settings.yandex_client_secret):
            return None
        return ProviderConfig(
            provider="yandex",
            client_id=oauth_settings.yandex_client_id,
            client_secret=oauth_settings.yandex_client_secret.get_secret_value(),
            auth_url="https://oauth.yandex.com/authorize",
            token_url="https://oauth.yandex.com/token",
            jwks_url=None,
            issuer=None,
            userinfo_url="https://login.yandex.ru/info?format=json",
            scope="login:email login:info",
        )
    if provider == "telegram":
        if not (oauth_settings.telegram_client_id and oauth_settings.telegram_client_secret):
            return None
        return ProviderConfig(
            provider="telegram",
            client_id=oauth_settings.telegram_client_id,
            client_secret=oauth_settings.telegram_client_secret.get_secret_value(),
            auth_url="https://oauth.telegram.org/auth",
            token_url="https://oauth.telegram.org/token",
            jwks_url="https://oauth.telegram.org/.well-known/jwks.json",
            issuer="https://oauth.telegram.org",
            userinfo_url=None,
            scope="openid profile",
        )
    return None


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def resolve_optional_bearer_user(request: Request | None) -> Users | None:
    if request is None:
        return None
    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub") or payload.get("user_id"))
    except Exception:
        return None
    user = await Users.get_or_none(id=user_id)
    if not user:
        return None
    token_version = payload.get("ver")
    if token_version is not None and int(token_version) != int(user.auth_token_version or 0):
        return None
    return user


async def create_oauth_authorization_url(
    *,
    provider: OAuthProvider,
    mode: OAuthMode,
    return_to: str | None,
    request: Request | None = None,
) -> str:
    config = get_provider_config(provider)
    if not provider_is_enabled(provider) or config is None:
        raise WebAuthError("provider_disabled", status_code=404)
    if mode not in {"login", "link"}:
        raise WebAuthError("invalid_mode", status_code=400)

    linking_user_id: int | None = None
    if mode == "link":
        user = await resolve_optional_bearer_user(request)
        if not user:
            raise WebAuthError("link_requires_auth", status_code=401)
        linking_user_id = int(user.id)

    state = generate_public_token(32)
    nonce = generate_public_token(24)
    verifier = generate_public_token(64)
    await AuthOAuthState.create(
        state_hash=hash_secret(state),
        provider=provider,
        mode=mode,
        nonce=nonce,
        pkce_verifier=verifier,
        linking_user_id=linking_user_id,
        return_to=_safe_return_to(return_to),
        expires_at=now_utc() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
    )

    params = {
        "client_id": config.client_id,
        "redirect_uri": oauth_callback_url(provider),
        "response_type": "code",
        "scope": config.scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if config.response_mode:
        params["response_mode"] = config.response_mode
    return f"{config.auth_url}?{urlencode(params)}"


async def _consume_oauth_state(provider: OAuthProvider, state: str) -> AuthOAuthState:
    state_hash = hash_secret(state)
    consumed_at = now_utc()
    updated = await AuthOAuthState.filter(
        state_hash=state_hash,
        provider=provider,
        consumed_at__isnull=True,
        expires_at__gte=consumed_at,
    ).update(consumed_at=consumed_at)
    state_row = await AuthOAuthState.get_or_none(state_hash=state_hash, provider=provider)
    if not state_row:
        raise WebAuthError("invalid_state", status_code=403)
    if updated != 1:
        if state_row.consumed_at is not None:
            raise WebAuthError("state_consumed", status_code=403)
        if state_row.expires_at < consumed_at:
            raise WebAuthError("state_expired", status_code=403)
        raise WebAuthError("invalid_state", status_code=403)
    return state_row


async def _consume_login_ticket(ticket: str) -> AuthLoginTicket:
    ticket_hash = hash_secret(ticket)
    consumed_at = now_utc()
    updated = await AuthLoginTicket.filter(
        ticket_hash=ticket_hash,
        consumed_at__isnull=True,
        expires_at__gte=consumed_at,
    ).update(consumed_at=consumed_at)
    row = await AuthLoginTicket.get_or_none(ticket_hash=ticket_hash)
    if not row or updated != 1:
        raise WebAuthError("invalid_ticket", status_code=403)
    return row


async def _consume_link_request(source_user: Users, token: str) -> AuthLinkRequest:
    token_hash = hash_secret(token)
    consumed_at = now_utc()
    updated = await AuthLinkRequest.filter(
        token_hash=token_hash,
        target_provider="telegram",
        source_user_id=source_user.id,
        consumed_at__isnull=True,
        expires_at__gte=consumed_at,
    ).update(consumed_at=consumed_at)
    row = await AuthLinkRequest.get_or_none(token_hash=token_hash, target_provider="telegram")
    if not row:
        raise WebAuthError("invalid_link_token", status_code=403)
    if int(row.source_user_id) != int(source_user.id):
        raise WebAuthError("link_user_mismatch", status_code=403)
    if updated != 1:
        raise WebAuthError("invalid_link_token", status_code=403)
    return row


async def _exchange_oauth_code(config: ProviderConfig, state_row: AuthOAuthState, code: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": oauth_callback_url(config.provider),
        "code_verifier": state_row.pkce_verifier,
        "client_id": config.client_id,
    }
    headers = {"Accept": "application/json"}
    if config.provider == "telegram":
        if not config.client_secret:
            raise WebAuthError("provider_disabled", status_code=404)
        basic = base64.b64encode(
            f"{config.client_id}:{config.client_secret}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if config.client_secret and config.provider != "telegram":
        data["client_secret"] = config.client_secret
    if config.provider == "yandex":
        # Yandex accepts form body with client credentials; keep it explicit for portability.
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(config.token_url, data=data, headers=headers)
    if response.status_code >= 400:
        logger.warning("OAuth token exchange failed provider=%s status=%s", config.provider, response.status_code)
        raise WebAuthError("token_exchange_failed", status_code=403)
    return response.json()


def _decode_id_token(config: ProviderConfig, id_token: str, nonce: str) -> dict[str, Any]:
    if not config.jwks_url or not config.issuer:
        raise WebAuthError("id_token_not_supported", status_code=403)
    try:
        jwks_client = jwt.PyJWKClient(config.jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token).key
        payload = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256", "ES256", "EdDSA"],
            audience=config.client_id,
            issuer=config.issuer,
            leeway=30,
        )
    except Exception as exc:
        logger.warning("OAuth id_token validation failed provider=%s: %s", config.provider, exc)
        raise WebAuthError("invalid_id_token", status_code=403)
    token_nonce = str(payload.get("nonce") or "")
    if token_nonce != nonce:
        raise WebAuthError("invalid_nonce", status_code=403)
    if not payload.get("sub"):
        raise WebAuthError("missing_subject", status_code=403)
    return payload


async def _decode_id_token_async(config: ProviderConfig, id_token: str, nonce: str) -> dict[str, Any]:
    # PyJWKClient performs synchronous network/cache work; keep it off the event loop.
    return await asyncio.to_thread(_decode_id_token, config, id_token, nonce)


def _extract_numeric_telegram_id(claims: dict[str, Any]) -> int:
    for key in ("id", "telegram_id"):
        raw = claims.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value.isdigit() and int(value) > 0:
            return int(value)
    raise WebAuthError("missing_telegram_id", status_code=403)


async def _fetch_userinfo(config: ProviderConfig, access_token: str) -> dict[str, Any]:
    if not config.userinfo_url:
        return {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            config.userinfo_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        logger.warning("OAuth userinfo failed provider=%s status=%s", config.provider, response.status_code)
        raise WebAuthError("userinfo_failed", status_code=403)
    return response.json()


async def resolve_provider_profile(config: ProviderConfig, state_row: AuthOAuthState, code: str) -> ProviderProfile:
    token_payload = await _exchange_oauth_code(config, state_row, code)
    id_token = token_payload.get("id_token")
    access_token = token_payload.get("access_token")

    id_claims: dict[str, Any] = {}
    if id_token:
        id_claims = await _decode_id_token_async(config, str(id_token), state_row.nonce)

    userinfo: dict[str, Any] = {}
    if access_token and config.userinfo_url:
        userinfo = await _fetch_userinfo(config, str(access_token))

    if config.provider == "yandex":
        subject = str(userinfo.get("id") or userinfo.get("client_id") or "").strip()
        email = normalize_email(
            userinfo.get("default_email")
            or ((userinfo.get("emails") or [None])[0] if isinstance(userinfo.get("emails"), list) else None)
        )
        display_name = userinfo.get("real_name") or userinfo.get("display_name") or userinfo.get("login")
        avatar_id = userinfo.get("default_avatar_id")
        avatar_url = f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200" if avatar_id else None
        if not subject:
            raise WebAuthError("missing_subject", status_code=403)
        return ProviderProfile(
            provider="yandex",
            subject=subject,
            email=email,
            email_verified=bool(email),
            display_name=str(display_name).strip() if display_name else None,
            avatar_url=avatar_url,
        )

    if config.provider == "telegram":
        telegram_id = _extract_numeric_telegram_id(id_claims)
        first_name = id_claims.get("first_name")
        last_name = id_claims.get("last_name")
        username = id_claims.get("username")
        display_name = (
            " ".join(str(part).strip() for part in (first_name, last_name) if str(part or "").strip())
            or str(id_claims.get("name") or username or "").strip()
            or None
        )
        avatar_url = id_claims.get("picture") or id_claims.get("photo_url")
        return ProviderProfile(
            provider="telegram",
            subject=str(telegram_id),
            email=None,
            email_verified=False,
            display_name=display_name,
            avatar_url=str(avatar_url).strip() if avatar_url else None,
        )

    subject = str(id_claims.get("sub") or userinfo.get("sub") or "").strip()
    if not subject:
        raise WebAuthError("missing_subject", status_code=403)
    email = normalize_email(id_claims.get("email") or userinfo.get("email"))
    email_verified = bool(id_claims.get("email_verified") or userinfo.get("email_verified"))
    display_name = id_claims.get("name") or userinfo.get("name") or email
    avatar_url = userinfo.get("picture") or id_claims.get("picture")
    return ProviderProfile(
        provider=config.provider,
        subject=subject,
        email=email,
        email_verified=email_verified,
        display_name=str(display_name).strip() if display_name else None,
        avatar_url=str(avatar_url).strip() if avatar_url else None,
    )


async def allocate_web_user_id() -> int:
    for _ in range(20):
        candidate = WEB_USER_ID_FLOOR + secrets.randbelow(WEB_USER_ID_CEILING - WEB_USER_ID_FLOOR)
        if await Users.get_or_none(id=candidate) is None:
            return candidate
    raise RuntimeError("Could not allocate unique web user id")


async def create_web_user(*, display_name: str | None, email: str | None) -> Users:
    user_id = await allocate_web_user_id()
    full_name = (display_name or email or "Vectra Web User").strip()[:1000]
    user = await Users.create(
        id=user_id,
        username=None,
        full_name=full_name or "Vectra Web User",
        email=email,
        familyurl=get_family_url(user_id),
    )
    return user


async def ensure_identity_for_user(user: Users, profile: ProviderProfile) -> AuthIdentity:
    try:
        identity, _ = await AuthIdentity.get_or_create(
            provider=profile.provider,
            provider_subject=profile.subject,
            defaults={
                "user": user,
                "email": profile.email,
                "email_verified": profile.email_verified,
                "display_name": profile.display_name,
                "avatar_url": profile.avatar_url,
                "last_login_at": now_utc(),
            },
        )
    except IntegrityError:
        identity = await AuthIdentity.get(provider=profile.provider, provider_subject=profile.subject)
    update_fields: list[str] = []
    if int(identity.user_id) != int(user.id):
        raise WebAuthError("identity_already_linked", status_code=409)
    for attr, value in {
        "email": profile.email,
        "email_verified": profile.email_verified,
        "display_name": profile.display_name,
        "avatar_url": profile.avatar_url,
        "last_login_at": now_utc(),
    }.items():
        if getattr(identity, attr) != value:
            setattr(identity, attr, value)
            update_fields.append(attr)
    if update_fields:
        await identity.save(update_fields=update_fields)
    return identity


async def get_or_create_user_for_oauth_profile(profile: ProviderProfile) -> tuple[Users, bool]:
    identity = await AuthIdentity.get_or_none(provider=profile.provider, provider_subject=profile.subject)
    if identity:
        user = await Users.get(id=identity.user_id)
        identity.last_login_at = now_utc()
        await identity.save(update_fields=["last_login_at"])
        return user, False
    user = await create_web_user(display_name=profile.display_name, email=profile.email)
    await ensure_identity_for_user(user, profile)
    return user, True


def _safe_html(value: Any) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)


async def notify_web_oauth_registration(
    user: Users, profile: ProviderProfile, provider: OAuthProvider
) -> None:
    try:
        from bloobcat.bot.notifications.admin import send_admin_message

        label = {"google": "Google", "yandex": "Yandex", "telegram": "Telegram", "apple": "Apple"}.get(
            provider,
            provider,
        )
        email_line = (
            f"\nEmail: <code>{_safe_html(profile.email)}</code>"
            if profile.email
            else "\nEmail: —"
        )
        text = (
            "🆕 Новая web-регистрация!\n\n"
            f"👤 Пользователь: {_safe_html(profile.display_name or user.full_name or profile.email)}\n"
            f"🆔 ID пользователя: <code>{int(user.id)}</code>\n"
            f"🔐 Провайдер: {_safe_html(label)}"
            f"{email_line}\n\n"
            "#новый_пользователь #web_auth"
        )
        delivered = await send_admin_message(text=text)
        if not delivered:
            logger.warning(
                "Web OAuth registration log was not delivered: user=%s provider=%s",
                user.id,
                provider,
            )
    except Exception as exc:
        logger.warning(
            "Web OAuth registration log failed: user=%s provider=%s error=%s",
            getattr(user, "id", None),
            provider,
            exc,
        )


def issue_access_token_for_user(user: Users) -> tuple[str, int]:
    return create_access_token(int(user.id), token_version=int(user.auth_token_version or 0))


async def create_login_ticket(user: Users) -> str:
    ticket = generate_public_token(32)
    await AuthLoginTicket.create(
        ticket_hash=hash_secret(ticket),
        user=user,
        expires_at=now_utc() + timedelta(seconds=LOGIN_TICKET_TTL_SECONDS),
    )
    return ticket


async def exchange_login_ticket(ticket: str) -> tuple[Users, str, int]:
    row = await _consume_login_ticket(ticket)
    user = await Users.get(id=row.user_id)
    token, ttl = issue_access_token_for_user(user)
    return user, token, ttl


async def handle_oauth_callback(provider: OAuthProvider, code: str, state: str, request: Request | None = None) -> tuple[str, str | None]:
    config = get_provider_config(provider)
    if not provider_is_enabled(provider) or config is None:
        raise WebAuthError("provider_disabled", status_code=404)
    state_row = await _consume_oauth_state(provider, state)
    profile = await resolve_provider_profile(config, state_row, code)

    if state_row.mode == "link":
        if not state_row.linking_user_id:
            raise WebAuthError("link_requires_auth", status_code=401)
        user = await Users.get_or_none(id=state_row.linking_user_id)
        if not user:
            raise WebAuthError("user_not_found", status_code=403)
        if provider == "telegram":
            user, merged = await merge_source_user_into_telegram_user(
                user,
                _telegram_user_from_profile(profile),
            )
            audit_reason = "merged" if merged else "linked"
        else:
            await ensure_identity_for_user(user, profile)
            audit_reason = "linked"
        await audit_auth_event(
            action="oauth_link",
            result="success",
            provider=provider,
            user_id=int(user.id),
            reason=audit_reason,
            request=request,
        )
    else:
        if provider == "telegram":
            user, created = await get_or_create_telegram_user_for_profile(profile)
        else:
            user, created = await get_or_create_user_for_oauth_profile(profile)
            if created:
                await notify_web_oauth_registration(user, profile, provider)
        await audit_auth_event(
            action="oauth_login",
            result="success",
            provider=provider,
            user_id=int(user.id),
            reason="created" if created else "existing",
            request=request,
        )
    ticket = await create_login_ticket(user)
    return ticket, state_row.return_to


async def ensure_telegram_identity(user: Users, telegram_user: Any) -> None:
    try:
        subject = str(int(telegram_user.id))
        display_name = " ".join(
            part for part in [getattr(telegram_user, "first_name", None), getattr(telegram_user, "last_name", None)] if part
        ).strip() or getattr(user, "full_name", None)
        await ensure_identity_for_user(
            user,
            ProviderProfile(
                provider="telegram",  # type: ignore[arg-type]
                subject=subject,
                display_name=display_name,
            ),
        )
    except Exception as exc:
        logger.debug("telegram identity ensure skipped user=%s: %s", getattr(user, "id", None), exc)


def _telegram_user_from_profile(profile: ProviderProfile) -> Any:
    telegram_id = int(profile.subject)
    display_name = (profile.display_name or "Telegram User").strip()
    return types.SimpleNamespace(
        id=telegram_id,
        first_name=display_name,
        last_name=None,
        username=None,
    )


async def _ensure_telegram_user_for_identity(telegram_user: Any) -> Users:
    target_user = await Users.get_or_none(id=int(telegram_user.id))
    if target_user is not None:
        return target_user

    from bloobcat.db.users import Users as UsersModel

    created_user, _ = await UsersModel.get_user(telegram_user=telegram_user, ensure_remnawave=False)
    if created_user is None:
        raise WebAuthError("telegram_user_create_failed", status_code=503)
    return created_user


async def merge_source_user_into_telegram_user(source_user: Users, telegram_user: Any) -> tuple[Users, bool]:
    telegram_id = int(telegram_user.id)
    target_user = await Users.get_or_none(id=telegram_id)

    if target_user is not None and int(target_user.id) == int(source_user.id):
        await ensure_telegram_identity(target_user, telegram_user)
        return target_user, False

    if not is_web_user_id(source_user.id):
        raise WebAuthError("merge_requires_support", status_code=409)

    if await user_has_material_data(int(source_user.id)):
        raise WebAuthError("merge_requires_support", status_code=409)

    target_user = target_user or await _ensure_telegram_user_for_identity(telegram_user)
    if int(target_user.id) == int(source_user.id):
        await ensure_telegram_identity(target_user, telegram_user)
        return target_user, False

    async with in_transaction() as conn:
        source_locked = await Users.select_for_update().using_db(conn).get(id=source_user.id)
        await Users.select_for_update().using_db(conn).get(id=target_user.id)
        if await user_has_material_data(int(source_locked.id), conn=conn):
            raise WebAuthError("merge_requires_support", status_code=409)
        await (
            AuthIdentity.filter(user_id=source_user.id)
            .exclude(provider="telegram")
            .using_db(conn)
            .update(user_id=target_user.id)
        )
        await (
            AuthPasswordCredential.filter(user_id=source_user.id)
            .using_db(conn)
            .update(user_id=target_user.id)
        )
        await ensure_telegram_identity(target_user, telegram_user)
        source_locked.auth_token_version = int(source_locked.auth_token_version or 0) + 1
        await source_locked.save(update_fields=["auth_token_version"], using_db=conn)
    return target_user, True


async def get_or_create_telegram_user_for_profile(profile: ProviderProfile) -> tuple[Users, bool]:
    telegram_user = _telegram_user_from_profile(profile)
    existing_user = await Users.get_or_none(id=int(telegram_user.id))
    user = existing_user or await _ensure_telegram_user_for_identity(telegram_user)
    await ensure_telegram_identity(user, telegram_user)
    return user, existing_user is None


async def create_telegram_link_request(user: Users) -> str:
    token = generate_public_token(24)
    await AuthLinkRequest.create(
        token_hash=hash_secret(token),
        source_user=user,
        target_provider="telegram",
        expires_at=now_utc() + timedelta(seconds=LINK_TOKEN_TTL_SECONDS),
    )
    return token


def _using_db(query: Any, conn: Any | None) -> Any:
    if conn is None:
        return query
    return query.using_db(conn)


async def _exists_with_db(query: Any, conn: Any | None = None) -> bool:
    return bool(await _using_db(query, conn).exists())


async def user_has_material_data(user_id: int, conn: Any | None = None) -> bool:
    user = (
        await Users.get_or_none(id=user_id)
        if conn is None
        else await _using_db(Users.filter(id=user_id), conn).first()
    )
    if user and (
        bool(getattr(user, "remnawave_uuid", None))
        or bool(getattr(user, "is_registered", False))
        or bool(getattr(user, "is_subscribed", False))
        or bool(getattr(user, "active_tariff_id", None))
        or int(getattr(user, "balance", 0) or 0) != 0
        or int(getattr(user, "referral_bonus_days_total", 0) or 0) != 0
        or bool(getattr(user, "referral_first_payment_rewarded", False))
        or bool(getattr(user, "utm", None))
        or bool(getattr(user, "is_partner", False))
        or int(getattr(user, "custom_referral_percent", 0) or 0) != 0
        or int(getattr(user, "referrals", 0) or 0) != 0
        or bool(getattr(user, "referred_by", None))
        or bool(getattr(user, "is_trial", False))
        or bool(getattr(user, "used_trial", False))
        or bool(getattr(user, "expired_at", None))
        or bool(getattr(user, "renew_id", None))
        or bool(getattr(user, "connected_at", None))
        or bool(getattr(user, "is_admin", False))
        or bool(getattr(user, "hwid_limit", None) is not None)
        or bool(getattr(user, "lte_gb_total", None) is not None)
        or bool(getattr(user, "last_hwid_reset", None))
        or bool(getattr(user, "is_blocked", False))
        or bool(getattr(user, "blocked_at", None))
        or bool(getattr(user, "last_failed_message_at", None))
        or int(getattr(user, "failed_message_count", 0) or 0) != 0
        or int(getattr(user, "prize_wheel_attempts", 0) or 0) != 0
    ):
        return True
    if await _exists_with_db(ActiveTariffs.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(ProcessedPayments.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(
        FamilyMembers.filter(owner_id=user_id), conn
    ) or await _exists_with_db(FamilyMembers.filter(member_id=user_id), conn):
        return True
    if await _exists_with_db(FamilyInvites.filter(owner_id=user_id), conn):
        return True
    if await _exists_with_db(FamilyDevices.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(PartnerEarnings.filter(partner_id=user_id), conn):
        return True
    if await _exists_with_db(PartnerWithdrawals.filter(owner_id=user_id), conn):
        return True
    if await _exists_with_db(PartnerQr.filter(owner_id=user_id), conn):
        return True
    if await _exists_with_db(ReferralRewards.filter(referrer_user_id=user_id), conn):
        return True
    if await _exists_with_db(ReferralRewards.filter(referred_user_id=user_id), conn):
        return True
    if await _exists_with_db(ReferralLevelRewards.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(PromoUsage.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(PersonalDiscount.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(PrizeWheelHistory.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(SubscriptionFreezes.filter(user_id=user_id), conn):
        return True
    if await _exists_with_db(RemnaWaveRetryJobs.filter(user_id=user_id), conn):
        return True
    return False


async def complete_telegram_link(source_user: Users, link_token: str, telegram_user: Any) -> tuple[Users, bool]:
    await _consume_link_request(source_user, link_token)
    return await merge_source_user_into_telegram_user(source_user, telegram_user)


async def grant_trial_if_eligible(user: Users) -> bool:
    """Grant the local trial entitlement without depending on RemnaWave availability."""
    if user.expired_at is not None or user.used_trial:
        return False

    trial_until = date.today() + timedelta(days=app_settings.trial_days)
    user.is_trial = True
    user.used_trial = True
    user.expired_at = trial_until
    await user.save(update_fields=["is_trial", "used_trial", "expired_at"])
    logger.info("Granted local trial for user=%s until %s", user.id, trial_until)

    try:
        from bloobcat.bot.notifications.trial.granted import notify_trial_granted

        await notify_trial_granted(user)
    except Exception as exc:
        logger.warning(
            "Trial grant notification failed for user=%s: %s",
            getattr(user, "id", None),
            exc,
        )
    return True


async def complete_registration_for_user(user: Users, *, defer_remnawave: bool = False) -> tuple[str, int]:
    if is_web_user_id(user.id):
        await grant_trial_if_eligible(user)
        if not user.remnawave_uuid:
            Users._schedule_remnawave_ensure(int(user.id))
        token, ttl = issue_access_token_for_user(user)
        return token, ttl

    if not user.remnawave_uuid:
        if defer_remnawave:
            await grant_trial_if_eligible(user)
            Users._schedule_remnawave_ensure(int(user.id))
            token, ttl = issue_access_token_for_user(user)
            return token, ttl
        ensured = await user._ensure_remnawave_user()
        if not ensured and not user.remnawave_uuid:
            raise WebAuthError("registration_sync_pending", status_code=503)
    token, ttl = issue_access_token_for_user(user)
    return token, ttl


def validate_password_strength(password: str) -> None:
    if len(password) < 8 or len(password) > 128:
        raise WebAuthError("weak_password", "Password must be 8-128 characters", status_code=400)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


async def send_auth_email(email: str, subject: str, body: str) -> bool:
    if resend_settings.api_key and resend_settings.from_email:
        return await send_auth_email_via_resend(email, subject, body)
    if smtp_settings.host and smtp_settings.from_email:
        return await send_auth_email_via_smtp(email, subject, body)

    logger.warning("Email delivery is not configured; auth email was not sent to hash=%s", hash_secret(email)[:12])
    return False


def _format_sender(from_email: str, from_name: str | None) -> str:
    if "<" in from_email and ">" in from_email:
        return from_email
    safe_name = (from_name or "").strip()
    return f"{safe_name} <{from_email}>" if safe_name else from_email


def _email_body_to_html(body: str) -> str:
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    if not paragraphs:
        return ""
    return "\n".join(
        f"<p>{escape(part).replace(chr(10), '<br>')}</p>"
        for part in paragraphs
    )


async def send_auth_email_via_resend(email: str, subject: str, body: str) -> bool:
    api_key = resend_settings.api_key.get_secret_value() if resend_settings.api_key else ""
    from_email = (resend_settings.from_email or "").strip()
    if not api_key or not from_email:
        logger.warning("Resend is not configured; auth email was not sent to hash=%s", hash_secret(email)[:12])
        return False

    url = f"{resend_settings.base_url.rstrip('/')}/emails"
    payload = {
        "from": _format_sender(from_email, resend_settings.from_name),
        "to": [email],
        "subject": subject,
        "text": body,
        "html": _email_body_to_html(body),
    }
    try:
        async with httpx.AsyncClient(timeout=resend_settings.timeout_seconds) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "vectra-connect-backend/1.0",
                },
                json=payload,
            )
        if 200 <= response.status_code < 300:
            return True
        logger.warning(
            "Resend auth email send failed hash=%s status=%s",
            hash_secret(email)[:12],
            response.status_code,
        )
        return False
    except Exception as exc:
        logger.warning("Resend auth email send failed hash=%s: %s", hash_secret(email)[:12], exc)
        return False


def _send_auth_email_via_smtp_sync(email: str, subject: str, body: str) -> bool:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _format_sender(smtp_settings.from_email or "", smtp_settings.from_name)
    message["To"] = email
    message.set_content(body)
    try:
        if smtp_settings.use_tls:
            with smtplib.SMTP_SSL(smtp_settings.host, smtp_settings.port, timeout=10) as smtp:
                if smtp_settings.username and smtp_settings.password:
                    smtp.login(smtp_settings.username, smtp_settings.password.get_secret_value())
                smtp.send_message(message)
        else:
            with smtplib.SMTP(smtp_settings.host, smtp_settings.port, timeout=10) as smtp:
                smtp.starttls()
                if smtp_settings.username and smtp_settings.password:
                    smtp.login(smtp_settings.username, smtp_settings.password.get_secret_value())
                smtp.send_message(message)
        return True
    except Exception as exc:
        logger.warning("Auth email send failed hash=%s: %s", hash_secret(email)[:12], exc)
        return False


async def send_auth_email_via_smtp(email: str, subject: str, body: str) -> bool:
    return await asyncio.to_thread(_send_auth_email_via_smtp_sync, email, subject, body)



def is_password_email_delivery_enabled() -> bool:
    return bool(
        (resend_settings.api_key and resend_settings.from_email)
        or (smtp_settings.host and smtp_settings.from_email)
    )


def _password_email_delivery_enabled_or_raise() -> None:
    if not is_password_email_delivery_enabled():
        raise WebAuthError("password_email_delivery_disabled", status_code=503)


def _verification_url(token: str) -> str:
    return f"{(oauth_settings.frontend_app_url or '').rstrip('/')}/auth/verify-email?token={token}"


def _reset_url(token: str) -> str:
    return f"{(oauth_settings.frontend_app_url or '').rstrip('/')}/auth/reset-password?token={token}"


async def _send_verification_email(email: str, token: str) -> bool:
    verify_url = _verification_url(token)
    return await send_auth_email(
        email,
        "Подтвердите вход в Vectra Connect",
        f"Чтобы завершить регистрацию, откройте ссылку:\n\n{verify_url}\n\nЕсли это были не вы, просто игнорируйте письмо.",
    )


async def _send_existing_account_email(email: str) -> bool:
    login_url = (oauth_settings.frontend_app_url or '').rstrip('/') or '/'
    return await send_auth_email(
        email,
        "Вход в Vectra Connect",
        f"Для этого email уже есть аккаунт Vectra Connect. Если это были вы, войдите по email на странице:\n\n{login_url}\n\nЕсли вы не запрашивали регистрацию, просто игнорируйте письмо.",
    )


def _password_auth_enabled_or_raise() -> None:
    if not (web_auth_settings.web_auth_enabled and web_auth_settings.password_auth_enabled):
        raise WebAuthError("password_auth_disabled", status_code=404)


async def register_password_user(email: str, password: str, request: Request | None = None) -> dict[str, Any]:
    _password_auth_enabled_or_raise()
    normalized = normalize_email(email)
    if not normalized:
        raise WebAuthError("invalid_email", status_code=400)
    validate_password_strength(password)
    await enforce_password_email_rate_limit(
        normalized,
        action="password_register",
        request=request,
    )
    _password_email_delivery_enabled_or_raise()

    existing = await AuthPasswordCredential.get_or_none(email_normalized=normalized)
    if existing:
        if existing.email_verified:
            email_sent = await _send_existing_account_email(normalized)
            await audit_auth_event(
                action="password_register",
                result="ignored",
                provider="password",
                reason="existing_verified",
                request=request,
            )
            return {**GENERIC_PASSWORD_RESPONSE, "emailSent": email_sent}

        token = generate_public_token(32)
        existing.verification_token_hash = hash_secret(token)
        existing.verification_expires_at = now_utc() + timedelta(seconds=EMAIL_TOKEN_TTL_SECONDS)
        await existing.save(update_fields=["verification_token_hash", "verification_expires_at", "updated_at"])
        email_sent = await _send_verification_email(normalized, token)
        await audit_auth_event(
            action="password_register",
            result="resent",
            provider="password",
            user_id=int(existing.user_id),
            reason="existing_unverified",
            request=request,
        )
        return {"ok": True, "emailVerificationRequired": True, "emailSent": email_sent}

    user = await create_web_user(display_name=normalized.split("@", 1)[0], email=normalized)
    token = generate_public_token(32)
    token_hash = hash_secret(token)
    await AuthPasswordCredential.create(
        user=user,
        email_normalized=normalized,
        password_hash=await hash_password_async(password),
        email_verified=False,
        verification_token_hash=token_hash,
        verification_expires_at=now_utc() + timedelta(seconds=EMAIL_TOKEN_TTL_SECONDS),
    )
    await ensure_identity_for_user(
        user,
        ProviderProfile(
            provider="password",  # type: ignore[arg-type]
            subject=normalized,
            email=normalized,
            email_verified=False,
            display_name=user.full_name,
        ),
    )
    email_sent = await _send_verification_email(normalized, token)
    await audit_auth_event(action="password_register", result="success", provider="password", user_id=int(user.id), request=request)
    return {"ok": True, "emailVerificationRequired": True, "emailSent": email_sent}


async def verify_password_email(token: str, request: Request | None = None) -> tuple[Users, str, int]:
    _password_auth_enabled_or_raise()
    credential = await AuthPasswordCredential.get_or_none(verification_token_hash=hash_secret(token))
    if not credential or not credential.verification_expires_at or credential.verification_expires_at < now_utc():
        raise WebAuthError("invalid_verification_token", status_code=403)
    credential.email_verified = True
    credential.verification_token_hash = None
    credential.verification_expires_at = None
    await credential.save(update_fields=["email_verified", "verification_token_hash", "verification_expires_at", "updated_at"])
    await AuthIdentity.filter(user_id=credential.user_id, provider="password").update(email_verified=True)
    user = await Users.get(id=credential.user_id)
    token_value, ttl = issue_access_token_for_user(user)
    await audit_auth_event(action="password_verify_email", result="success", provider="password", user_id=int(user.id), request=request)
    return user, token_value, ttl


async def login_with_password(email: str, password: str, request: Request | None = None) -> tuple[Users, str, int]:
    _password_auth_enabled_or_raise()
    normalized = normalize_email(email)
    if not normalized:
        raise WebAuthError("invalid_credentials", status_code=403)
    await enforce_password_email_rate_limit(
        normalized,
        action="password_login",
        request=request,
    )
    credential = await AuthPasswordCredential.get_or_none(email_normalized=normalized)
    password_ok = False
    if credential and credential.email_verified:
        password_ok = await verify_password_async(password, credential.password_hash)
    if not credential or not credential.email_verified or not password_ok:
        await audit_auth_event(action="password_login", result="failed", provider="password", reason="invalid_credentials", request=request)
        raise WebAuthError("invalid_credentials", status_code=403)
    user = await Users.get(id=credential.user_id)
    token_value, ttl = issue_access_token_for_user(user)
    await audit_auth_event(action="password_login", result="success", provider="password", user_id=int(user.id), request=request)
    return user, token_value, ttl


async def request_password_reset(email: str, request: Request | None = None) -> dict[str, bool]:
    _password_auth_enabled_or_raise()
    normalized = normalize_email(email)
    if not normalized:
        return {"ok": True}
    await enforce_password_email_rate_limit(
        normalized,
        action="password_reset_request",
        request=request,
    )
    _password_email_delivery_enabled_or_raise()
    credential = await AuthPasswordCredential.get_or_none(email_normalized=normalized)
    if credential:
        token = generate_public_token(32)
        credential.reset_token_hash = hash_secret(token)
        credential.reset_expires_at = now_utc() + timedelta(seconds=RESET_TOKEN_TTL_SECONDS)
        await credential.save(update_fields=["reset_token_hash", "reset_expires_at", "updated_at"])
        reset_url = _reset_url(token)
        await send_auth_email(
            normalized,
            "Сброс пароля Vectra Connect",
            f"Чтобы сбросить пароль, откройте ссылку:\n\n{reset_url}\n\nЕсли это были не вы, просто игнорируйте письмо.",
        )
    await audit_auth_event(action="password_reset_request", result="accepted", provider="password", request=request)
    return {"ok": True}


async def confirm_password_reset(token: str, password: str, request: Request | None = None) -> dict[str, bool]:
    _password_auth_enabled_or_raise()
    validate_password_strength(password)
    credential = await AuthPasswordCredential.get_or_none(reset_token_hash=hash_secret(token))
    if not credential or not credential.reset_expires_at or credential.reset_expires_at < now_utc():
        raise WebAuthError("invalid_reset_token", status_code=403)
    credential.password_hash = await hash_password_async(password)
    credential.email_verified = True
    credential.reset_token_hash = None
    credential.reset_expires_at = None
    await credential.save(
        update_fields=["password_hash", "email_verified", "reset_token_hash", "reset_expires_at", "updated_at"]
    )
    user = await Users.get(id=credential.user_id)
    user.auth_token_version = int(user.auth_token_version or 0) + 1
    await user.save(update_fields=["auth_token_version"])
    await AuthIdentity.filter(user_id=credential.user_id, provider="password").update(email_verified=True)
    await audit_auth_event(action="password_reset_confirm", result="success", provider="password", user_id=int(credential.user_id), request=request)
    return {"ok": True}


async def list_user_identities(user: Users) -> list[dict[str, Any]]:
    identities = await AuthIdentity.filter(user_id=user.id).order_by("provider", "linked_at")
    return [
        {
            "provider": identity.provider,
            "email": identity.email,
            "emailVerified": identity.email_verified,
            "displayName": identity.display_name,
            "linkedAt": identity.linked_at.isoformat() if identity.linked_at else None,
            "lastLoginAt": identity.last_login_at.isoformat() if identity.last_login_at else None,
        }
        for identity in identities
    ]


async def unlink_identity(user: Users, provider: str) -> dict[str, bool]:
    provider = provider.strip().lower()
    if provider == "telegram" and not is_web_user_id(user.id):
        raise WebAuthError("cannot_unlink_primary_telegram", status_code=400)

    identity_exists = await AuthIdentity.filter(user_id=user.id, provider=provider).exists()
    password_exists = False
    if provider == "password":
        password_exists = await AuthPasswordCredential.filter(user_id=user.id).exists()
    if not identity_exists and not password_exists:
        raise WebAuthError("identity_not_found", status_code=404)

    non_password_identity_count = await AuthIdentity.filter(user_id=user.id).exclude(provider="password").count()
    verified_password_exists = await AuthPasswordCredential.filter(
        user_id=user.id,
        email_verified=True,
    ).exists()
    working_methods = non_password_identity_count + (1 if verified_password_exists else 0)
    removes_working_method = (
        (provider == "password" and verified_password_exists)
        or (provider != "password" and identity_exists)
    )
    if removes_working_method and working_methods <= 1:
        raise WebAuthError("last_identity", status_code=400)

    deleted = await AuthIdentity.filter(user_id=user.id, provider=provider).delete()
    if provider == "password":
        await AuthPasswordCredential.filter(user_id=user.id).delete()
    if not deleted:
        # A legacy credential without a matching identity is still unlinked by the
        # password credential deletion above.
        if provider != "password":
            raise WebAuthError("identity_not_found", status_code=404)
    return {"ok": True}


def raise_http_from_web_auth(error: WebAuthError) -> None:
    detail = {"code": error.code, "message": public_web_auth_error_message(error)}
    raise HTTPException(status_code=error.status_code, detail=detail)


def web_auth_exception_response(error: WebAuthError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": public_web_auth_error_message(error)},
    )


def oauth_provider_or_404(raw_provider: str) -> OAuthProvider:
    provider = raw_provider.strip().lower()
    if provider not in SUPPORTED_OAUTH_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider  # type: ignore[return-value]
