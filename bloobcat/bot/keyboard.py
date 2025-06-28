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
    
    # Нормализуем URL - убеждаемся что путь начинается с /
    if url and not url.startswith('/'):
        url = '/' + url
    
    # Формируем итоговый URL
    final_url = telegram_settings.miniapp_url + url
    
    # Логируем для отладки
    logger.debug(f"Creating WebApp button: text='{text}', path='{url}', final_url='{final_url}'")
    
    keyboard.button(
        text=text, web_app=WebAppInfo(url=final_url)
    )
    return keyboard.as_markup()
