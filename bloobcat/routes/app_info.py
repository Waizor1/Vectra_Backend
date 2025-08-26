from fastapi import APIRouter
from bloobcat.bot.bot import get_bot_username # Предполагаемый путь
from bloobcat.settings import app_settings  # Импортируем настройки
from bloobcat.config import referral_percent  # Импортируем конфигурацию реферальных отчислений

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
    # Получаем процент реферальных отчислений из конфигурации
    referral_percent_value = referral_percent[0][1] if referral_percent else 40
    return {
        "bot_username": username,
        "trial_days": app_settings.trial_days,
        "referral_percent": referral_percent_value
    } 