from urllib.parse import quote

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bloobcat.bot.keyboard import legal_documents_inline_keyboard, start_inline_keyboard
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings

logger = get_logger("bot_start")
router = Router()


def _detect_lang(message: Message) -> str:
    lang_code = message.from_user.language_code
    if lang_code and lang_code.lower().startswith("ru"):
        return "ru"
    return "en"


@router.message(CommandStart())
async def command_start_handler(message: Message, command: CommandObject):

    detected_lang = _detect_lang(message)

    # Тексты приветствий
    welcome_texts = {
        "ru": "Привет.\n\nЭто Vectra Connect.\n\nНажмите кнопку ниже для запуска.",
        "en": "Hello.\n\nThis is Vectra Connect.\n\nPress the button below to launch."
    }
    
    button_texts = {
        "ru": "Запустить",
        "en": "Launch"
    }
    docs_button_texts = {
        "ru": "Документы",
        "en": "Documents",
    }

    # Выбираем текст и текст кнопки в зависимости от языка
    response_text = welcome_texts.get(detected_lang, welcome_texts["en"])
    button_text = button_texts.get(detected_lang, button_texts["en"])
    docs_button_text = docs_button_texts.get(detected_lang, docs_button_texts["en"])

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
        reply_markup=await start_inline_keyboard(
            launch_text=button_text,
            docs_text=docs_button_text,
            launch_url=launch_url,
        ),
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


@router.message(Command("documents", "legal", "docs"))
async def documents_handler(message: Message):
    detected_lang = _detect_lang(message)
    texts = {
        "ru": (
            "Документы Vectra Connect\n\n"
            "Политика конфиденциальности, пользовательское соглашение и контакт поддержки всегда доступны по ссылкам ниже."
        ),
        "en": (
            "Vectra Connect documents\n\n"
            "Privacy Policy, Terms of Use, and support contact are always available below."
        ),
    }

    await message.answer(
        texts.get(detected_lang, texts["en"]),
        reply_markup=await legal_documents_inline_keyboard(detected_lang),
    )
