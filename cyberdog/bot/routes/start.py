from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from langdetect import detect, LangDetectException

from cyberdog.bot.keyboard import webapp_inline_button
from cyberdog.db.users import Users

router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message, command: CommandObject):
    user = message.from_user
    # Получаем или создаем пользователя
    await Users.get_user(user)

    # Определяем язык пользователя
    lang_code = user.language_code
    detected_lang = "en"  # По умолчанию английский
    if lang_code:
        try:
            # Используем language_code для определения языка
            if lang_code.startswith("ru"):
                 detected_lang = "ru"
        except LangDetectException:
            pass
            
    # Тексты приветствий (минималистичные)
    welcome_texts = {
        "ru": "Привет! 👋 Нажми кнопку ниже, чтобы запустить BloopCat.",
        "en": "Hi! 👋 Press the button below to launch BloopCat."
    }
    
    button_texts = {
        "ru": "Запустить",
        "en": "Launch"
    }

    # Выбираем текст и текст кнопки в зависимости от языка
    response_text = welcome_texts.get(detected_lang, welcome_texts["en"])
    button_text = button_texts.get(detected_lang, button_texts["en"])

    # Отправляем стикер и приветственное сообщение с кнопкой WebApp
    try:
        await message.answer_sticker(
            "AAMCAgADGQEAATQDs2gGqXiHiPkkNPdsFAKgw0_gVxKDAAJvAAPb234AAZlbUKh7k4B0AQAHbQADNgQ"
        )
    except TelegramAPIError:
        pass
    await message.answer(
        response_text,
        reply_markup=await webapp_inline_button(button_text), # Используем локализованный текст кнопки
    )


@router.callback_query()
async def call_all(call: CallbackQuery):
    await call.answer(
        "Мы обновились! Напишите /start чтобы продолжить работу",
        show_alert=True,
        cache_time=3600,
    )
