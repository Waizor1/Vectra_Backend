from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TEST

from bloobcat.settings import script_settings, telegram_settings

TOKEN = telegram_settings.token.get_secret_value()

test_session = AiohttpSession()
test_session.api = TEST

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
    session=test_session if script_settings.dev else None,
)


async def get_bot_username() -> str:
    me = await bot.me()
    if not me.username:
        return ""
    return me.username
