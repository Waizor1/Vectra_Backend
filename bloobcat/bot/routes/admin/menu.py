from aiogram import Router, types
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiogram.filters import Command

from bloobcat.bot.bot import bot
from bloobcat.settings import telegram_settings
from .functions import IsAdmin

router = Router()

@router.message(Command("setmenu"), IsAdmin())
async def set_menu_command(message: types.Message):
    """
    Принудительно обновляет кнопку меню для WebApp.
    """
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Личный кабинет",
                web_app=WebAppInfo(url=telegram_settings.miniapp_url),
            )
        )
        await message.answer("✅ Кнопка меню успешно обновлена!")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка при обновлении кнопки меню: {e}") 