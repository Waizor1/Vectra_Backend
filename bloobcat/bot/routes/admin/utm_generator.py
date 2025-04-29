import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from .functions import IsAdmin
from bloobcat.settings import telegram_settings

router = Router()


class UtmForm(StatesGroup):
    waiting_for_source = State()


@router.message(Command("utm"), IsAdmin())
async def cmd_utm(message: Message, state: FSMContext):
    """Handles the /utm command from an admin."""
    await message.answer("Введите название UTM-метки (например, 'vk_ads', 'channel_promo'). Используйте только латиницу, цифры и символ подчеркивания `_`.")
    await state.set_state(UtmForm.waiting_for_source)


@router.message(UtmForm.waiting_for_source, F.text, IsAdmin())
async def process_utm_source(message: Message, state: FSMContext, bot: Bot):
    """Processes the UTM source entered by the admin."""
    utm_source = message.text.strip()

    # Basic validation for allowed characters
    if not re.match(r"^[a-zA-Z0-9_]+$", utm_source):
        await message.answer("Некорректное название UTM-метки. Пожалуйста, используйте только латиницу, цифры и символ подчеркивания `_`. Попробуйте снова или отмените командой /cancel.")
        return # Keep the state waiting for correct input

    # Check if bot object is available (should be injected by aiogram)
    if not bot:
        await message.answer("Ошибка: Не удалось получить информацию о боте.")
        await state.clear()
        return
        
    try:
        # Используем URL из настроек
        base_url = telegram_settings.webapp_url
        # Добавляем id пользователя, генерирующего UTM ссылку
        user_id = message.from_user.id
        link = f"{base_url.rstrip('/')}?startapp={utm_source}-{user_id}"
        # Отправляем ссылку как код для легкого копирования, используя HTML parse_mode
        await message.answer(f"Готово! Ваша ссылка:\n<code>{link}</code>", parse_mode="HTML")
    except Exception as e:
        # Log the error if needed
        print(f"Error generating UTM link: {e}") 
        await message.answer("Произошла ошибка при генерации ссылки.")

    await state.clear()

@router.message(UtmForm.waiting_for_source)
async def process_utm_source_invalid(message: Message):
    """Handles invalid input when waiting for UTM source."""
    await message.answer("Пожалуйста, введите название UTM-метки текстом или отмените командой /cancel.") 