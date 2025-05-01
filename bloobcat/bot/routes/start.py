from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users

router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message, command: CommandObject):

    # Определяем язык пользователя
    lang_code = message.from_user.language_code

    detected_lang = "en"
    if lang_code and lang_code.lower().startswith("ru"):
        detected_lang = "ru"

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
            "CAACAgIAAxkBAAE0A7NoBql4h4j5JDT3bBQCoMNP4FcSgwACbwAD29t-AAGZW1Coe5OAdDYE"
        )
    except TelegramAPIError as e:
        print(f"Error sending sticker: {e}")
    await message.answer(
        response_text,
        reply_markup=await webapp_inline_button(button_text), # Используем локализованный текст кнопки
    )


