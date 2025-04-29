from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from bloobcat.db.users import Users
from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger

logger = get_logger("validate")

oauth2_scheme = APIKeyHeader(name="Authorization", auto_error=False)


async def validate(init_data: str = Depends(oauth2_scheme)) -> Users:
    try:
        logger.info(f"Получены данные для валидации: {init_data[:50]}...")  # логируем только начало для безопасности
        
        if not init_data:
            logger.error("Отсутствует заголовок Authorization")
            raise HTTPException(status_code=403, detail="Missing Authorization header")
            
        user = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), init_data
        )
        logger.info(f"Успешная валидация для пользователя {user.user.id}")
        
        referred_by = 0
        utm = None
        logger.info(f"Проверка start_param для пользователя {user.user.id}: {user.start_param!r}") 
        if user.start_param:
            param = user.start_param
            # Combined UTM and numeric referral (format: <utm>-<referrer_id>)
            if "-" in param:
                # Разделяем по последнему дефису
                utm_part, ref_part = param.rsplit("-", 1)
                if ref_part.isdigit():
                    referred_by = int(ref_part)
                    utm = utm_part
                    logger.info(f"Найдена UTM: '{utm}' и реферал: {referred_by} для пользователя {user.user.id}")
                else:
                    # Если часть после '-' не число, считаем всю строку UTM
                    utm = param
                    logger.info(f"Найдена UTM (без реферала): '{utm}' для пользователя {user.user.id}")
            elif param.startswith("family_"):
                logger.info(f"Пропускаем family link: {param} для пользователя {user.user.id}")
            elif param.isdigit():
                referred_by = int(param)
                logger.info(f"Найден реферал: {referred_by} для пользователя {user.user.id}")
            else:
                utm = param
                logger.info(f"Найдена UTM: '{utm}' для пользователя {user.user.id}")

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
