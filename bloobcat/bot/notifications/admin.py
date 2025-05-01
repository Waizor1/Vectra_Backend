from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from bloobcat.bot.bot import bot
from bloobcat.settings import admin_settings
from bloobcat.logger import get_logger

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
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as btn_error:
                logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
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
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None, utm: str | None = None
):
    try:
        text = f"""👤 Новая регистрация в боте!

👤 Пользователь: {name}
🆔 ID пользователя: {user_id}"""

        if referrer_id:
            text += f"\n👨‍👩‍👧‍👦 Реферер: {referrer_name} (ID: {referrer_id})"
        else:
            text += "\n👨‍👩‍👧‍👦 Реферер: Отсутствует"
            
        if utm:
            text += f"\n🎯 UTM: {utm}"
            
        text += "\n\n#новый_пользователь"
        
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(
                    user_id, referrer_id if referrer_id else 0
                ),
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации бота: {str(e)}")

async def on_activated_key(
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None, utm: str | None = None
):
    try:
        text = f"""✅ Активация ключа пользователем!

👤 Пользователь: {name}
🆔 ID пользователя: {user_id}"""

        if referrer_name:
            text += f"\n👨‍👩‍👧‍👦 Реферер: {referrer_name} (ID: {referrer_id})"
        else:
            text += "\n👨‍👩‍👧‍👦 Реферер: Отсутствует"
            
        if utm:
            text += f"\n🎯 UTM: {utm}"
            
        text += "\n\n#активация #ключ"
            
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(
                    user_id, referrer_id if referrer_id else 0
                ),
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text
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
    payment_id: str,
    is_auto: bool = False,
):
    try:
        # Получаем информацию о пользователе
        from bloobcat.db.users import Users
        user = await Users.get_or_none(id=user_id)
        
        # Формируем имя пользователя
        user_name = user.full_name if user else f"ID: {user_id}"
        username = f"@{user.username}" if user and user.username else "Нет юзернейма"
        
        # Проверяем статус автопродления
        # is_sub передается из payment.py и содержит значение user.is_subscribed
        # Дополнительно проверяем наличие renew_id у пользователя (для подстраховки)
        recurrent_status = "✅ Да" if is_sub and (user and user.renew_id) else "❌ Нет"
        
        # Определяем, является ли текущий платеж автосписанием
        is_auto_payment = is_auto or ("auto" in method.lower())
        auto_payment_status = "✅ Да" if is_auto_payment else "❌ Нет"
        
        text = f"""💰 Успешная оплата пользователя!

👤 Пользователь: {user_name} ({username})
🆔 ID пользователя: {user_id}
💸 Сумма платежа: {amount}₽
📅 Период подписки: {months} месяц(ев)
🔄 Автоматическое списание: {auto_payment_status}
♻️ Автопродление: {recurrent_status}
💳 Метод оплаты: {method}
🧾 ID платежа: {payment_id}"""

        if referrer:
            text += f"\n👨‍👩‍👧‍👦 Реферер: {referrer}"
            
        # Добавляем соответствующий хештег
        if is_auto_payment:
            text += "\n\n#оплата #подписка #автосписание"
        else:
            text += "\n\n#оплата #подписка"
            
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(user_id)
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о платеже: {str(e)}")

async def cancel_subscription(user, reason="Пользователь отключил автопродление"):
    """
    Отправляет уведомление в админ чат о том, что пользователь отключил автопродление подписки.
    
    Args:
        user: Пользователь, отключивший автопродление
        reason: Причина отключения автопродления (по умолчанию - пользователь сам отключил)
    """
    try:
        # Формируем информацию о пользователе
        user_name = user.full_name
        username = f"@{user.username}" if user.username else "Нет юзернейма"
        
        # Определяем дату окончания подписки
        from datetime import date
        days_remaining = (user.expired_at - date.today()).days if user.expired_at else 0
        expiration_info = f"{user.expired_at.strftime('%d.%m.%Y')} (осталось {days_remaining} дн.)" if user.expired_at else "Нет активной подписки"
        
        text = f"""❌ Отключение автопродления подписки!

👤 Пользователь: {user_name} ({username})
🆔 ID пользователя: {user.id}
📅 Дата окончания подписки: {expiration_info}
📝 Причина: {reason}

После окончания текущего периода подписка не будет продлена автоматически.

#отмена #автопродление"""
        
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(user.id)
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить уведомление об отключении автопродления с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text
            )
            
        logger.info(f"Отправлено уведомление об отключении автопродления для пользователя {user.id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об отключении автопродления: {str(e)}")
