from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from cyberdog.bot.bot import bot
from cyberdog.settings import admin_settings
from cyberdog.logger import get_logger

logger = get_logger("admin_notifications")

async def write_to(user_id: int, referrer_id: int = 0):
    kb = InlineKeyboardBuilder()
    kb.button(text="Написать", url=f"tg://user?id={user_id}")
    if referrer_id:
        kb.button(text="Реферер", url=f"tg://user?id={referrer_id}")
    kb.adjust(1)
    return kb.as_markup()

async def send_admin_message(text: str, reply_markup=None):
    """Общая функция для отправки сообщений админу/в канал"""
    try:
        chat_id = admin_settings.chat_id
        logger.info(f"Отправка сообщения в чат {chat_id}: {text[:100]}...")
        
        # Проверяем, является ли чат каналом
        is_channel = str(chat_id).startswith('-100')
        
        # Для каналов используем HTML-разметку без reply_markup
        if is_channel:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML"
            )
        else:
            # Для личных сообщений используем reply_markup
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            
    except TelegramBadRequest as e:
        if "chat not found" in str(e):
            logger.error(f"Чат {chat_id} не найден. Убедитесь, что бот добавлен в канал или начат диалог с админом")
        else:
            logger.error(f"Ошибка отправки в Telegram: {str(e)}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при отправке сообщения: {str(e)}")

async def on_activated_bot(
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None
):
    try:
        text = f"🔌 {name} #АктивировалБота"
        if referrer_id:
            text += f"\nРеферер: {referrer_name}"
        
        await bot.send_message(
            chat_id=admin_settings.telegram_id,
            text=text,
            reply_markup=await write_to(
                user_id, referrer_id if referrer_id else 0
            ),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации бота: {str(e)}")

async def on_activated_key(
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None
):
    try:
        text = f"🔑 {name} #АктивировалКлюч"
        if referrer_name:
            text += f"\nРеферер: {referrer_name}"
            
        await bot.send_message(
            chat_id=admin_settings.telegram_id,
            text=text,
            reply_markup=await write_to(
                user_id, referrer_id if referrer_id else 0
            ),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации ключа: {str(e)}")

async def on_payment(
    user_id: int,
    is_sub: bool,
    referrer: str | None,
    amount: int,
    months: int,
    method: str,
):
    try:
        text = f"""#ПоступилаОплата💰
Пользователь {user_id}
оплатил подписку на {months} месяцев, на сумму {amount} рублей.
Метод оплаты: {method}
Рекуррентный платеж: {"да" if is_sub else "нет"}"""
        if referrer:
            text += f"\nРеферер: {referrer}"
            
        await bot.send_message(
            chat_id=admin_settings.telegram_id,
            text=text,
            reply_markup=await write_to(user_id)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о платеже: {str(e)}")


# async def cancel_subscription(user: Users):
#     text = f"💔 {user.name()} #ОтменилПодписку по рекуррентным платежам"
#     await bot.send_message(
#         admin_id, text, reply_markup=await write_to(user.id)
#     )
