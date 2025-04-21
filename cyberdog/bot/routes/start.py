from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from cyberdog.bot.keyboard import webapp_inline_button
from cyberdog.db.users import Users

router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message, command: CommandObject):
    # Просто получаем или создаем пользователя, без обработки аргументов команды
    # Обработка referral ID и UTM теперь происходит в validate при запуске WebApp
    await Users.get_user(message.from_user)

    # Отправляем стикер и приветственное сообщение с кнопкой WebApp
    try:
        await message.answer_sticker(
            "CAACAgIAAxkBAAEM07Fm6envLIjKfJExwf9VZOXzI8K2WwACMTQAAugboErSr6fEZiaivDYE"
        )
    except TelegramAPIError:
        pass
    await message.answer(
        """🐶 Добро пожаловать в CyberDog VPN! 🚀 

Ты только что сделал первый шаг к свободному и безопасному интернету! 🎉  

🔹 Что ты получаешь с CyberDog VPN?  
✅ YouTube и Instagram без рекламы  
✅ Безлимитный трафик и скорость
✅ Полная анонимность и защита данных  
✅ Самый надежный VPN-протокол
✅ Бесплатный тестовый период!  

🔥 Нажми кнопку «Запустить» ниже и подключайся прямо сейчас! 🔥""",
        reply_markup=await webapp_inline_button("Запустить"),
    )


@router.callback_query()
async def call_all(call: CallbackQuery):
    await call.answer(
        "Мы обновились! Напишите /start чтобы продолжить работу",
        show_alert=True,
        cache_time=3600,
    )
