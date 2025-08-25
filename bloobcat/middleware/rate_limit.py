import time
import asyncio
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from bloobcat.logger import get_logger

logger = get_logger("rate_limit")

class RateLimiter:
    """Простой rate limiter для FastAPI эндпоинтов"""
    
    def __init__(self, requests_per_minute: int = 5, window_seconds: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.requests: Dict[str, list] = {}  # user_id -> list of timestamps
    
    def is_allowed(self, user_id: str) -> Tuple[bool, Optional[int]]:
        """
        Проверяет, разрешен ли запрос для пользователя
        
        Returns:
            Tuple[bool, Optional[int]]: (разрешено, время до следующего разрешения в секундах)
        """
        now = time.time()
        
        # Очищаем старые записи
        if user_id in self.requests:
            self.requests[user_id] = [
                timestamp for timestamp in self.requests[user_id]
                if now - timestamp < self.window_seconds
            ]
        else:
            self.requests[user_id] = []
        
        # Проверяем лимит
        if len(self.requests[user_id]) >= self.requests_per_minute:
            # Вычисляем время до следующего разрешения
            oldest_request = min(self.requests[user_id])
            next_allowed = oldest_request + self.window_seconds - now
            return False, max(0, int(next_allowed))
        
        # Добавляем текущий запрос
        self.requests[user_id].append(now)
        return True, None

# Создаем экземпляры rate limiter для разных эндпоинтов
reset_devices_limiter = RateLimiter(requests_per_minute=2, window_seconds=60)  # 2 запроса в минуту
family_revoke_limiter = RateLimiter(requests_per_minute=1, window_seconds=300)  # 1 запрос в 5 минут
promo_validate_limiter = RateLimiter(requests_per_minute=5, window_seconds=60)  # 5 запросов в минуту
promo_redeem_limiter = RateLimiter(requests_per_minute=2, window_seconds=60)  # 2 запроса в минуту

async def rate_limit_middleware(request: Request, call_next):
    """Middleware для rate limiting"""
    
    # Проверяем только определенные эндпоинты
    if request.url.path == "/user/reset_devices" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = reset_devices_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for reset_devices by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )
    
    elif request.url.path == "/user/family/revoke" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = family_revoke_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for family/revoke by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )

    elif request.url.path == "/promo/validate" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = promo_validate_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/validate by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов к проверке промокодов. Попробуйте снова через {wait_time} секунд.",
                    headers={"Retry-After": str(wait_time)}
                )

    elif request.url.path == "/promo/redeem" and request.method == "POST":
        user_id = await get_user_id_from_request(request)
        if user_id:
            allowed, wait_time = promo_redeem_limiter.is_allowed(str(user_id))
            if not allowed:
                logger.warning(f"Rate limit exceeded for promo/redeem by user {user_id}, wait {wait_time}s")
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком много запросов к активации промокодов. Попробуйте снова через {wait_time} секунд.",
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
        
        # Импортируем здесь, чтобы избежать циклических импортов
        from bloobcat.funcs.validate import validate
        from bloobcat.settings import telegram_settings
        from aiogram.utils.web_app import safe_parse_webapp_init_data
        
        # Парсим данные Telegram WebApp
        user = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), 
            auth_header
        )
        return user.user.id
    except Exception as e:
        logger.debug(f"Failed to extract user_id from request: {e}")
        return None