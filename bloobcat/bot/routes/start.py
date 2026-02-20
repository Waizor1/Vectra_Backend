from aiogram import Router, F
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings
from urllib.parse import quote

logger = get_logger("bot_start")
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
        "ru": "Привет! 👋 Нажми кнопку ниже, чтобы запустить TVPN.",
        "en": "Hi! 👋 Press the button below to launch TVPN."
    }
    
    button_texts = {
        "ru": "Запустить",
        "en": "Launch"
    }

    # Выбираем текст и текст кнопки в зависимости от языка
    response_text = welcome_texts.get(detected_lang, welcome_texts["en"])
    button_text = button_texts.get(detected_lang, button_texts["en"])

    # Forward /start payload into the Mini App URL as a query param.
    # Telegram's `start_param` is available only for MiniApp deep links, so we store it in the URL
    # and let the Mini App bootstrap send it to backend via /auth/telegram.
    payload = (command.args or "").strip()
    miniapp_url = telegram_settings.miniapp_url
    if payload:
        sep = "&" if "?" in miniapp_url else "?"
        launch_url = f"{miniapp_url}{sep}start={quote(payload, safe='')}"
    else:
        launch_url = miniapp_url

    await message.answer(
        response_text,
        reply_markup=await webapp_inline_button(button_text, url=launch_url),  # локализованный текст кнопки
    )
    
    # Автоматическая установка админской клавиатуры только для уже зарегистрированных админов.
    # Важно: не создаем пользователя на /start, чтобы не надувать БД при DDoS-спаме.
    try:
        user = await Users.get_or_none(id=message.from_user.id)
        if user and user.is_admin:
            from bloobcat.bot.routes.admin.admin_menu import set_admin_keyboard
            await set_admin_keyboard(message.bot, user.id)
            logger.info(f"Автоматически установлена админская клавиатура для пользователя {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при установке админской клавиатуры для {message.from_user.id}: {e}")


