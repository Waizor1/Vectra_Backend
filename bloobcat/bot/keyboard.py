from aiogram.types import WebAppInfo
from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger

logger = get_logger("keyboard")


async def webapp_inline_button(
    text: str = "Личный кабинет", url: str = ""
) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    
    # Нормализуем URL правильно - избегаем двойных слешей
    base_url = telegram_settings.miniapp_url.rstrip('/')  # Убираем слеш в конце базового URL
    path = url.lstrip('/') if url else ''  # Убираем слеш в начале пути
    
    # Формируем итоговый URL
    if path:
        final_url = f"{base_url}/{path}"
    else:
        final_url = base_url
    
    # Логируем для отладки
    logger.debug(f"Creating WebApp button: text='{text}', original_path='{url}', normalized_path='{path}', final_url='{final_url}'")
    
    keyboard.button(
        text=text, web_app=WebAppInfo(url=final_url)
    )
    return keyboard.as_markup()
