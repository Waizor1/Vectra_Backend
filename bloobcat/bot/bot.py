from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from bloobcat.bot.telegram_api import create_bot_session
from bloobcat.settings import script_settings, telegram_settings

TOKEN = telegram_settings.token.get_secret_value()

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
    session=create_bot_session(
        is_dev=script_settings.dev,
        fallback_ips=telegram_settings.api_fallback_ips,
    ),
)


async def get_bot_username() -> str:
    configured_username = (telegram_settings.username or "").strip().lstrip("@")
    if configured_username:
        return configured_username

    me = await bot.me()
    if not me.username:
        return ""
    return me.username
