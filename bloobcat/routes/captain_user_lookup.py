from __future__ import annotations

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from bloobcat.logger import get_logger
from bloobcat.services.captain_user_lookup import (
    CaptainUserProfile,
    ErrorResponse,
    user_repository,
)
from bloobcat.settings import captain_lookup_settings

router = APIRouter(
    prefix="/api/users",
    tags=["Captain User Lookup"],
)

logger = get_logger("captain_user_lookup")


def _error_response(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


def _log_lookup(telegram_id: int, status_code: int) -> None:
    logger.bind(telegram_id=telegram_id, status_code=status_code).info(
        "captain_user_lookup"
    )


def _is_authorized(authorization_header: str | None) -> bool:
    if not authorization_header:
        return False
    if not authorization_header.lower().startswith("bearer "):
        return False
    provided = authorization_header.split(" ", 1)[1].strip()
    return bool(
        provided and provided == captain_lookup_settings.api_key.get_secret_value()
    )


def _is_domain_allowed(request: Request) -> bool:
    allowed = captain_lookup_settings.allowlist_domains
    if not allowed:
        return True
    host = request.url.hostname or request.headers.get("host", "")
    host = host.split(":")[0].lower()
    return host in allowed


@router.get(
    "/{telegram_id}",
    response_model=CaptainUserProfile,
    responses={
        400: {"model": ErrorResponse, "description": "Некорректный telegram_id"},
        401: {"model": ErrorResponse, "description": "Не верный или отсутствует API ключ"},
        403: {"model": ErrorResponse, "description": "Домен не в allowlist"},
        404: {"model": ErrorResponse, "description": "Пользователь не найден"},
    },
)
async def lookup_user(
    telegram_id: int,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Поиск пользователя Captain User Lookup."""

    if not _is_domain_allowed(request):
        _log_lookup(telegram_id, status.HTTP_403_FORBIDDEN)
        return _error_response(status.HTTP_403_FORBIDDEN, "forbidden")

    if not _is_authorized(authorization):
        _log_lookup(telegram_id, status.HTTP_401_UNAUTHORIZED)
        return _error_response(status.HTTP_401_UNAUTHORIZED, "unauthorized")

    if telegram_id <= 0:
        _log_lookup(telegram_id, status.HTTP_400_BAD_REQUEST)
        return _error_response(status.HTTP_400_BAD_REQUEST, "invalid_telegram_id")

    user = await user_repository.get_by_telegram_id(telegram_id)

    if not user:
        _log_lookup(telegram_id, status.HTTP_404_NOT_FOUND)
        return _error_response(status.HTTP_404_NOT_FOUND, "not_found")

    _log_lookup(telegram_id, status.HTTP_200_OK)
    return user
