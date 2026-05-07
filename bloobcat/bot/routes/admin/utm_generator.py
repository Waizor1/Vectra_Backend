import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from .functions import IsPartnerOrAdmin
from bloobcat.bot.bot import get_bot_username

router = Router()

UTM_SOURCE_MAX_LEN = 50


class UtmForm(StatesGroup):
    waiting_for_source = State()


@router.message(Command("utm"), IsPartnerOrAdmin())
async def cmd_utm(message: Message, state: FSMContext):
    """Handles the /utm command from an admin."""
    await message.answer(
        "Введите название UTM-метки (например, 'vk_ads', 'channel_promo').\n\n"
        "Допустимы только латиница, цифры и символ подчеркивания `_`, до 50 символов.\n\n"
        "Ссылка откроет чат с ботом и кнопку «START» — это сохраняет лида, "
        "если пользователь не дойдёт до Mini App.",
    )
    await state.set_state(UtmForm.waiting_for_source)


@router.message(UtmForm.waiting_for_source, F.text, IsPartnerOrAdmin())
async def process_utm_source(message: Message, state: FSMContext, bot: Bot):
    """Processes the UTM source entered by the admin."""
    utm_source = message.text.strip()

    if not re.match(r"^[a-zA-Z0-9_]+$", utm_source) or len(utm_source) > UTM_SOURCE_MAX_LEN:
        await message.answer(
            "Некорректное название UTM-метки. Используйте только латиницу, цифры и `_`, "
            f"до {UTM_SOURCE_MAX_LEN} символов. Попробуйте снова или отмените командой /cancel.",
        )
        return

    if not bot:
        await message.answer("Ошибка: Не удалось получить информацию о боте.")
        await state.clear()
        return

    try:
        bot_username = (await get_bot_username() or "").strip().lstrip("@")
        if not bot_username:
            raise RuntimeError("bot username unavailable")
        user_id = message.from_user.id
        payload = f"{utm_source}-{user_id}"
        link = f"https://t.me/{bot_username}?start={payload}"
        await message.answer(
            f"Готово! Ваша ссылка:\n<code>{link}</code>\n\n"
            "Откроет чат с ботом и кнопку «START». После старта пользователь "
            "попадёт в Mini App, а атрибуция сохранится в payload.",
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"Error generating UTM link: {e}")
        await message.answer("Произошла ошибка при генерации ссылки.")

    await state.clear()

@router.message(UtmForm.waiting_for_source)
async def process_utm_source_invalid(message: Message):
    """Handles invalid input when waiting for UTM source."""
    await message.answer("Пожалуйста, введите название UTM-метки текстом или отмените командой /cancel.") 