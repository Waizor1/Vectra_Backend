from aiogram.types import WebAppInfo
from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.settings import telegram_settings


async def webapp_inline_button(
    text: str = "Личный кабинет", url: str = ""
) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text=text, web_app=WebAppInfo(url=telegram_settings.miniapp_url + url)
    )
    return keyboard.as_markup()
