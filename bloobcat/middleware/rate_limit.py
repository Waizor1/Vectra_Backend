import time
import asyncio
import os
import ipaddress
import hashlib
import uuid
from typing import Any, Dict, Tuple, Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from bloobcat.logger import get_logger

logger = get_logger("rate_limit")

REDIS_SLIDING_WINDOW_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[4]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local oldest_score = tonumber(oldest[2]) or tonumber(ARGV[2])
  local wait = math.max(1, math.floor(oldest_score + tonumber(ARGV[3]) - tonumber(ARGV[2])))
  return {0, wait}
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[5])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {1, 0}
"""

class RateLimiter:
    """Простой rate limiter для FastAPI эндпоинтов"""
    
    def __init__(
        self,
        requests_per_minute: int = 5,
        window_seconds: int = 60,
        *,
        redis_url: str | None = None,
        redis_client: Any | None = None,
        namespace: str = "rate_limit",
        fail_closed: bool = False,
    ):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.requests: Dict[str, list] = {}  # user_id -> list of timestamps
        self._lock = asyncio.Lock()
        self._redis_url = redis_url if redis_url is not None else os.getenv("RATE_LIMIT_REDIS_URL", "").strip()
        self._redis_client = redis_client
        self._namespace = namespace
        self._fail_closed = fail_closed

    async def _get_redis_client(self):
        if self._redis_client is not None:
            return self._redis_client
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as redis  # type: ignore

            self._redis_client = redis.from_url(self._redis_url, decode_responses=True)
            return self._redis_client
        except Exception as exc:
            logger.warning("Redis rate limiter unavailable, using in-memory fallback: {}", exc)
            self._redis_url = ""
            return None

    def _redis_key(self, user_id: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8", errors="ignore")).hexdigest()
        return f"{self._namespace}:{self.window_seconds}:{digest}"

    async def _is_allowed_redis(self, user_id: str) -> Tuple[bool, Optional[int]] | None:
        redis_was_configured = bool(self._redis_client is not None or self._redis_url)
        client = await self._get_redis_client()
        if client is None:
            if redis_was_configured and self._fail_closed:
                logger.warning(
                    "Redis rate limiter unavailable for fail-closed namespace=%s",
                    self._namespace,
                )
                return False, 1
            return None
        now = time.time()
        key = self._redis_key(user_id)
        cutoff = now - self.window_seconds
        try:
            member = f"{now:.6f}:{uuid.uuid4().hex}"
            if hasattr(client, "eval"):
                result = await client.eval(
                    REDIS_SLIDING_WINDOW_SCRIPT,
                    1,
                    key,
                    cutoff,
                    now,
                    self.window_seconds,
                    self.requests_per_minute,
                    member,
                )
                allowed = bool(int(result[0]))
                wait_time = int(result[1] or 0)
                return allowed, (wait_time or None if allowed else max(1, wait_time))

            # Best-effort fallback for Redis-like test doubles that do not expose
            # EVAL. Real Redis uses the atomic Lua path above.
            await client.zremrangebyscore(key, float("-inf"), cutoff)
            count = int(await client.zcard(key))
            if count >= self.requests_per_minute:
                oldest = await client.zrange(key, 0, 0, withscores=True)
                oldest_score = float(oldest[0][1]) if oldest else now
                wait_time = int(max(1, oldest_score + self.window_seconds - now))
                return False, wait_time
            await client.zadd(key, {member: now})
            await client.expire(key, self.window_seconds)
            return True, None
        except Exception as exc:
            if self._fail_closed:
                logger.warning(
                    "Redis rate limiter check failed, denying fail-closed namespace=%s: %s",
                    self._namespace,
                    exc,
                )
                return False, 1
            logger.warning("Redis rate limiter check failed, using in-memory fallback: {}", exc)
            return None
    
    async def is_allowed(self, user_id: str) -> Tuple[bool, Optional[int]]:
        """
        Проверяет, разрешен ли запрос для пользователя
        
        Returns:
            Tuple[bool, Optional[int]]: (разрешено, время до следующего разрешения в секундах)
        """
        redis_result = await self._is_allowed_redis(user_id)
        if redis_result is not None:
            return redis_result

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
reset_devices_limiter = RateLimiter(requests_per_minute=2, window_seconds=60, namespace="reset_devices")  # 2 запроса в минуту
family_revoke_limiter = RateLimiter(requests_per_minute=1, window_seconds=300, namespace="family_revoke")  # 1 запрос в 5 минут
promo_validate_limiter = RateLimiter(requests_per_minute=5, window_seconds=60, namespace="promo_validate")  # 5 запросов в минуту
promo_redeem_limiter = RateLimiter(requests_per_minute=2, window_seconds=60, namespace="promo_redeem")  # 2 запроса в минуту
auth_ip_limiter = RateLimiter(requests_per_minute=120, window_seconds=60, namespace="auth_telegram", fail_closed=True)  # Anti-storm для /auth/telegram
auth_oauth_ip_limiter = RateLimiter(requests_per_minute=60, window_seconds=60, namespace="auth_oauth", fail_closed=True)  # OAuth start/callback/ticket
auth_password_ip_limiter = RateLimiter(requests_per_minute=12, window_seconds=60, namespace="auth_password", fail_closed=True)  # login/register/reset brute-force guard
auth_link_ip_limiter = RateLimiter(requests_per_minute=30, window_seconds=60, namespace="auth_link", fail_closed=True)  # account linking guard
user_ip_limiter = RateLimiter(requests_per_minute=240, window_seconds=60, namespace="user_hot")  # Hot endpoint /user
devices_ip_limiter = RateLimiter(requests_per_minute=240, window_seconds=60, namespace="devices_hot")  # Hot endpoint /devices
app_info_ip_limiter = RateLimiter(requests_per_minute=300, window_seconds=60, namespace="app_info")  # Public endpoint /app/info
welcome_vpn_ip_limiter = RateLimiter(requests_per_minute=180, window_seconds=60, namespace="welcome_vpn")  # Public endpoint /welcome-vpn
partner_summary_ip_limiter = RateLimiter(requests_per_minute=180, window_seconds=60, namespace="partner_summary")  # Hot partner endpoint
error_report_ip_limiter = RateLimiter(requests_per_minute=20, window_seconds=60, namespace="error_report")  # Public client diagnostics endpoint
unauth_sensitive_ip_limiter = RateLimiter(requests_per_minute=60, window_seconds=60, namespace="unauth_sensitive")  # Неавторизованные запросы на чувствительные endpoints


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


def _rate_limited_response(wait_time: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": detail},
        headers={"Retry-After": str(wait_time)},
    )

async def rate_limit_middleware(request: Request, call_next):
    """Middleware для rate limiting"""
    client_ip = get_client_ip(request)

    # Защита горячих endpoint'ов от штормов/ддос-пиков по IP
    if request.url.path == "/auth/telegram" and request.method == "POST":
        allowed, wait_time = await auth_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /auth/telegram by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path.startswith("/auth/oauth/") or request.url.path == "/auth/exchange-ticket":
        allowed, wait_time = await auth_oauth_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for auth oauth by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path.startswith("/auth/password/"):
        allowed, wait_time = await auth_password_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for auth password by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path.startswith("/auth/link/"):
        allowed, wait_time = await auth_link_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for auth link by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/user" and request.method == "GET":
        allowed, wait_time = await user_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /user by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/devices" and request.method == "GET":
        allowed, wait_time = await devices_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /devices by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/app/info" and request.method == "GET":
        allowed, wait_time = await app_info_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /app/info by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/welcome-vpn" and request.method == "GET":
        allowed, wait_time = await welcome_vpn_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /welcome-vpn by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/partner/summary" and request.method == "GET":
        allowed, wait_time = await partner_summary_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /partner/summary by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    elif request.url.path == "/errors/report" and request.method == "POST":
        allowed, wait_time = await error_report_ip_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"Rate limit exceeded for /errors/report by ip={client_ip}, wait={wait_time}s")
            return _rate_limited_response(
                wait_time=wait_time or 1,
                detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
            )
    
    # Проверяем только определенные эндпоинты
    if request.url.path == "/user/reset_devices" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await reset_devices_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for reset_devices by user {user_id}, wait {wait_time}s")
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                )
    
    elif request.url.path == "/user/family/revoke" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await family_revoke_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for family/revoke by user {user_id}, wait {wait_time}s")
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                )

    elif request.url.path == "/promo/validate" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await promo_validate_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/validate by user {user_id}, wait {wait_time}s")
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов к проверке промокодов. Попробуйте снова через {wait_time} секунд.",
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                )

    elif request.url.path == "/promo/redeem" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = await promo_redeem_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/redeem by user {user_id}, wait {wait_time}s")
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов к активации промокодов. Попробуйте снова через {wait_time} секунд.",
                )
        else:
            allowed, wait_time = await unauth_sensitive_ip_limiter.is_allowed(client_ip)
            if not allowed:
                return _rate_limited_response(
                    wait_time=wait_time or 1,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
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
