from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
import jwt

from bloobcat.db.users import Users
from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger
from bloobcat.funcs.auth_tokens import decode_access_token

logger = get_logger("validate")

oauth2_scheme = APIKeyHeader(name="Authorization", auto_error=False)


async def validate(init_data: str = Depends(oauth2_scheme)) -> Users:
    try:
        preview = (init_data or "")[:50]
        logger.debug(f"Получены данные для валидации: {preview}...")  # логируем только начало для безопасности
        
        if not init_data:
            logger.error("Отсутствует заголовок Authorization")
            raise HTTPException(status_code=403, detail="Missing Authorization header")

        if init_data.lower().startswith("bearer "):
            token = init_data.split(" ", 1)[1].strip()
            if not token:
                raise HTTPException(status_code=403, detail="Empty bearer token")
            try:
                payload = decode_access_token(token)
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=403, detail="Token expired")
            except jwt.InvalidTokenError:
                raise HTTPException(status_code=403, detail="Invalid token")

            user_id = payload.get("sub") or payload.get("user_id")
            if not user_id:
                raise HTTPException(status_code=403, detail="Invalid token payload")

            try:
                user_id_int = int(user_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=403, detail="Invalid token subject")

            db_user = await Users.get_or_none(id=user_id_int)
            if not db_user:
                raise HTTPException(status_code=403, detail="User not found")

            return db_user
            
        user = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), init_data
        )

        # Проверка на None после парсинга - защита от некорректных данных
        if not user or not user.user:
            logger.error("Парсинг вернул пустой объект пользователя")
            raise HTTPException(status_code=403, detail="Invalid user data")

        logger.debug(f"Успешная валидация для пользователя {user.user.id}")

        referred_by = 0
        utm = None
        logger.debug(f"Проверка start_param для пользователя {user.user.id}: {user.start_param!r}") 
        if user.start_param:
            param = user.start_param
            # Combined UTM and numeric referral (format: <utm>-<referrer_id>)
            if "-" in param:
                # Разделяем по последнему дефису
                utm_part, ref_part = param.rsplit("-", 1)
                if ref_part.isdigit():
                    referred_by = int(ref_part)
                    utm = utm_part
                    logger.debug(f"Найдена UTM: '{utm}' и рефер: {referred_by} для пользователя {user.user.id}")
                else:
                    # Если часть после '-' не число, считаем всю строку UTM
                    utm = param
                    logger.debug(f"Найдена UTM (без реферера): '{utm}' для пользователя {user.user.id}")
            elif param.startswith("family_"):
                logger.debug(f"Пропускаем family link: {param} для пользователя {user.user.id}")
            elif param.isdigit():
                referred_by = int(param)
                logger.debug(f"Найден реферал: {referred_by} для пользователя {user.user.id}")
            else:
                utm = param
                logger.debug(f"Найдена UTM: '{utm}' для пользователя {user.user.id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка валидации: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    
    # Явно передаем параметры по имени для большей ясности
    db_user = await Users.get_user(
        telegram_user=user.user, 
        referred_by=referred_by, 
        utm=utm
    )
    
    if not db_user:
        logger.error("Не удалось получить пользователя из базы данных")
        raise HTTPException(status_code=500, detail="User not found in database")
    
    return db_user
