from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from aiogram.utils.web_app import safe_parse_webapp_init_data

from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.funcs.local_dev_auth import resolve_local_dev_telegram_user
from bloobcat.funcs.referral_attribution import resolve_referral_from_start_param
from bloobcat.funcs.start_params import is_registration_exception_start_param
from bloobcat.settings import local_dev_auth_settings, telegram_settings
from bloobcat.logger import get_logger
from bloobcat.services.web_auth import (
    WebAuthError,
    audit_auth_event,
    complete_registration_for_user,
    complete_telegram_link,
    create_oauth_authorization_url,
    create_telegram_link_request,
    exchange_login_ticket,
    frontend_callback_url,
    get_enabled_oauth_providers,
    handle_oauth_callback,
    issue_access_token_for_user,
    is_password_email_delivery_enabled,
    list_user_identities,
    login_with_password,
    oauth_provider_or_404,
    raise_http_from_web_auth,
    register_password_user,
    request_password_reset,
    confirm_password_reset,
    unlink_identity,
    verify_password_email,
    ensure_telegram_identity,
)

logger = get_logger("routes.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    initData: str
    # Optional: forwarded start payload (e.g. from bot /start -> Mini App URL ?start=...).
    startParam: str | None = None
    # Explicit user intent to create/ensure a DB user row.
    # Backward-compatible default keeps old clients working.
    registerIntent: bool = False


class TelegramAuthResponse(BaseModel):
    accessToken: str
    expiresIn: int
    was_just_created: bool = False
    requires_registration: bool = False


class AuthTokenResponse(BaseModel):
    accessToken: str
    expiresIn: int
    was_just_created: bool = False


class ProvidersResponse(BaseModel):
    webAuthEnabled: bool
    passwordAuthEnabled: bool
    passwordEmailDeliveryEnabled: bool = False
    passwordRegisterEnabled: bool = False
    passwordResetEnabled: bool = False
    oauthProviders: list[dict[str, str]]


class OAuthStartResponse(BaseModel):
    authorizationUrl: str


class TicketExchangeRequest(BaseModel):
    ticket: str


class PasswordRegisterRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordLoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordVerifyEmailRequest(BaseModel):
    token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str


class TelegramLinkStartResponse(BaseModel):
    linkToken: str
    telegramStartParam: str
    expiresIn: int


class TelegramLinkCompleteRequest(BaseModel):
    linkToken: str
    initData: str


class TelegramLinkCompleteResponse(AuthTokenResponse):
    merged: bool = False


class IdentityListResponse(BaseModel):
    identities: list[dict]


class OkResponse(BaseModel):
    ok: bool = True


def _issue_telegram_auth_response(
    db_user: Users, *, was_just_created: bool = False
) -> TelegramAuthResponse:
    token, ttl_seconds = issue_access_token_for_user(db_user)
    return TelegramAuthResponse(
        accessToken=token,
        expiresIn=ttl_seconds,
        was_just_created=was_just_created,
        requires_registration=False,
    )


async def _parse_callback_payload(request: Request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        values[key] = value
    if request.method.upper() == "POST":
        content_type = (request.headers.get("content-type") or "").lower()
        body = await request.body()
        if body and (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" not in content_type
        ):
            parsed = parse_qs(body.decode("utf-8", errors="ignore"))
            for key, items in parsed.items():
                if items:
                    values[key] = items[0]
    return values


async def _parse_telegram_init_data(init_data: str, request: Request | None):
    try:
        allowed_telegram_ids: set[int] = {
            int(telegram_id)
            for telegram_id in (local_dev_auth_settings.allowed_telegram_ids or [])
        }
        user_data = resolve_local_dev_telegram_user(
            init_data,
            request,
            enabled=local_dev_auth_settings.enabled,
            allowed_telegram_ids=allowed_telegram_ids,
        )
        if user_data is None:
            user_data = safe_parse_webapp_init_data(
                telegram_settings.token.get_secret_value(), init_data
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Invalid Telegram initData: {exc}")
        raise HTTPException(status_code=403, detail="Invalid initData")
    if not user_data or not user_data.user:
        raise HTTPException(status_code=403, detail="Invalid user data")
    return user_data


@router.post("/telegram", response_model=TelegramAuthResponse)
async def auth_telegram(
    payload: TelegramAuthRequest, request: Request = None
) -> TelegramAuthResponse:
    init_data = (payload.initData or "").strip()
    if not init_data:
        raise HTTPException(status_code=400, detail="Missing initData")

    try:
        allowed_telegram_ids: set[int] = {
            int(telegram_id)
            for telegram_id in (local_dev_auth_settings.allowed_telegram_ids or [])
        }
        user_data = resolve_local_dev_telegram_user(
            init_data,
            request,
            enabled=local_dev_auth_settings.enabled,
            allowed_telegram_ids=allowed_telegram_ids,
        )
        if user_data is None:
            user_data = safe_parse_webapp_init_data(
                telegram_settings.token.get_secret_value(), init_data
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Invalid initData for /auth/telegram: {exc}")
        raise HTTPException(status_code=403, detail="Invalid initData")

    if not user_data or not user_data.user:
        raise HTTPException(status_code=403, detail="Invalid user data")

    try:
        # Determine effective start param trust boundary:
        # - Telegram-signed initData.start_param is the only trusted source.
        # - Unsigned payload.startParam is never used for start_param-based decisions.
        signed_start_param = (getattr(user_data, "start_param", None) or "").strip()
        payload_start_param = (payload.startParam or "").strip()
        if (
            signed_start_param
            and payload_start_param
            and signed_start_param != payload_start_param
        ):
            logger.warning(
                f"Security warning /auth/telegram start_param mismatch for telegram_id={user_data.user.id}: "
                f"signed_present={bool(signed_start_param)} payload_present={bool(payload_start_param)} "
                f"signed_len={len(signed_start_param)} payload_len={len(payload_start_param)}. Rejecting request."
            )
            raise HTTPException(status_code=403, detail="Invalid start_param")
        start_param = signed_start_param

        referred_by, utm = await resolve_referral_from_start_param(
            start_param,
            user_id=user_data.user.id,
            track_partner_qr_view=True,
        )

        should_register = bool(
            payload.registerIntent
        ) or is_registration_exception_start_param(start_param)
        if should_register:
            is_explicit_registration = bool(payload.registerIntent)
            ensure_remnawave = not bool(payload.registerIntent)
            db_user, was_just_created = await Users.get_user(
                telegram_user=user_data.user,
                referred_by=referred_by,
                utm=utm,
                ensure_remnawave=ensure_remnawave,
            )
            if not db_user:
                logger.error(
                    "Users.get_user вернул None для telegram_id={} (registerIntent={}, start_param={})",
                    user_data.user.id,
                    bool(payload.registerIntent),
                    start_param or "",
                )
                raise HTTPException(
                    status_code=503, detail="Service temporarily unavailable"
                )

            await ensure_telegram_identity(db_user, user_data.user)
            if is_explicit_registration:
                token, ttl_seconds = await complete_registration_for_user(db_user)
                return TelegramAuthResponse(
                    accessToken=token,
                    expiresIn=ttl_seconds,
                    was_just_created=was_just_created,
                    requires_registration=False,
                )
            return _issue_telegram_auth_response(
                db_user, was_just_created=was_just_created
            )

        # No explicit registration intent and no start_param:
        # do not create a new DB row here. Return current auth state.
        existing_user = await Users.get_or_none(id=user_data.user.id)
        if existing_user:
            await ensure_telegram_identity(existing_user, user_data.user)
            return _issue_telegram_auth_response(existing_user)

        return TelegramAuthResponse(
            accessToken="",
            expiresIn=0,
            was_just_created=False,
            requires_registration=True,
        )
    except WebAuthError as error:
        raise_http_from_web_auth(error)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Критическая ошибка /auth/telegram (telegram_id={}, registerIntent={}): {}",
            getattr(user_data.user, "id", None),
            bool(payload.registerIntent),
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@router.get("/providers", response_model=ProvidersResponse)
async def auth_providers() -> ProvidersResponse:
    from bloobcat.settings import web_auth_settings

    password_auth_enabled = bool(
        web_auth_settings.web_auth_enabled
        and web_auth_settings.password_auth_enabled
    )
    password_email_delivery_enabled = bool(
        password_auth_enabled and is_password_email_delivery_enabled()
    )
    return ProvidersResponse(
        webAuthEnabled=bool(web_auth_settings.web_auth_enabled),
        passwordAuthEnabled=password_auth_enabled,
        passwordEmailDeliveryEnabled=password_email_delivery_enabled,
        passwordRegisterEnabled=password_email_delivery_enabled,
        passwordResetEnabled=password_email_delivery_enabled,
        oauthProviders=get_enabled_oauth_providers(),
    )


@router.get("/oauth/{provider}/start", response_model=OAuthStartResponse)
async def oauth_start(
    provider: str,
    request: Request,
    mode: str = "login",
    returnTo: str | None = None,
) -> OAuthStartResponse:
    oauth_provider = oauth_provider_or_404(provider)
    try:
        url = await create_oauth_authorization_url(
            provider=oauth_provider,
            mode="link" if mode == "link" else "login",
            return_to=returnTo,
            request=request,
        )
        return OAuthStartResponse(authorizationUrl=url)
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.api_route("/oauth/{provider}/callback", methods=["GET", "POST"])
async def oauth_callback(provider: str, request: Request):
    oauth_provider = oauth_provider_or_404(provider)
    payload = await _parse_callback_payload(request)
    error = (payload.get("error") or "").strip()
    if error:
        await audit_auth_event(
            action="oauth_callback",
            result="failed",
            provider=oauth_provider,
            reason=error[:128],
            request=request,
        )
        return RedirectResponse(frontend_callback_url(error=error))
    code = (payload.get("code") or "").strip()
    state = (payload.get("state") or "").strip()
    if not code or not state:
        return RedirectResponse(frontend_callback_url(error="missing_oauth_payload"))
    try:
        ticket, return_to = await handle_oauth_callback(
            oauth_provider, code, state, request=request
        )
        return RedirectResponse(frontend_callback_url(ticket=ticket, return_to=return_to))
    except WebAuthError as exc:
        await audit_auth_event(
            action="oauth_callback",
            result="failed",
            provider=oauth_provider,
            reason=exc.code,
            request=request,
        )
        return RedirectResponse(frontend_callback_url(error=exc.code))


@router.post("/exchange-ticket", response_model=AuthTokenResponse)
async def exchange_ticket(payload: TicketExchangeRequest) -> AuthTokenResponse:
    try:
        _user, token, ttl = await exchange_login_ticket(payload.ticket.strip())
        return AuthTokenResponse(accessToken=token, expiresIn=ttl)
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/password/register")
async def password_register(payload: PasswordRegisterRequest, request: Request):
    try:
        return await register_password_user(
            str(payload.email), payload.password, request=request
        )
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/password/login", response_model=AuthTokenResponse)
async def password_login(payload: PasswordLoginRequest, request: Request) -> AuthTokenResponse:
    try:
        _user, token, ttl = await login_with_password(
            str(payload.email), payload.password, request=request
        )
        return AuthTokenResponse(accessToken=token, expiresIn=ttl)
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/password/verify-email", response_model=AuthTokenResponse)
async def password_verify_email(
    payload: PasswordVerifyEmailRequest, request: Request
) -> AuthTokenResponse:
    try:
        _user, token, ttl = await verify_password_email(
            payload.token.strip(), request=request
        )
        return AuthTokenResponse(accessToken=token, expiresIn=ttl)
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/password/reset/request", response_model=OkResponse)
async def password_reset_request(payload: PasswordResetRequest, request: Request) -> OkResponse:
    try:
        await request_password_reset(str(payload.email), request=request)
        return OkResponse()
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/password/reset/confirm", response_model=OkResponse)
async def password_reset_confirm(
    payload: PasswordResetConfirmRequest, request: Request
) -> OkResponse:
    try:
        await confirm_password_reset(
            payload.token.strip(), payload.password, request=request
        )
        return OkResponse()
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.get("/identities", response_model=IdentityListResponse)
async def identities(user: Users = Depends(validate)) -> IdentityListResponse:
    return IdentityListResponse(identities=await list_user_identities(user))


@router.post("/identities/{provider}/unlink", response_model=OkResponse)
async def unlink_auth_identity(
    provider: str, user: Users = Depends(validate)
) -> OkResponse:
    try:
        await unlink_identity(user, provider)
        return OkResponse()
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/link/telegram/start", response_model=TelegramLinkStartResponse)
async def telegram_link_start(user: Users = Depends(validate)) -> TelegramLinkStartResponse:
    token = await create_telegram_link_request(user)
    return TelegramLinkStartResponse(
        linkToken=token,
        telegramStartParam=f"link_{token}",
        expiresIn=600,
    )


@router.post("/link/telegram/complete", response_model=TelegramLinkCompleteResponse)
async def telegram_link_complete(
    payload: TelegramLinkCompleteRequest,
    request: Request,
    user: Users = Depends(validate),
) -> TelegramLinkCompleteResponse:
    init_data = (payload.initData or "").strip()
    if not init_data:
        raise HTTPException(status_code=400, detail="Missing initData")
    user_data = await _parse_telegram_init_data(init_data, request)
    try:
        target_user, merged = await complete_telegram_link(
            user, payload.linkToken.strip(), user_data.user
        )
        token, ttl = issue_access_token_for_user(target_user)
        return TelegramLinkCompleteResponse(
            accessToken=token,
            expiresIn=ttl,
            was_just_created=False,
            merged=merged,
        )
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)


@router.post("/complete-registration", response_model=AuthTokenResponse)
async def complete_registration(user: Users = Depends(validate)) -> AuthTokenResponse:
    try:
        token, ttl = await complete_registration_for_user(user)
        return AuthTokenResponse(accessToken=token, expiresIn=ttl)
    except WebAuthError as exc:
        raise_http_from_web_auth(exc)
