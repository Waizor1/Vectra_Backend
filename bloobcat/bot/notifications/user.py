from asyncio import sleep
from datetime import datetime

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger

# Инициализация логгера для модуля уведомлений
logger = get_logger("notifications.user")


async def on_activated_key(user: Users):
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        """Включайте, отключайте,
меняйте страну VPN в приложении которое Вы установили""",
    )
    await sleep(5)
    logger.info(f"Отправка второго уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        """Не забудьте вернуться в бот чтобы проверить свой личный кабинет 🎁

Управление ключами VPN происходит в боте. Рекомендуем закрепить бот, чтобы не потерять.

Мы на связи, BlubCat VPN 🤙""",
        reply_markup=await webapp_inline_button(),
    )


async def on_disabled(user: Users):
    logger.info(f"Отправка уведомления об истечении ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        """😢Ваш ключ истек. Пожалуйста, продлите подписку в личном кабинете""",
        reply_markup=await webapp_inline_button("Продлить подписку", "pay"),
    )


async def day_after_disabled(user: Users):
    logger.info(f"Отправка уведомления о сутках до истечения подписки пользователю {user.id}")
    await bot.send_message(
        user.id,
        """До конца подписки остались сутки⏳
Нажмите для продления подписки ⬇️""",
        reply_markup=await webapp_inline_button("Продлить подписку", "pay"),
    )


async def hour_after_disabled(user: Users):
    logger.info(f"Отправка уведомления о часе до истечения подписки пользователю {user.id}")
    await bot.send_message(
        user.id,
        """До конца подписки остался час⏳
Нажмите для продления подписки ⬇️""",
        reply_markup=await webapp_inline_button("Продлить подписку", "pay"),
    )


async def on_referral_payment(user: Users, referral: Users, amount: int):
    to_add = int(amount * user.referral_percent() / 100)
    user.balance += to_add
    await user.save()
    logger.info(f"Начисление реферального бонуса пользователю {user.id} в размере {to_add} руб. за оплату реферала {referral.id} на сумму {amount} руб.")
    text = f"""💰Ваш реферал {referral.name()}
совершил оплату на сумму {amount} руб.

Ваш реф процент {user.referral_percent()}%
Вам зачислено {to_add} руб """
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button(),
    )


async def on_referral_registration(user: Users, referral: Users):
    logger.info(f"Отправка уведомления о регистрации реферала {referral.id} пользователю {user.id}")
    text = f"""🎉Ваш реферал {referral.name()} зарегистрировался в нашем сервисе.
Вы получили 7 дней подписки бесплатно!"""
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button("Личный кабинет"),
    )


async def notify_auto_payment(user: Users):
    """
    Уведомляет пользователя о предстоящем автоматическом платеже
    """
    logger.info(f"Подготовка уведомления об автоплатеже для пользователя {user.id}")
    # Получаем последний платеж пользователя
    last_payment = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).order_by("-processed_at").first()
    
    if not last_payment:
        logger.warning(f"Не найден успешный платеж для пользователя {user.id}, уведомление об автоплатеже не отправлено")
        return
    
    # Получаем оставшиеся дни
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об автоплатеже пользователю {user.id}, дней до списания: {days_remaining}, сумма: {last_payment.amount}")
    
    text = f"""🔄 Автоматическое продление подписки
    
Через {days_remaining} {'день' if days_remaining == 1 else 'дня'} закончится ваша текущая подписка.
В день окончания подписки будет произведено автоматическое списание в размере {last_payment.amount} руб.
для её продления.

Если вы хотите отключить автопродление, нажмите кнопку ниже и перейдите в раздел настроек."""
    
    try:
        # Используем готовую функцию для создания кнопки веб-приложения
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет")
        )
        logger.info(f"Уведомление об автоплатеже успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об автоплатеже пользователю {user.id}: {str(e)}")


async def notify_expiring_subscription(user: Users):
    """
    Уведомляет пользователя без автопродления о скором истечении подписки
    """
    logger.info(f"Подготовка уведомления об истечении подписки для пользователя {user.id}")
    
    # Получаем оставшиеся дни
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days_remaining}")
    
    # Формируем текст в зависимости от количества оставшихся дней
    if days_remaining == 1:
        text = "❗ Ваш ключ истекает через 1 день. Пожалуйста, продлите подписку в личном кабинете."
    elif days_remaining == 2:
        text = "❗ Ваш ключ истекает через 2 дня. Пожалуйста, продлите подписку в личном кабинете."
    elif days_remaining == 3:
        text = "❗ Ваш ключ истекает через 3 дня. Пожалуйста, продлите подписку в личном кабинете."
    else:
        # Для других значений используем общий шаблон
        text = f"⚠️ Истечение подписки\n\nЧерез {days_remaining} {'день' if days_remaining == 1 else 'дня' if 1 < days_remaining < 5 else 'дней'} закончится ваша текущая подписка.\nАвтоматическое продление не включено.\n\nЧтобы VPN продолжил работать, пожалуйста, продлите подписку в личном кабинете."
    
    try:
        # Используем готовую функцию для создания кнопки веб-приложения
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Продлить подписку", "pay")
        )
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {str(e)}")


async def notify_trial_ended(user: Users):
    """
    Уведомляет пользователя о завершении пробного периода
    """
    logger.info(f"Отправка уведомления о завершении пробного периода пользователю {user.id}")
    
    text = """🔥 Ваш пробный период был завершен!
❗Вы можете продлить бесплатный тест период, напишите нам @BlubCatVPN_support
💸 Для продления подписки нажмите на кнопку «продлить подписку»"""
    
    try:
        # Используем готовую функцию для создания кнопки веб-приложения
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Продлить подписку", "pay")
        )
        logger.info(f"Уведомление о завершении пробного периода успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о завершении пробного периода пользователю {user.id}: {str(e)}")


async def notify_no_trial_taken(user: Users, hours_passed: int):
    """
    Уведомляет пользователя, который не взял пробную подписку
    
    Args:
        user: Пользователь
        hours_passed: Количество часов, прошедших с момента разговора с ботом
    """
    logger.info(f"Подготовка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    
    # Проверяем, что у пользователя никогда не было подписки (expired_at == None)
    if user.expired_at is not None:
        logger.info(f"Пользователь {user.id} имеет или имел подписку (expired_at={user.expired_at}), уведомление не отправляется")
        return
    
    # Проверяем, есть ли у пользователя платежи
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).exists()
    
    if has_payments:
        logger.info(f"Пользователь {user.id} имеет платежи, уведомление не отправляется")
        return
    
    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    
    text = """❗Вы еще не подключили бесплатный доступ к VPN.
❗Поддержка поможет подключиться @BlubCatVPN_support
🔥Мы хотим подарить вам приятную скидку, напишите нам, пожалуйста"""
    
    try:
        # Используем готовую функцию для создания кнопки веб-приложения
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Подключить VPN", "second")
        )
        logger.info(f"Уведомление о невзятой пробной подписке успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о невзятой пробной подписке пользователю {user.id}: {str(e)}")


async def notify_auto_renewal_success_balance(user: Users, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    logger.info(f"Отправка уведомления об успешном автопродлении с баланса пользователю {user.id}")
    text = f"""✅ Ваша подписка успешно продлена на {days} дней!

С вашего реферального баланса было списано {amount:.2f} руб."""
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет"),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном автопродлении с баланса для {user.id}: {e}")


async def notify_auto_renewal_failure(user: Users, reason: str = "Неизвестная ошибка"):
    """
    Уведомляет пользователя о неудаче автоматического продления подписки.
    """
    logger.warning(f"Отправка уведомления о НЕУДАЧНОМ автопродлении пользователю {user.id}. Причина: {reason}")
    text = f"""⚠️ Не удалось автоматически продлить вашу подписку.

Причина: {reason}

Пожалуйста, продлите подписку вручную в личном кабинете или обратитесь в поддержку.

Ваш текущий статус автопродления был отключен."""
    # Кнопка для перехода к ручной оплате
    kb = await webapp_inline_button("💳 Продлить вручную")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о неудачном автопродлении для {user.id}: {e}")


async def notify_renewal_success_yookassa(user: Users, days: int, amount_paid_via_yookassa: float, amount_from_balance: float):
    """
    Уведомляет пользователя об успешном продлении подписки через Yookassa
    (включая возможное частичное списание с баланса).
    """
    logger.info(f"Отправка уведомления об успешном продлении (Yookassa) пользователю {user.id}")
    
    message_parts = [f"✅ Ваша подписка успешно продлена на {days} дней!"]
    
    if amount_from_balance > 0:
        message_parts.append(f"\nС вашего реферального баланса было списано {amount_from_balance:.2f} руб.")
        
    if amount_paid_via_yookassa > 0: # Должно быть всегда > 0 в этом сценарии
        message_parts.append(f"С привязанного способа оплаты списано {amount_paid_via_yookassa:.2f} руб.")
        
    text = "\n".join(message_parts)
    
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет"),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном продлении (Yookassa) для {user.id}: {e}")


async def notify_trial_extended(user: Users, days: int):
    """
    Уведомляет пользователя о продлении пробного периода
    """
    logger.info(f"Отправка уведомления о продлении пробного периода пользователю {user.id}")
    
    text = f"""🎉 Ваш пробный период продлен на {days} дней!
    
Мы заметили, что вы еще не успели попробовать наш VPN сервис.
Теперь у вас есть дополнительное время для тестирования.

Если у вас возникнут вопросы, наша поддержка всегда готова помочь @BlubCatVPN_support"""
    
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Подключить VPN", "second")
        )
        logger.info(f"Уведомление о продлении пробного периода успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о продлении пробного периода пользователю {user.id}: {str(e)}")
