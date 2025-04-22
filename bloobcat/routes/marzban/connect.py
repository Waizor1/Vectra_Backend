from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from bloobcat.logger import get_logger

from bloobcat.db.users import Users
from bloobcat.settings import marzban_settings
from .client import MarzbanClient

logger = get_logger("marzban_connect")
marzban = MarzbanClient()

router = APIRouter(prefix="/marzban", tags=["marzban"])


@router.get("/connect/{connection}")
async def connect(connection: str, request: Request):
    logger.info(f"Получен запрос на подключение с URL: {connection}")
    
    try:
        user = await Users.get(connect_url=connection)
        logger.info(f"Найден пользователь: {user.id}")
    except Exception as e:
        logger.error(f"Ошибка поиска пользователя: {str(e)}", exc_info=True)
        raise HTTPException(status_code=404, detail="User not found")
        
    try:
        subscription_path = await marzban.users.get_subscription_url(user)
        logger.info(f"Получен относительный путь подписки от Marzban для пользователя {user.id}: {subscription_path}")
        
        base_url = marzban_settings.url
        if not base_url.endswith('/'):
            base_url += '/'
        if subscription_path.startswith('/'):
            subscription_path = subscription_path[1:]
            
        full_url = base_url + subscription_path
        logger.info(f"Редирект на полный URL: {full_url}")
        
        return RedirectResponse(full_url)
    except Exception as e:
        logger.error(f"Ошибка получения или формирования URL Marzban: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get Marzban URL")
