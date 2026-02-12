from fastapi import APIRouter
from bloobcat.bot.bot import get_bot_username # Предполагаемый путь
from bloobcat.build_info import get_build_info
from bloobcat.settings import app_settings  # Импортируем настройки
from bloobcat.config import referral_percent  # Импортируем конфигурацию реферальных отчислений
from tortoise import Tortoise

from bloobcat.logger import get_logger

router = APIRouter(prefix="/app", tags=["app_info"])
logger = get_logger("app_info")


async def read_maintenance_settings() -> tuple[bool, str]:
    """
    Best-effort read maintenance settings from Directus singleton collection.
    Falls back to disabled mode when Directus table/row is unavailable.
    """
    try:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            """
            SELECT maintenance_mode, maintenance_message
            FROM tvpn_admin_settings
            LIMIT 1
            """
        )
        if not rows:
            return False, ""
        row = rows[0]
        mode = bool(row.get("maintenance_mode", False))
        message_raw = row.get("maintenance_message")
        message = message_raw.strip() if isinstance(message_raw, str) else ""
        return mode, message
    except Exception as exc:
        logger.debug("Maintenance settings unavailable: {}", exc)
        return False, ""

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
    maintenance_mode, maintenance_message = await read_maintenance_settings()
    return {
        "bot_username": username,
        "trial_days": app_settings.trial_days,
        "referral_percent": referral_percent_value,
        "maintenance_mode": maintenance_mode,
        "maintenance_message": maintenance_message,
        **get_build_info(),
    } 