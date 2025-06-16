from fastapi import APIRouter
from bloobcat.bot.bot import get_bot_username # Предполагаемый путь
from bloobcat.settings import app_settings  # Импортируем настройки

router = APIRouter(prefix="/app", tags=["app_info"])

@router.get("/bot_username")
async def get_bot_username_endpoint():
    """
    Возвращает текущее имя пользователя бота.
    """
    username = await get_bot_username() # Предполагаем, что функция асинхронная
    return {"bot_username": username}

@router.get("/info")
async def get_app_info():
    """
    Возвращает общую информацию о приложении.
    """
    username = await get_bot_username()
    return {
        "bot_username": username,
        "trial_days": app_settings.trial_days
    } 