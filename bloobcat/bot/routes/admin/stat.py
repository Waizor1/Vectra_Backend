from datetime import datetime, timedelta
import logging

from aiogram.dispatcher.router import Router
from aiogram.filters.command import Command, CommandObject
from aiogram.types.message import Message
from pytz import UTC, timezone
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.bot.routes.admin.functions import IsPartnerOrAdmin
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments

router = Router()
logger = logging.getLogger(__name__)

# Московский часовой пояс
MOSCOW_TZ = timezone('Europe/Moscow')


@router.message(Command("stat"), IsPartnerOrAdmin())
async def admin_stat(message: Message, command: CommandObject):
    utm = command.args
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    payed = 0
    # Count how many registered users have at least one successful payment
    registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
    if registered_ids:
        paid_user_ids = await ProcessedPayments.filter(
            user_id__in=registered_ids, status="succeeded"
        ).values_list("user_id", flat=True)
        payed = len(set(paid_user_ids))
    else:
        payed = 0

    now_moscow = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M:%S")
    percent_registered = amount and registered / amount * 100 or 0
    percent_payed = registered and payed / registered * 100 or 0

    stats_message = (
        f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
        f"<i>отчет на {now_moscow}</i>\n\n"
        f"👥 Всего зашли: <b>{amount}</b>\n"
        f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
        f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
    )
    await message.answer(stats_message, parse_mode="HTML")


@router.message(Command("online"), IsPartnerOrAdmin())
async def online_(message: Message):
    m = await message.answer("⏳ <i>Подождите...</i>", parse_mode="HTML")

    # Используем московское время для проверки онлайна
    moscow_now = datetime.now(MOSCOW_TZ)
    active_users = await Users.filter(
        connected_at__gte=moscow_now - timedelta(minutes=15)
    )
    i = len(active_users)

    await m.edit_text(f"👥 <b>Сейчас онлайн:</b> <code>{i}</code>", parse_mode="HTML")


@router.message(Command("stats"), IsPartnerOrAdmin())
async def stats_all(message: Message):
    await show_utm_list(message, page=0)


async def show_utm_list(message, page=0, edit_message=False):
    """Показывает список UTM с пагинацией"""
    # Получаем все UTM, включая None
    raw_utms = await Users.all().values_list("utm", flat=True)
    
    # Фильтруем None и пустые строки, получаем уникальные значения
    utms = list(set([utm for utm in raw_utms if utm is not None and utm != ""]))
    logger.debug(f"UTMs после фильтрации: {utms}")
    
    if not utms:
        return await message.answer("Нет зарегистрированных UTM", parse_mode="HTML")
    
    # Сортируем UTM для консистентности
    utms.sort()
    
    # Считаем общую статистику (включая пользователей без UTM)
    total_users = await Users.all().count()
    total_registered = await Users.filter(is_registered=True).count()
    
    # Получаем количество пользователей онлайн
    now_moscow = datetime.now(MOSCOW_TZ)
    active_users_online = await Users.filter(
        connected_at__gte=now_moscow - timedelta(minutes=15)
    ).count()
    
    # Получаем все ID зарегистрированных пользователей
    registered_ids_all = await Users.filter(is_registered=True).values_list("id", flat=True)
    total_paid = 0
    if registered_ids_all:
        paid_user_ids_all = await ProcessedPayments.filter(
            user_id__in=registered_ids_all, status="succeeded"
        ).values_list("user_id", flat=True)
        total_paid = len(set(paid_user_ids_all))
    
    # Считаем активных пользователей (подписка не истекла)
    # Для проверки истечения подписки используем дату в московском времени
    moscow_today = now_moscow.date()
    total_active_now = await Users.filter(is_registered=True, expired_at__gt=moscow_today).count()
    
    now_str = now_moscow.strftime("%d.%m.%Y %H:%M:%S")
    percent_registered_total = total_users and total_registered / total_users * 100 or 0
    percent_paid_total = total_registered and total_paid / total_registered * 100 or 0
    percent_active_now = total_registered and total_active_now / total_registered * 100 or 0 # Calculate percentage of active users
    
    # Создаем сообщение с общей статистикой
    lines = [
        f"📊 <b>Общая статистика:</b> <i>отчет на {now_str}</i>\n",
        f"👥 Всего пользователей: <b>{total_users}</b>",
        f"✅ Активировано: <b>{total_registered}</b> (<i>{percent_registered_total:.1f}%</i>)",
        f"⚡ Активны сейчас: <b>{total_active_now}</b> (<i>{percent_active_now:.1f}%)</i>", # Add new line for active users
        f"💰 Оплачено: <b>{total_paid}</b> (<i>{percent_paid_total:.1f}%</i>)",
        f"🟢 Сейчас онлайн: <b>{active_users_online}</b>" # Use correct variable name
    ]
    
    # Определяем пагинацию
    items_per_page = 5  # Показываем 5 UTM на странице
    total_pages = (len(utms) + items_per_page - 1) // items_per_page
    
    # Ограничиваем номер страницы
    page = max(0, min(page, total_pages - 1))
    
    # Получаем UTM для текущей страницы
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(utms))
    current_page_utms = utms[start_idx:end_idx]
    
    # Строим клавиатуру пагинации
    builder = InlineKeyboardBuilder()
    
    # Кнопки для UTM на текущей странице с информацией о статистике
    for utm in current_page_utms:
        # Получаем статистику для этого UTM для отображения в кнопке
        amount = await Users.filter(utm=utm).count()
        registered = await Users.filter(utm=utm, is_registered=True).count()
        
        # Считаем оплативших для UTM
        payed = 0
        registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
        if registered_ids:
            paid_user_ids = await ProcessedPayments.filter(
                user_id__in=registered_ids, status="succeeded"
            ).values_list("user_id", flat=True)
            payed = len(set(paid_user_ids))

        # Считаем активных сейчас для UTM
        active_now_utm = await Users.filter(utm=utm, is_registered=True, expired_at__gt=moscow_today).count()
        
        utm_str = str(utm)
        # Обновляем текст кнопки, добавляя активных
        button_text = f"{utm_str} ({amount}|{registered}|{active_now_utm}|{payed})" # Add active_now_utm
        # Добавляем каждую кнопку в отдельную строку
        builder.row(InlineKeyboardButton(text=button_text, callback_data=f"utm:{utm_str}:{page}"))
    
    # Кнопки навигации (предыдущая, номер страницы, следующая)
    if total_pages > 1:
        nav_row = []
        
        # Кнопка "Назад"
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="◀️ Назад", 
                callback_data=f"stats_page_{page-1}"
            ))
        
        # Индикатор страницы
        nav_row.append(InlineKeyboardButton(
            text=f"{page+1}/{total_pages}", 
            callback_data="stats_noop"
        ))
        
        # Кнопка "Вперед"
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="Вперед ▶️", 
                callback_data=f"stats_page_{page+1}"
            ))
        
        builder.row(*nav_row)
    
    # Кнопка "Главное меню" только для админов, партнеры используют постраничное переключение
    
    # Соединяем сообщение и отправляем
    message_text = "\n".join(lines)
    
    if edit_message and hasattr(message, 'edit_text'):
        try:
            await message.edit_text(
                message_text, 
                parse_mode="HTML", 
                reply_markup=builder.as_markup()
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            # Если не удалось отредактировать, отправляем новое
            await message.answer(
                message_text, 
                parse_mode="HTML", 
                reply_markup=builder.as_markup()
            )
    else:
        await message.answer(
            message_text, 
            parse_mode="HTML", 
            reply_markup=builder.as_markup()
        )


async def show_utm_detail(message, utm, page=0):
    """Показывает детальную статистику по конкретному UTM"""
    # Compute stats for specific UTM
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
    
    payed = 0
    if registered_ids:
        paid_user_ids = await ProcessedPayments.filter(
            user_id__in=registered_ids, status="succeeded"
        ).values_list("user_id", flat=True)
        payed = len(set(paid_user_ids))

    # Count currently active users for this UTM
    now_moscow = datetime.now(MOSCOW_TZ)
    moscow_today = now_moscow.date()
    active_now = await Users.filter(utm=utm, is_registered=True, expired_at__gt=moscow_today).count()
    
    now_str = now_moscow.strftime("%d.%m.%Y %H:%M:%S")
    percent_registered = amount and registered / amount * 100 or 0
    percent_payed = registered and payed / registered * 100 or 0
    percent_active_now = registered and active_now / registered * 100 or 0 # Calculate percentage of active users
    
    stats_message = (
        f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
        f"<i>отчет на {now_str}</i>\n\n"
        f"👥 Всего зашли: <b>{amount}</b>\n"
        f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
        f"⚡ Активны сейчас: <b>{active_now}</b> (<i>{percent_active_now:.1f}%)</i>\n"  # Add new line
        f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
    )
    
    # Создаем кнопку "Назад" к списку UTM
    builder = InlineKeyboardBuilder()
    builder.button(
        text="◀️ Вернуться к списку", 
        callback_data=f"back_to_list_{page}"
    )
    
    try:
        await message.edit_text(
            stats_message, 
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения детальной статистики: {e}")
        await message.answer(
            stats_message, 
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )


@router.callback_query(lambda c: c.data and c.data.startswith(("utm:", "stats_page_", "back_to_list_", "stats_noop")), IsPartnerOrAdmin())
async def callback_handler(callback: CallbackQuery):
    # Acknowledge callback to remove loading spinner
    await callback.answer()
    
    if callback.data == "stats_noop":
        await callback.answer("Текущая страница")
        return
        

        
    # Обработка навигации по страницам статистики
    if callback.data.startswith("stats_page_"):
        try:
            page = int(callback.data[len("stats_page_"):])
            await show_utm_list(callback.message, page=page, edit_message=True)
            return
        except ValueError:
            logger.error(f"Неверный формат страницы статистики: {callback.data}")
    
    # Обработка кнопки "назад к списку"
    if callback.data.startswith("back_to_list_"):
        try:
            page = int(callback.data[len("back_to_list_"):])
            await show_utm_list(callback.message, page=page, edit_message=True)
            return
        except ValueError:
            logger.error(f"Неверный формат страницы для возврата: {callback.data}")
    
    # Обработка нажатия на UTM
    if callback.data.startswith("utm:"):
        parts = callback.data.split(":", 2)
        if len(parts) == 3:
            utm = parts[1]
            page = int(parts[2])
            await show_utm_detail(callback.message, utm, page)
            return
        
    # Если ничего не сработало
    logger.error(f"Неизвестный формат callback_data: {callback.data}")
    await callback.answer("Неизвестная команда")


@router.message(Command("trial_stats"), IsPartnerOrAdmin())
async def trial_extension_stats(message: Message):
    """ОПТИМИЗИРОВАНО: Показывает детальную статистику trial extension уведомлений"""
    m = await message.answer("⏳ <i>Загружаю оптимизированную статистику...</i>", parse_mode="HTML")
    
    try:
        # Импортируем функции статистики из scheduler
        from bloobcat.scheduler import get_trial_extension_stats, reset_trial_extension_stats
        
        stats = get_trial_extension_stats()
        
        # Форматируем время работы
        uptime_hours = stats.get("uptime_hours", 0)
        if uptime_hours < 1:
            uptime_str = f"{uptime_hours * 60:.0f} мин"
        elif uptime_hours < 24:
            uptime_str = f"{uptime_hours:.1f} ч"
        else:
            uptime_str = f"{uptime_hours / 24:.1f} дн"
        
        # Производительность в реальном времени
        performance = stats.get("performance", {})
        mps = performance.get("messages_per_second", 0.0)
        processed = performance.get("processed_count", 0)
        eta_formatted = stats.get("eta_formatted", "Неизвестно")
        
        # Telegram API здоровье
        telegram_health = stats.get("telegram_health", {})
        rate_limit_hits = telegram_health.get("rate_limit_hits", 0)
        api_error_rate = telegram_health.get("api_error_rate", 0.0)
        
        # Статистика успешности
        success_rate = stats.get("success_rate", 0.0)
        
        # Формируем текст статистики
        text = f"""📊 <b>СТАТИСТИКА TRIAL EXTENSIONS</b>
<i>ОПТИМИЗИРОВАНО для 10k+ пользователей</i>

⚡ <b>ПРОИЗВОДИТЕЛЬНОСТЬ:</b>
• Скорость: <code>{mps:.2f} сообщений/сек</code>
• Обработано: <code>{processed}</code> из <code>{stats.get('total_attempts', 0)}</code>
• ETA завершения: <code>{eta_formatted}</code>
• Настройка: <code>28.5 сообщений/сек</code> (лимит: 30)

📈 <b>СТАТИСТИКА ОТПРАВКИ:</b>
• Успешно: <code>{stats.get('successful_notifications', 0)}</code>
• Ошибки: <code>{stats.get('failed_notifications', 0)}</code>
• Таймауты: <code>{stats.get('timeouts', 0)}</code>
• Успешность: <code>{success_rate:.1f}%</code>

🚦 <b>TELEGRAM API ЗДОРОВЬЕ:</b>
• Rate limit 429: <code>{rate_limit_hits}</code> раз
• Процент API ошибок: <code>{api_error_rate:.2f}%</code>
• Время работы: <code>{uptime_str}</code>

⚙️ <b>ОПТИМИЗАЦИИ:</b>
• Delay: <code>0.035с</code> (было: 0.5с)
• Timeout: <code>600с</code> (было: 120с)
• Retry with proper retry_after handling
• Exponential backoff + jitter

<i>Обновлено: {stats.get('last_reset', 'Неизвестно')}</i>"""
        
        # Клавиатура с действиями
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Обновить", callback_data="refresh_trial_stats")
        builder.button(text="🗑 Сбросить", callback_data="reset_trial_stats")
        builder.button(text="🧪 Тестировать", callback_data="test_trial_notification")
        builder.adjust(2, 1)
        
        await m.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        
    except Exception as e:
        await m.edit_text(f"❌ <b>Ошибка получения статистики:</b>\n<code>{e}</code>", parse_mode="HTML")


@router.callback_query(lambda c: c.data and c.data in ["refresh_trial_stats", "reset_trial_stats"], IsPartnerOrAdmin())
async def trial_stats_callback_handler(callback: CallbackQuery):
    """Обрабатывает кнопки статистики trial extensions"""
    await callback.answer()
    
    try:
        from bloobcat.scheduler import get_trial_extension_stats, reset_trial_extension_stats
        
        if callback.data == "reset_trial_stats":
            reset_trial_extension_stats()
            await callback.message.answer("✅ Статистика сброшена", parse_mode="HTML")
        
        # В любом случае показываем обновленную статистику
        stats = get_trial_extension_stats()
        
        uptime_hours = stats.get("uptime_hours", 0)
        if uptime_hours < 1:
            uptime_str = f"{uptime_hours * 60:.0f} мин"
        elif uptime_hours < 24:
            uptime_str = f"{uptime_hours:.1f} ч"
        else:
            days = uptime_hours // 24
            hours = uptime_hours % 24
            uptime_str = f"{days:.0f}д {hours:.1f}ч"
        
        stats_message = [
            f"📊 <b>Статистика Trial Extensions</b>",
            f"<i>с {stats['last_reset'].strftime('%d.%m %H:%M')}, работает: {uptime_str}</i>\n",
            f"🔄 Всего попыток: <b>{stats['total_attempts']}</b>",
            f"✅ Успешно: <b>{stats['successful_notifications']}</b>",
            f"❌ Неудачно: <b>{stats['failed_notifications']}</b>",
            f"⏰ Таймауты: <b>{stats['timeouts']}</b>",
            f"🚦 Rate limited: <b>{stats['rate_limited']}</b>",
            f"📈 Успешность: <b>{stats['success_rate']:.1f}%</b>"
        ]
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_trial_stats"),
            InlineKeyboardButton(text="🗑 Сбросить", callback_data="reset_trial_stats")
        )
        
        await callback.message.edit_text(
            "\n".join(stats_message), 
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Ошибка:</b> {e}", parse_mode="HTML")


@router.message(Command("test_trial"), IsPartnerOrAdmin())
async def test_trial_notification_command(message: Message, command: CommandObject):
    """Тестирует отправку trial extension уведомления"""
    args = command.args
    if not args:
        await message.answer("❌ Укажите ID пользователя: <code>/test_trial 123456</code>", parse_mode="HTML")
        return
    
    try:
        user_id = int(args.strip())
    except ValueError:
        await message.answer("❌ Некорректный ID пользователя", parse_mode="HTML")
        return
    
    m = await message.answer("⏳ <i>Тестирую отправку уведомления...</i>", parse_mode="HTML")
    
    try:
        from bloobcat.test_notifications import test_trial_extension_notification
        
        result = await test_trial_extension_notification(user_id)
        
        if result["success"]:
            response = [
                f"✅ <b>Тест успешен!</b>",
                f"👤 Пользователь: {result['details'].get('user_full_name', 'N/A')}",
                f"🌐 Язык: {result['details'].get('user_language', 'unknown')}",
                f"⏱ Время отправки: {result['timing'].get('notification_duration', 0):.2f}с",
                f"⏱ Общее время: {result['timing'].get('total_duration', 0):.2f}с"
            ]
        else:
            response = [
                f"❌ <b>Тест неудачен</b>",
                f"🔍 Пользователь найден: {result['details'].get('user_exists', False)}",
                f"❗ Ошибка: <code>{result.get('error', 'Unknown')}</code>",
                f"💬 Детали: {result['details'].get('message', 'N/A')}"
            ]
            
        await m.edit_text("\n".join(response), parse_mode="HTML")
        
    except Exception as e:
        await m.edit_text(f"❌ <b>Критическая ошибка:</b> {e}", parse_mode="HTML")


@router.callback_query(lambda c: c.data == "refresh_trial_stats")
async def refresh_trial_stats_callback(callback: CallbackQuery):
    """Обновляет статистику trial extensions"""
    await callback.answer("🔄 Обновляю статистику...")
    
    try:
        from bloobcat.scheduler import get_trial_extension_stats
        stats = get_trial_extension_stats()
        
        # Используем тот же код форматирования что и в главной команде
        uptime_hours = stats.get("uptime_hours", 0)
        if uptime_hours < 1:
            uptime_str = f"{uptime_hours * 60:.0f} мин"
        elif uptime_hours < 24:
            uptime_str = f"{uptime_hours:.1f} ч"
        else:
            uptime_str = f"{uptime_hours / 24:.1f} дн"
        
        performance = stats.get("performance", {})
        mps = performance.get("messages_per_second", 0.0)
        processed = performance.get("processed_count", 0)
        eta_formatted = stats.get("eta_formatted", "Неизвестно")
        
        telegram_health = stats.get("telegram_health", {})
        rate_limit_hits = telegram_health.get("rate_limit_hits", 0)
        api_error_rate = telegram_health.get("api_error_rate", 0.0)
        
        success_rate = stats.get("success_rate", 0.0)
        
        text = f"""📊 <b>СТАТИСТИКА TRIAL EXTENSIONS</b>
<i>ОПТИМИЗИРОВАНО для 10k+ пользователей</i>

⚡ <b>ПРОИЗВОДИТЕЛЬНОСТЬ:</b>
• Скорость: <code>{mps:.2f} сообщений/сек</code>
• Обработано: <code>{processed}</code> из <code>{stats.get('total_attempts', 0)}</code>
• ETA завершения: <code>{eta_formatted}</code>
• Настройка: <code>28.5 сообщений/сек</code> (лимит: 30)

📈 <b>СТАТИСТИКА ОТПРАВКИ:</b>
• Успешно: <code>{stats.get('successful_notifications', 0)}</code>
• Ошибки: <code>{stats.get('failed_notifications', 0)}</code>
• Таймауты: <code>{stats.get('timeouts', 0)}</code>
• Успешность: <code>{success_rate:.1f}%</code>

🚦 <b>TELEGRAM API ЗДОРОВЬЕ:</b>
• Rate limit 429: <code>{rate_limit_hits}</code> раз
• Процент API ошибок: <code>{api_error_rate:.2f}%</code>
• Время работы: <code>{uptime_str}</code>

⚙️ <b>ОПТИМИЗАЦИИ:</b>
• Delay: <code>0.035с</code> (было: 0.5с)
• Timeout: <code>600с</code> (было: 120с)
• Retry with proper retry_after handling
• Exponential backoff + jitter

<i>Обновлено: {stats.get('last_reset', 'Неизвестно')}</i>"""
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Обновить", callback_data="refresh_trial_stats")
        builder.button(text="🗑 Сбросить", callback_data="reset_trial_stats")
        builder.button(text="🧪 Тестировать", callback_data="test_trial_notification")
        builder.adjust(2, 1)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Ошибка обновления:</b>\n<code>{e}</code>", parse_mode="HTML")


@router.callback_query(lambda c: c.data == "reset_trial_stats")
async def reset_trial_stats_callback(callback: CallbackQuery):
    """Сбрасывает статистику trial extensions"""
    await callback.answer("🗑 Сбрасываю статистику...")
    
    try:
        from bloobcat.scheduler import reset_trial_extension_stats
        reset_trial_extension_stats()
        
        await callback.message.edit_text(
            "✅ <b>Статистика сброшена!</b>\n\n"
            "Все счётчики обнулены, таймеры сброшены.\n"
            "Новая статистика начнёт собираться с этого момента.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Ошибка сброса:</b>\n<code>{e}</code>", parse_mode="HTML")


@router.callback_query(lambda c: c.data == "test_trial_notification")
async def test_trial_notification_callback(callback: CallbackQuery):
    """Показывает форму для тестирования trial notification"""
    await callback.answer()
    
    await callback.message.edit_text(
        "🧪 <b>Тестирование Trial Notification</b>\n\n"
        "Отправьте команду в формате:\n"
        "<code>/test_trial USER_ID</code>\n\n"
        "Например: <code>/test_trial 123456789</code>\n\n"
        "Это протестирует оптимизированную отправку уведомления "
        "конкретному пользователю с новыми настройками.",
        parse_mode="HTML"
    )



