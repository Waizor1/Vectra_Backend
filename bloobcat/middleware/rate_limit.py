import time
import asyncio
import os
import ipaddress
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
from bloobcat.logger import get_logger

logger = get_logger("rate_limit")

class RateLimiter:
    """Простой rate limiter для FastAPI эндпоинтов"""
    
    def __init__(self, requests_per_minute: int = 5, window_seconds: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.requests: Dict[str, list] = {}  # user_id -> list of timestamps
        self._lock = asyncio.Lock()
    
    async def is_allowed(self, user_id: str) -> Tuple[bool, Optional[int]]:
        """
        Проверяет, разрешен ли запрос для пользователя
        
        Returns:
            Tuple[bool, Optional[int]]: (разрешено, время до следующего разрешения в секундах)
        """
        async with self._lock:
            now = time.time()
            
            # Очищаем старые записи
            if user_id in self.requests:
                filtered = [
                    timestamp for timestamp in self.requests[user_id]
                    if now - timestamp < self.window_seconds
                ]
                if filtered:
                    self.requests[user_id] = filtered
                else:
                    self.requests.pop(user_id, None)
            
            user_requests = self.requests.setdefault(user_id, [])
            
            # Проверяем лимит
            if len(user_requests) >= self.requests_per_minute:
                # Вычисляем время до следующего разрешения
                oldest_request = min(user_requests)
                next_allowed = oldest_request + self.window_seconds - now
                return False, max(0, int(next_allowed))
            
            # Добавляем текущий запрос
            user_requests.append(now)
            return True, None

# Создаем экземпляры rate limiter для разных эндпоинтов
reset_devices_limiter = RateLimiter(requests_per_minute=2, window_seconds=60)  # 2 запроса в минуту
family_revoke_limiter = RateLimiter(requests_per_minute=1, window_seconds=300)  # 1 запрос в 5 минут
promo_validate_limiter = RateLimiter(requests_per_minute=5, window_seconds=60)  # 5 запросов в минуту
promo_redeem_limiter = RateLimiter(requests_per_minute=2, window_seconds=60)  # 2 запроса в минуту
auth_ip_limiter = RateLimiter(requests_per_minute=120, window_seconds=60)  # Anti-storm для /auth/telegram
user_ip_limiter = RateLimiter(requests_per_minute=240, window_seconds=60)  # Hot endpoint /user
devices_ip_limiter = RateLimiter(requests_per_minute=240, window_seconds=60)  # Hot endpoint /devices
app_info_ip_limiter = RateLimiter(requests_per_minute=300, window_seconds=60)  # Public endpoint /app/info
partner_summary_ip_limiter = RateLimiter(requests_per_minute=180, window_seconds=60)  # Hot partner endpoint
unauth_sensitive_ip_limiter = RateLimiter(requests_per_minute=60, window_seconds=60)  # Неавторизованные запросы на чувствительные endpoints


def _is_trusted_proxy_ip(ip: str) -> bool:
    # Явный allowlist proxy IP/сетей через env (CSV): RATE_LIMIT_TRUSTED_PROXIES
    trusted = [item.strip() for item in os.getenv("RATE_LIMIT_TRUSTED_PROXIES", "").split(",") if item.strip()]
    if not trusted:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for item in trusted:
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            else:
                if addr == ipaddress.ip_address(item):
                    return True
        except ValueError:
            continue
    return False


def get_client_ip(request: Request) -> str:
    # Берем X-Forwarded-For только если непосредственный клиент — доверенный proxy.
    xff = request.headers.get("x-forwarded-for")
    direct_ip = request.client.host if request.client and request.client.host else ""
    if xff and direct_ip and _is_trusted_proxy_ip(direct_ip):
        return xff.split(",")[0].strip()
    if direct_ip:
        return direct_ip
    return "unknown"

async def rate_limit_middleware(request: Request, call_next):
    """Middleware для rate limiting"""
    client_ip = get_client_ip(request)

    # Защита горячих endpoint'ов от штормов/ддос-пиков по IP
    if request.url.path == "/auth/telegram" and request.method == "POST":
        allowed, wait_time = await auth_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /auth/telegram by ip={client_ip}, wait={wait_time}s")
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                headers={"Retry-After": str(wait_time)}
            )
    elif request.url.path == "/user" and request.method == "GET":
        allowed, wait_time = await user_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /user by ip={client_ip}, wait={wait_time}s")
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                headers={"Retry-After": str(wait_time)}
            )
    elif request.url.path == "/devices" and request.method == "GET":
        allowed, wait_time = await devices_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /devices by ip={client_ip}, wait={wait_time}s")
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                headers={"Retry-After": str(wait_time)}
            )
    elif request.url.path == "/app/info" and request.method == "GET":
        allowed, wait_time = await app_info_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /app/info by ip={client_ip}, wait={wait_time}s")
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                headers={"Retry-After": str(wait_time)}
            )
    elif request.url.path == "/partner/summary" and request.method == "GET":
        allowed, wait_time = await partner_summary_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /partner/summary by ip={client_ip}, wait={wait_time}s")
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                headers={"Retry-After": str(wait_time)}
            )
    
    # Проверяем только определенные эндпоинты
    if request.url.path == "/user/reset_devices" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await reset_devices_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for reset_devices by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
    
    elif request.url.path == "/user/family/revoke" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await family_revoke_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for family/revoke by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )

    elif request.url.path == "/promo/validate" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await promo_validate_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/validate by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов к проверке промокодов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )

    elif request.url.path == "/promo/redeem" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await promo_redeem_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/redeem by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов к активации промокодов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
    
    # Продолжаем обработку запроса
    response = await call_next(request)
    return response

async def get_user_id_from_request(request: Request) -> Optional[int]:
    """Извлекает user_id из запроса для rate limiting"""
    try:
        # Получаем Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        # Bearer JWT mode
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if not token:
                return None
            try:
                from bloobcat.funcs.auth_tokens import decode_access_token
                payload = decode_access_token(token)
                user_id = payload.get("sub") or payload.get("user_id")
                return int(user_id) if user_id is not None else None
            except Exception:
                return None

        # Telegram initData mode
        from bloobcat.settings import telegram_settings
        from aiogram.utils.web_app import safe_parse_webapp_init_data

        user = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(),
            auth_header
        )
        if user and user.user:
            return user.user.id
        return None
    except Exception as e:
        logger.debug(f"Failed to extract user_id from request: {e}")
        return None