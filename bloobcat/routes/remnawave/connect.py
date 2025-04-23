from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from bloobcat.logger import get_logger
import traceback
from typing import Optional

from bloobcat.db.users import Users
from bloobcat.settings import remnawave_settings
from .client import RemnaWaveClient

logger = get_logger("remnawave_connect")
remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

router = APIRouter(prefix="/remnawave", tags=["remnawave"])

@router.get("/connect/{connection}")
async def connect(connection: str, request: Request):
    logger.info(f"Получен запрос на подключение с URL: {connection}")
    
    try:
        user = await Users.get(connect_url=connection)
        logger.info(f"Найден пользователь: {user.id}, is_registered: {user.is_registered}")
    except Exception as e:
        logger.error(f"Ошибка поиска пользователя по connect_url={connection}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=404, detail="User not found by connection URL")
    
    try:
        logger.info(f"Запрос URL подписки для пользователя {user.id}")
        subscription_url = await remnawave.users.get_subscription_url(user)
        logger.info(f"Получен URL подписки от RemnaWave для пользователя {user.id}: {subscription_url}")
        
        logger.info(f"Редирект на URL: {subscription_url}")
        return RedirectResponse(subscription_url)
    except Exception as e:
        logger.error(f"Ошибка получения URL RemnaWave: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get RemnaWave URL") 