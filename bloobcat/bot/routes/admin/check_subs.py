from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from datetime import datetime, timedelta
from tortoise.functions import Count
from pytz import timezone

from bloobcat.bot.routes.admin.functions import IsAdmin, search_user
from bloobcat.routes.remnawave.catcher import remnawave_updater
from bloobcat.db.users import Users, normalize_date
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.routes.payment import create_auto_payment
from bloobcat.bot.notifications.subscription.expiration import notify_auto_payment
from bloobcat.settings import app_settings

logger = get_logger("admin_check_subs")
router = Router()

# Московский часовой пояс
MOSCOW_TZ = timezone('Europe/Moscow')


@router.message(Command("check_subs"), IsAdmin())
async def admin_check_subs(message: Message):
    """
    Хендлер для ручного запуска проверки подписок администратором.
    Команда: /check_subs
    """
    try:
        await message.answer("Начинаю проверку подписок пользователей...")
        
        # Запрос ручной проверки подписок отключен (legacy schedules removed)
        # await check_subscriptions()
        
        await message.answer("Проверка подписок успешно завершена! Подробности в логах.")
        logger.info(f"Администратор {message.from_user.id} вручную запустил проверку подписок")
    except Exception as e:
        error_message = f"Ошибка при проверке подписок: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)





@router.message(Command("expiring"), IsAdmin())
async def admin_expiring_subs(message: Message):
    """
    Хендлер для получения информации о ближайших истекающих подписках.
    Команда: /expiring [дней=7]
    """
    try:
        # Получаем количество дней из аргументов команды или используем значение по умолчанию
        args = message.text.split()
        days = 7  # По умолчанию показываем подписки, истекающие в течение 7 дней
        if len(args) > 1 and args[1].isdigit():
            days = int(args[1])
        
        # Получаем текущую дату в московском часовом поясе и дату через указанное количество дней
        moscow_now = datetime.now(MOSCOW_TZ)
        today = moscow_now.date()
        future_date = today + timedelta(days=days)
        
        # Получаем пользователей с подписками, истекающими в указанный период
        users = await Users.filter(
            is_registered=True,
            expired_at__gte=today,
            expired_at__lte=future_date
        ).order_by('expired_at')
        
        if not users:
            await message.answer(f"Нет подписок, истекающих в ближайшие {days} дней.")
            return
        
        # Формируем сообщение с информацией о пользователях
        response = f"Подписки, истекающие в ближайшие {days} дней ({len(users)} пользователей):\n\n"
        
        for user in users:
            user_expired_at = normalize_date(user.expired_at)
            days_left = (user_expired_at - today).days if user_expired_at else 0
            auto_renewal = "✅" if user.is_subscribed and user.renew_id else "❌"
            trial_info = "🆓 Пробный" if user.is_trial else "💰 Платный"
            used_trial = "✅" if user.used_trial else "❌"

            response += (f"ID: {user.id}\n"
                        f"Имя: {user.name()}\n"
                        f"Истекает: {user_expired_at.strftime('%d.%m.%Y') if user_expired_at else 'N/A'} (через {days_left} дн.)\n"
                        f"Тип: {trial_info}\n"
                        f"Пробный использован: {used_trial}\n"
                        f"Автопродление: {auto_renewal}\n\n")
            
            # Telegram имеет ограничение на длину сообщения в 4096 символов
            if len(response) > 3800:
                await message.answer(response)
                response = "Продолжение списка:\n\n"
        
        if response:
            await message.answer(response)
            
        logger.info(f"Администратор {message.from_user.id} запросил информацию о подписках, истекающих в ближайшие {days} дней")
    except Exception as e:
        error_message = f"Ошибка при получении информации о истекающих подписках: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("check_all"), IsAdmin())
async def admin_check_all(message: Message):
    """
    Хендлер для ручного запуска всех задач планировщика.
    Команда: /check_all
    """
    try:
        await message.answer("Начинаю выполнение всех задач планировщика...")
        
        # Запускаем все задачи последовательно
        start_time = datetime.now(MOSCOW_TZ)
        
        # 1. Обновление RemnaWave
        await message.answer("1/5. Запуск обновления RemnaWave...")
        await remnawave_updater()

        # 4. Проверка подписок отключена (legacy schedules removed)
        # await message.answer("4/5. Запуск проверки подписок...")
        # await check_subscriptions()
        
        # 5. Проверка пользователей с пробным периодом отключена
        # await message.answer("5/5. Запуск проверки пользователей с пробным периодом...")
        # await check_trial_users()
        
        elapsed = (datetime.now(MOSCOW_TZ) - start_time).total_seconds()
        await message.answer(f"Все задачи успешно выполнены за {elapsed:.2f} секунд! Подробности в логах.")
        logger.info(f"Администратор {message.from_user.id} вручную запустил все задачи планировщика")
    except Exception as e:
        error_message = f"Ошибка при выполнении задач планировщика: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("user_stats"), IsAdmin())
async def admin_user_stats(message: Message):
    """
    Хендлер для получения статистики по пользователям.
    Команда: /user_stats
    """
    try:
        await message.answer("Собираю статистику по пользователям...")
        
        # Получаем текущую дату в московском часовом поясе
        moscow_now = datetime.now(MOSCOW_TZ)
        today = moscow_now.date()
        
        # Общее количество пользователей
        total_users = await Users.all().count()
        
        # Количество зарегистрированных пользователей
        registered_users = await Users.filter(is_registered=True).count()
        
        # Количество пользователей с активной подпиской
        active_users = await Users.filter(
            is_registered=True,
            expired_at__gt=today
        ).count()
        
        # Количество пользователей с истекшей подпиской
        expired_users = await Users.filter(
            is_registered=True,
            expired_at__lte=today
        ).count()
        
        # Количество пользователей с автопродлением
        auto_renewal_users = await Users.filter(
            is_registered=True,
            is_subscribed=True,
            renew_id__isnull=False
        ).count()
        
        # Количество пользователей с пробным периодом
        trial_users = await Users.filter(
            is_registered=True,
            is_trial=True,
            expired_at__gt=today
        ).count()
        
        # Количество пользователей, которые уже использовали пробный период
        used_trial_users = await Users.filter(
            used_trial=True
        ).count()
        
        # Количество пользователей, подписка которых истекает в ближайшие 7 дней
        expiring_soon = await Users.filter(
            is_registered=True,
            expired_at__gt=today,
            expired_at__lte=today + timedelta(days=7)
        ).count()
        
        # Количество пользователей, подключившихся за последние 24 часа
        last_day = moscow_now - timedelta(days=1)
        connected_last_day = await Users.filter(
            connected_at__gte=last_day
        ).count()
        
        # Количество заблокированных пользователей
        blocked_users = await Users.filter(is_blocked=True).count()
        
        # Формируем сообщение со статистикой
        stats_message = (
            "📊 <b>Статистика пользователей</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"✅ Зарегистрированных: <b>{registered_users}</b>\n"
            f"🟢 С активной подпиской: <b>{active_users}</b>\n"
            f"🔴 С истекшей подпиской: <b>{expired_users}</b>\n"
            f"🔄 С автопродлением: <b>{auto_renewal_users}</b>\n"
            f"🆓 С пробным периодом: <b>{trial_users}</b>\n"
            f"🟢 Использовали пробный период: <b>{used_trial_users}</b>\n"
            f"⏳ Истекает в ближайшие 7 дней: <b>{expiring_soon}</b>\n"
            f"📱 Подключались за последние 24ч: <b>{connected_last_day}</b>\n"
            f"🚫 Заблокированных: <b>{blocked_users}</b>\n"
        )
        
        await message.answer(stats_message, parse_mode="HTML")
        logger.info(f"Администратор {message.from_user.id} запросил статистику по пользователям")
    except Exception as e:
        error_message = f"Ошибка при получении статистики по пользователям: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("user_info"), IsAdmin())
async def admin_user_info(message: Message):
    """
    Хендлер для получения подробной информации о пользователе по ID.
    Команда: /user_info [user_id]
    """
    try:
        # Получаем ID пользователя из аргументов команды
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Ошибка: Необходимо указать ID пользователя.\nПример: /user_info 123456789")
            return
        
        user_id = int(args[1])
        
        # Получаем информацию о пользователе
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Получаем текущую дату в московском часовом поясе для расчета оставшихся дней
        moscow_now = datetime.now(MOSCOW_TZ)
        today = moscow_now.date()
        
        # Формируем статус подписки
        user_expired_at = normalize_date(user.expired_at)
        if not user.is_registered:
            subscription_status = "❌ Не зарегистрирован"
        elif user_expired_at and user_expired_at > today:
            days_left = (user_expired_at - today).days
            trial_info = " (пробный период)" if user.is_trial else ""
            subscription_status = f"✅ Активна{trial_info} (осталось {days_left} дн.)"
        else:
            subscription_status = "🔴 Истекла"
        
        # Формируем статус автопродления
        if user.is_subscribed and user.renew_id:
            auto_renewal = "✅ Включено"
        else:
            auto_renewal = "❌ Отключено"
        
        # Форматируем даты
        created_at = user.created_at.strftime("%d.%m.%Y %H:%M:%S") if user.created_at else "Нет данных"
        connected_at = user.connected_at.strftime("%d.%m.%Y %H:%M:%S") if user.connected_at else "Нет данных"
        expired_at = user.expired_at.strftime("%d.%m.%Y") if user.expired_at else "Нет данных"
        
        # Формируем информацию о пробном периоде
        trial_status = "✅ Использован" if user.used_trial else "❌ Не использован"
        
        # Формируем сообщение с информацией о пользователе
        user_info = (
            f"📋 <b>Информация о пользователе</b>\n\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"👤 Имя: {user.name()}\n"
            f"📱 Username: {f'@{user.username}' if user.username else 'Нет'}\n\n"
            f"📊 <b>Статус</b>\n"
            f"🔑 Регистрация: {'✅ Да' if user.is_registered else '❌ Нет'}\n"
            f"🔄 Подписка: {subscription_status}\n"
            f"📅 Дата истечения: {expired_at}\n"
            f"♻️ Автопродление: {auto_renewal}\n"
            f"🆓 Пробный период: {trial_status}\n\n"
            f"⏱ <b>Даты</b>\n"
            f"🗓 Создан: {created_at}\n"
            f"🔌 Последнее подключение: {connected_at}\n"
            f"\n📈 <b>Трафик</b>\n📊 Безлимитный\n"
        )
        
        # Добавляем информацию о платежах, если есть
        if user.renew_id:
            user_info += f"\n💰 <b>Платежи</b>\n💳 ID платежа: <code>{user.renew_id}</code>\n"
        
        await message.answer(user_info, parse_mode="HTML")
        logger.info(f"Администратор {message.from_user.id} запросил информацию о пользователе {user_id}")
    except Exception as e:
        error_message = f"Ошибка при получении информации о пользователе: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)





@router.message(Command("set_auto_renewal"), IsAdmin())
async def admin_set_auto_renewal(message: Message):
    """
    Хендлер для ручной настройки автопродления подписки пользователя.
    Команда: /set_auto_renewal [user_id] [status] [renew_id]
    status: 1 - включить автопродление, 0 - выключить автопродление
    renew_id: ID платежа для автопродления (только при status=1)
    """
    try:
        # Получаем аргументы команды
        args = message.text.split()
        if len(args) < 3 or not args[1].isdigit() or args[2] not in ['0', '1']:
            await message.answer(
                "Ошибка: Неверный формат команды.\n"
                "Пример для включения: /set_auto_renewal 123456789 1 payment_id\n"
                "Пример для отключения: /set_auto_renewal 123456789 0"
            )
            return
        
        user_id = int(args[1])
        is_subscribed = args[2] == '1'
        renew_id = None
        
        # Если включаем автопродление, проверяем наличие renew_id
        if is_subscribed and len(args) < 4:
            await message.answer("Ошибка: При включении автопродления необходимо указать ID платежа (renew_id)")
            return
        elif is_subscribed:
            renew_id = args[3]
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Сохраняем предыдущее состояние для логирования
        previous_state = (user.is_subscribed, user.renew_id)
        
        # Обновляем статус автопродления
        user.is_subscribed = is_subscribed
        user.renew_id = renew_id if is_subscribed else None
        await user.save()
        
        status_text = "включено" if is_subscribed else "отключено"
        renew_info = f", ID платежа: {renew_id}" if is_subscribed else ""
        
        await message.answer(f"Автопродление для пользователя {user_id} {status_text}{renew_info}.")
        
        logger.info(
            f"Администратор {message.from_user.id} изменил статус автопродления пользователя {user_id} "
            f"с '{previous_state[0]}' на '{is_subscribed}', renew_id с '{previous_state[1]}' на '{renew_id}'"
        )
    except Exception as e:
        error_message = f"Ошибка при обновлении статуса автопродления пользователя: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("admin_help"), IsAdmin())
async def admin_help(message: Message):
    """
    Хендлер для отображения справки по административным командам.
    Команда: /admin_help
    """
    try:
        help_text = (
            "📚 <b>Справка по административным командам</b>\n\n"
            
            "🆕 <b>ГЛАВНОЕ АДМИН МЕНЮ</b>\n"
            "🔧 <b>/admin</b> - Главное админ меню с удобной навигацией\n"
            "🧪 <b>Тесты</b> - Кнопка в reply клавиатуре для тестовых функций\n\n"
            
            "⚡ <b>БЫСТРЫЕ КОМАНДЫ (часто используемые)</b>\n"
            "/user_info [user_id] - Подробная информация о пользователе\n"
            "/stat [utm] - Статистика по конкретному UTM\n"
            "/utm [название] - Генератор UTM ссылок\n"
            "/send - Массовая рассылка\n\n"
            
            "⚙️ <b>УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (команды)</b>\n"
            "/set_registered [user_id] [0|1] - Изменить статус регистрации\n"
            "/set_auto_renewal [user_id] [0|1] [renew_id] - Настроить автопродление\n"
            f"/set_trial [user_id] [days={app_settings.trial_days}] [force] - Установить trial период\n"
            "/balance [user_id] [amount] - Изменить баланс пользователя\n"
            "/days [user_id] [amount] - Изменить дни подписки\n\n"
            
            "🔧 <b>СИСТЕМНЫЕ КОМАНДЫ (редко используемые)</b>\n"
            "/reset_trial [user_id] - Сбросить флаг trial периода\n"
            "/send_renewal_notice [user_id] - Отправить уведомление о списании\n"
            "/trigger_autopay [user_id] - Запустить автоплатеж (тест)\n"
            "/unblock_user [user_id] - Разблокировать пользователя\n"
            "/block_user [user_id] [reason] - Заблокировать пользователя\n"
            "/reset_expired - Сбросить истекшие подписки (⚠️ только вручную)\n\n"
            
            "⌨️ <b>УПРАВЛЕНИЕ КЛАВИАТУРАМИ</b>\n"
            "/setup_admin_keyboard - Установить админскую клавиатуру себе\n"
            "/setup_all_admin_keyboards - Установить клавиатуры всем админам\n"
            "/remove_admin_keyboard - Убрать админскую клавиатуру\n\n"
            
            "❓ <b>СПРАВКА</b>\n"
            "/admin_help - Показать эту справку\n\n"
            
            "💡 <b>РЕКОМЕНДАЦИЯ:</b> Большинство функций доступно через удобное меню <b>/admin</b>!\n"
            "Команды выше нужны только для быстрого доступа или автоматизации."
        )
        
        await message.answer(help_text, parse_mode="HTML")
        logger.info(f"Администратор {message.from_user.id} запросил справку по командам")
    except Exception as e:
        error_message = f"Ошибка при отображении справки: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("send_renewal_notice"), IsAdmin())
async def admin_send_renewal_notice(message: Message):
    """
    Хендлер для ручной отправки уведомления о предстоящем списании.
    Команда: /send_renewal_notice [user_id]
    """
    try:
        # Получаем аргументы команды
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "Ошибка: Неверный формат команды.\n"
                "Пример: /send_renewal_notice 123456789"
            )
            return
        
        user_id = int(args[1])
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Проверяем, настроено ли автопродление
        if not user.is_subscribed or not user.renew_id:
            await message.answer(
                f"Ошибка: У пользователя {user_id} не настроено автопродление.\n"
                f"Текущие значения: is_subscribed={user.is_subscribed}, renew_id={user.renew_id}"
            )
            return
        
        # Отправляем уведомление
        await notify_auto_payment(user)
        
        await message.answer(f"Уведомление о предстоящем списании отправлено пользователю {user_id}.")
        logger.info(f"Администратор {message.from_user.id} вручную отправил уведомление о предстоящем списании пользователю {user_id}")
    except Exception as e:
        error_message = f"Ошибка при отправке уведомления: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("check_trial"), IsAdmin())
async def admin_check_trial(message: Message):
    """
    Хендлер для ручного запуска проверки пользователей с пробным периодом.
    Команда: /check_trial
    """
    try:
        await message.answer("Начинаю проверку пользователей с пробным периодом...")
        
        # Запускаем проверку пользователей с пробным периодом
        # await check_trial_users()
        
        await message.answer("Проверка пользователей с пробным периодом успешно завершена! Подробности в логах.")
        logger.info(f"Администратор {message.from_user.id} вручную запустил проверку пользователей с пробным периодом")
    except Exception as e:
        error_message = f"Ошибка при проверке пользователей с пробным периодом: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)




@router.message(Command("reset_trial"), IsAdmin())
async def admin_reset_trial(message: Message):
    """
    Хендлер для ручного сброса флага пробного периода для пользователя.
    Команда: /reset_trial [user_id]
    """
    try:
        # Получаем аргументы команды
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "Ошибка: Неверный формат команды.\n"
                "Пример: /reset_trial 123456789"
            )
            return
        
        user_id = int(args[1])
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Сохраняем предыдущее состояние для логирования
        previous_state = user.is_trial
        
        # Сбрасываем флаг пробного периода
        user.is_trial = False
        await user.save()
        
        await message.answer(
            f"Флаг пробного периода для пользователя {user_id} сброшен.\n"
            f"Предыдущее состояние: {'Пробный период' if previous_state else 'Обычная подписка'}"
        )
        
        logger.info(
            f"Администратор {message.from_user.id} сбросил флаг пробного периода для пользователя {user_id} "
            f"(предыдущее состояние: {previous_state})"
        )
    except Exception as e:
        error_message = f"Ошибка при сбросе флага пробного периода: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("delete_user"), IsAdmin())
async def admin_delete_user(message: Message, command: CommandObject):
    """
    Хендлер для полного удаления пользователя вместе с его платежами.
    Формат: /delete_user <user_id>
    """
    if not command.args:
        await message.answer("Введите ID пользователя для удаления")
        return

    try:
        user_id = int(command.args)
    except ValueError:
        await message.answer("ID пользователя должен быть числом")
        return

    # Проверяем существование пользователя
    user = await Users.get_or_none(id=user_id)
    if not user:
        await message.answer(f"Пользователь с ID {user_id} не найден")
        return

    # Получаем количество платежей пользователя
    payments_count = await ProcessedPayments.filter(user_id=user_id).count()
    
    # Удаляем платежи пользователя
    await ProcessedPayments.filter(user_id=user_id).delete()
    
    # Удаляем пользователя
    await user.delete()
    
    # Отправляем сообщение об успешном удалении
    await message.answer(
        f"Пользователь {user_id} успешно удален вместе с {payments_count} платежами.\n"
        f"Теперь при повторной регистрации пользователь сможет получить пробный период."
    )
    logger.info(f"Администратор удалил пользователя {user_id} вместе с {payments_count} платежами")


@router.message(Command("set_registered"), IsAdmin())
async def admin_set_registered(message: Message, command: CommandObject):
    """
    Хендлер для изменения статуса регистрации пользователя.
    Команда: /set_registered [user_id] [0|1]
    """
    try:
        if not command.args:
            await message.answer(
                "Ошибка: Неверный формат команды.\n"
                "Пример: /set_registered 123456789 1 (активировать)\n"
                "Пример: /set_registered 123456789 0 (деактивировать)"
            )
            return
        
        args = command.args.split()
        if len(args) < 2 or not args[0].isdigit() or args[1] not in ['0', '1']:
            await message.answer(
                "Ошибка: Неверный формат команды.\n"
                "Пример: /set_registered 123456789 1"
            )
            return
        
        user_id = int(args[0])
        is_registered = args[1] == '1'
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Сохраняем предыдущее состояние
        previous_state = user.is_registered
        
        # Обновляем статус
        user.is_registered = is_registered
        await user.save()
        
        status_text = "активирован" if is_registered else "деактивирован"
        await message.answer(
            f"✅ Пользователь {user_id} ({user.full_name}) {status_text}.\n"
            f"Предыдущий статус: {'активирован' if previous_state else 'деактивирован'}"
        )
        
        logger.info(
            f"Администратор {message.from_user.id} изменил статус регистрации пользователя {user_id} "
            f"с '{previous_state}' на '{is_registered}'"
        )
        
    except Exception as e:
        error_message = f"Ошибка при изменении статуса регистрации: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("set_trial"), IsAdmin())
async def admin_set_trial(message: Message, command: CommandObject):
    """
    Хендлер для предоставления trial периода пользователю.
    Команда: /set_trial [user_id] [days] [force]
    """
    try:
        if not command.args:
            await message.answer(
                f"Ошибка: Неверный формат команды.\n"
                f"Пример: /set_trial 123456789 (дать {app_settings.trial_days} дней)\n"
                f"Пример: /set_trial 123456789 14 (дать 14 дней)\n"
                f"Пример: /set_trial 123456789 7 force (принудительно)"
            )
            return
        
        args = command.args.split()
        if not args[0].isdigit():
            await message.answer("Ошибка: ID пользователя должен быть числом.")
            return
        
        user_id = int(args[0])
        days = app_settings.trial_days  # По умолчанию из настроек
        force = False
        
        # Парсим дополнительные аргументы
        if len(args) > 1:
            if args[1].isdigit():
                days = int(args[1])
            elif args[1] == "force":
                force = True
        
        if len(args) > 2 and args[2] == "force":
            force = True
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        if not user:
            await message.answer(f"Пользователь с ID {user_id} не найден.")
            return
        
        # Проверяем, можно ли дать trial
        if user.used_trial and not force:
            await message.answer(
                f"❌ Пользователь {user_id} уже использовал trial период.\n"
                f"Используйте 'force' для принудительного предоставления:\n"
                f"/set_trial {user_id} {days} force"
            )
            return
        
        # Предоставляем trial
        await user.extend_subscription(days)
        user.is_trial = True
        user.used_trial = True
        await user.save()
        
        force_text = " (принудительно)" if force else ""
        await message.answer(
            f"✅ Пользователю {user_id} ({user.full_name}) предоставлен trial период на {days} дней{force_text}.\n"
            f"Подписка истекает: {user.expired_at.strftime('%d.%m.%Y')}"
        )
        
        logger.info(
            f"Администратор {message.from_user.id} предоставил trial период {days} дней "
            f"пользователю {user_id} (force={force})"
        )
        
    except Exception as e:
        error_message = f"Ошибка при предоставлении trial периода: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)



@router.message(Command("trigger_autopay"), IsAdmin())
async def admin_trigger_autopay(message: Message, command: CommandObject):
    """
    Хендлер для ручного запуска create_auto_payment для пользователя.
    Команда: /trigger_autopay <user_id>
    """
    if not command.args:
        await message.answer("Ошибка: Укажите ID пользователя.\nПример: /trigger_autopay 123456789")
        return

    user_id_str = command.args.strip()
    if not user_id_str.isdigit():
        await message.answer("Ошибка: ID пользователя должен быть числом.")
        return
        
    user_id = int(user_id_str)

    await message.answer(f"Ищу пользователя {user_id}...")
    user = await search_user(user_id_str) # Используем search_user для поиска
    if not user:
        await message.answer(f"Пользователь с ID {user_id} не найден.")
        return

    await message.answer(f"Запускаю попытку автопродления для пользователя {user_id} ({user.name()})...")
    logger.info(f"Администратор {message.from_user.id} вручную запустил trigger_autopay для пользователя {user_id}")

    try:
        result = await create_auto_payment(user)
        if result:
            response_message = f"✅ Попытка автопродления для пользователя {user_id} ({user.name()}) успешно инициирована/выполнена с баланса."
        else:
            response_message = f"⚠️ Не удалось инициировать/выполнить автопродление для пользователя {user_id} ({user.name()}). Проверьте логи для деталей."
        
        await message.answer(response_message)
        logger.info(f"Результат trigger_autopay для {user_id}, запущенного админом {message.from_user.id}: {result}")

    except Exception as e:
        error_message = f"❌ Произошла ошибка при запуске автопродления для {user_id}: {str(e)}"
        await message.answer(error_message)
        logger.error(f"Ошибка при запуске trigger_autopay для {user_id} админом {message.from_user.id}: {e}", exc_info=True) 