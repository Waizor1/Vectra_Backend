from aiogram.types import WebAppInfo
from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger

logger = get_logger("keyboard")


def public_app_url(path: str = "") -> str:
    base_url = telegram_settings.miniapp_url.rstrip("/")
    normalized_path = path.lstrip("/")
    return f"{base_url}/{normalized_path}" if normalized_path else base_url


async def webapp_inline_button(
    text: str = "Личный кабинет", url: str = ""
) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    
    # If caller passed an absolute URL - use it as-is (allows query strings, deep links, etc).
    raw_url = (url or "").strip()
    if raw_url.lower().startswith("http://") or raw_url.lower().startswith("https://"):
        final_url = raw_url
        path = ""
    else:
        # Нормализуем URL правильно - избегаем двойных слешей
        base_url = telegram_settings.miniapp_url.rstrip('/')  # Убираем слеш в конце базового URL
        path = raw_url.lstrip('/') if raw_url else ''  # Убираем слеш в начале пути

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


async def start_inline_keyboard(
    launch_text: str,
    docs_text: str,
    launch_url: str = "",
) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()

    raw_launch_url = (launch_url or "").strip() or telegram_settings.miniapp_url
    keyboard.button(text=launch_text, web_app=WebAppInfo(url=raw_launch_url))
    keyboard.button(text=docs_text, url=public_app_url("/legal/"))
    keyboard.adjust(1)
    return keyboard.as_markup()


async def legal_documents_inline_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    if lang == "ru":
        privacy_text = "Политика конфиденциальности"
        terms_text = "Пользовательское соглашение"
        support_text = "Поддержка"
    else:
        privacy_text = "Privacy Policy"
        terms_text = "Terms of Use"
        support_text = "Support"

    keyboard.button(text=privacy_text, url=public_app_url("/legal/privacy/"))
    keyboard.button(text=terms_text, url=public_app_url("/legal/terms/"))
    keyboard.button(text=support_text, url="https://t.me/VectraConnect_support_bot")
    keyboard.adjust(1)
    return keyboard.as_markup()
