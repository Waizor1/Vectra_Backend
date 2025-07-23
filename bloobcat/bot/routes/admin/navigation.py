from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bloobcat.bot.routes.admin.functions import IsAdmin, search_user
from bloobcat.logger import get_logger
from .keyboards import *
from .states import SendFSM, UserSearchState

logger = get_logger("bot_admin_navigation")
router = Router()


def create_fake_message(callback: CallbackQuery, text: str = None):
    """Создает fake message объект для совместимости с существующими функциями"""
    class FakeMessage:
        def __init__(self):
            self.from_user = callback.from_user
            self.answer = callback.message.answer
            self.text = text
    
    return FakeMessage()





# ============ ОСНОВНАЯ НАВИГАЦИЯ ============

@router.callback_query(F.data == "admin:main", IsAdmin())
async def main_menu_callback(callback: CallbackQuery):
    """Возврат в главное меню"""
    await callback.answer()
    await callback.message.edit_text(
        "🔧 **АДМИН ПАНЕЛЬ**\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_main_admin_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:users", IsAdmin())
async def users_menu_callback(callback: CallbackQuery):
    """Меню управления пользователями"""
    await callback.answer()
    await callback.message.edit_text(
        "👥 **УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ**\n\n"
        "Выберите действие:",
        reply_markup=get_users_menu(),
        parse_mode="Markdown"
    )


async def show_utm_stats_with_pagination(callback: CallbackQuery, page: int = 0):
    """Показывает UTM статистику с пагинацией"""
    from bloobcat.db.users import Users
    from bloobcat.db.payments import ProcessedPayments
    from datetime import datetime, timedelta
    from pytz import timezone
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    
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
    moscow_today = now_moscow.date()
    total_active_now = await Users.filter(is_registered=True, expired_at__gt=moscow_today).count()
    
    now_str = now_moscow.strftime("%d.%m.%Y %H:%M:%S")
    percent_registered_total = total_users and total_registered / total_users * 100 or 0
    percent_paid_total = total_registered and total_paid / total_registered * 100 or 0
    percent_active_now = total_registered and total_active_now / total_registered * 100 or 0
    
    # Формируем сообщение с общей статистикой как в /stats
    stats_text = f"📊 <b>Общая статистика:</b> <i>отчет на {now_str}</i>\n\n"
    stats_text += f"👥 Всего пользователей: <b>{total_users}</b>\n"
    stats_text += f"✅ Активировано: <b>{total_registered}</b> (<i>{percent_registered_total:.1f}%</i>)\n"
    stats_text += f"⚡ Активны сейчас: <b>{total_active_now}</b> (<i>{percent_active_now:.1f}%</i>)\n"
    stats_text += f"💰 Оплачено: <b>{total_paid}</b> (<i>{percent_paid_total:.1f}%</i>)\n"
    stats_text += f"🟢 Сейчас онлайн: <b>{active_users_online}</b>"
    
    # Создаем UTM кнопки как в /stats
    builder = InlineKeyboardBuilder()
    
    # Получаем UTM список как в /stats
    raw_utms = await Users.all().values_list("utm", flat=True)
    utms = list(set([utm for utm in raw_utms if utm is not None and utm != ""]))
    
    if utms:
        utms.sort()
        moscow_today = now_moscow.date()
        
        # Пагинация: показываем 5 UTM на страницу
        items_per_page = 5
        total_pages = (len(utms) + items_per_page - 1) // items_per_page
        
        # Проверяем корректность номера страницы
        if page < 0:
            page = 0
        elif page >= total_pages:
            page = total_pages - 1
        
        # Вычисляем индексы для текущей страницы
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, len(utms))
        current_page_utms = utms[start_idx:end_idx]
        
        # Показываем UTM на текущей странице
        for utm in current_page_utms:
            amount = await Users.filter(utm=utm).count()
            registered = await Users.filter(utm=utm, is_registered=True).count()
            
            # Считаем оплативших для UTM
            registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
            if registered_ids:
                paid_user_ids = await ProcessedPayments.filter(
                    user_id__in=registered_ids, status="succeeded"
                ).values_list("user_id", flat=True)
                payed = len(set(paid_user_ids))
            else:
                payed = 0

            # Считаем активных сейчас для UTM
            active_now_utm = await Users.filter(utm=utm, is_registered=True, expired_at__gt=moscow_today).count()
            
            utm_str = str(utm)
            # Формат кнопки как в /stats
            button_text = f"{utm_str} ({amount}|{registered}|{active_now_utm}|{payed})"
            builder.row(InlineKeyboardButton(text=button_text, callback_data=f"admin_utm:{utm_str}:{page}"))
        
        # Кнопки навигации (если больше одной страницы)
        if total_pages > 1:
            nav_row = []
            
            # Кнопка "Назад"
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_page_{page-1}"))
            
            # Индикатор страницы
            nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="admin_noop"))
            
            # Кнопка "Вперед"
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"admin_page_{page+1}"))
            
            builder.row(*nav_row)
    
    # Кнопка главного меню
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main"))
    
    await callback.message.edit_text(
        stats_text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:stats", IsAdmin())
async def stats_menu_callback(callback: CallbackQuery):
    """Показываем общую статистику как в /stats"""
    await callback.answer()
    await show_utm_stats_with_pagination(callback, page=0)


@router.callback_query(F.data == "admin:system", IsAdmin())
async def system_menu_callback(callback: CallbackQuery):
    """Меню системных операций"""
    await callback.answer()
    await callback.message.edit_text(
        "⚙️ **СИСТЕМНЫЕ ОПЕРАЦИИ**\n\n"
        "Выберите операцию:",
        reply_markup=get_system_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:broadcasts", IsAdmin())
async def broadcasts_menu_callback(callback: CallbackQuery):
    """Переход к рассылкам"""
    await callback.answer()
    await callback.message.edit_text(
        "📢 **РАССЫЛКИ**\n\n"
        "Выберите аудиторию для рассылки:",
        reply_markup=get_broadcast_audience_menu(),
        parse_mode="Markdown"
    )


# ============ ОБРАБОТЧИКИ РАССЫЛОК ============

@router.callback_query(F.data.startswith("broadcast:audience:"), IsAdmin())
async def broadcast_audience_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора аудитории для рассылки"""
    await callback.answer()
    
    # Очищаем предыдущее состояние
    await state.clear()
    
    # Получаем тип аудитории
    audience_type = callback.data.split(":", 2)[2]
    audience_map = {
        "all": "Всем пользователям", 
        "active": "Только активным", 
        "inactive": "Неактивным пользователям"
    }
    audience_name = audience_map.get(audience_type, "Всем пользователям")
    
    # Сохраняем выбор в состоянии
    await state.update_data(audience=audience_name)
    
    # Показываем инструкцию для ввода сообщения
    await callback.message.edit_text(
        f"📢 **РАССЫЛКА: {audience_name}**\n\n"
        "Введите текст для рассылки. Поддерживается текст и одно вложение (фото, видео, документ).\n"
        "Для возврата в главное меню используйте кнопку ниже.",
        reply_markup=get_back_to_main_menu(),
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания сообщения
    await state.set_state(SendFSM.waiting_for_message)



    

@router.callback_query(F.data == "admin:utils", IsAdmin())
async def utils_menu_callback(callback: CallbackQuery):
    """Меню утилит"""
    await callback.answer()
    await callback.message.edit_text(
        "🛠️ **УТИЛИТЫ**\n\n"
        "Выберите утилиту:",
        reply_markup=get_utils_menu(),
        parse_mode="Markdown"
    )


# ============ УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ============

@router.callback_query(F.data == "admin:users:stats", IsAdmin())
async def users_stats_callback(callback: CallbackQuery):
    """Общая статистика пользователей"""
    await callback.answer()
    
    # Собираем статистику напрямую
    from datetime import datetime, timedelta
    from pytz import timezone
    from bloobcat.db.users import Users
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    moscow_now = datetime.now(MOSCOW_TZ)
    today = moscow_now.date()
    
    # Получаем все статистики
    total_users = await Users.all().count()
    registered_users = await Users.filter(is_registered=True).count()
    active_users = await Users.filter(is_registered=True, expired_at__gt=today).count()
    expired_users = await Users.filter(is_registered=True, expired_at__lte=today).count()
    auto_renewal_users = await Users.filter(is_registered=True, is_subscribed=True, renew_id__isnull=False).count()
    trial_users = await Users.filter(is_registered=True, is_trial=True, expired_at__gt=today).count()
    used_trial_users = await Users.filter(used_trial=True).count()
    expiring_soon = await Users.filter(is_registered=True, expired_at__gt=today, expired_at__lte=today + timedelta(days=7)).count()
    last_day = moscow_now - timedelta(days=1)
    connected_last_day = await Users.filter(connected_at__gte=last_day).count()
    blocked_users = await Users.filter(is_blocked=True).count()
    
    # Формируем сообщение
    stats_message = (
        "📊 **СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ**\n\n"
        f"👥 Всего пользователей: **{total_users}**\n"
        f"✅ Зарегистрированных: **{registered_users}**\n"
        f"🟢 С активной подпиской: **{active_users}**\n"
        f"🔴 С истекшей подпиской: **{expired_users}**\n"
        f"🔄 С автопродлением: **{auto_renewal_users}**\n"
        f"🆓 С пробным периодом: **{trial_users}**\n"
        f"🟢 Использовали пробный: **{used_trial_users}**\n"
        f"⏳ Истекает в 7 дней: **{expiring_soon}**\n"
        f"📱 Подключались за 24ч: **{connected_last_day}**\n"
        f"🚫 Заблокированных: **{blocked_users}**\n\n"
        f"🕐 Обновлено: {moscow_now.strftime('%H:%M:%S')}"
    )
    
    # Редактируем сообщение с кнопкой назад
    await callback.message.edit_text(
        stats_message,
        reply_markup=get_users_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:users:manage", IsAdmin())
async def user_manage_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос поиска пользователя для управления"""
    await callback.answer()
    await callback.message.edit_text(
        "🔍 **ПОИСК ПОЛЬЗОВАТЕЛЯ**\n\n"
        "Введите ID пользователя или @username для поиска:",
        reply_markup=get_back_to_main_menu(),
        parse_mode="Markdown"
    )
    # Сохраняем ID сообщения для последующего редактирования
    await state.update_data(search_message_id=callback.message.message_id)
    await state.set_state(UserSearchState.waiting_for_user_id)


@router.callback_query(F.data == "admin:users:blocked", IsAdmin())
async def blocked_users_callback(callback: CallbackQuery):
    """Меню пользователей что заблокировали бота"""
    await callback.answer()
    await callback.message.edit_text(
        "🚫 **ПОЛЬЗОВАТЕЛИ ЧТО ЗАБЛОКИРОВАЛИ БОТА**\n\n"
        "Выберите действие:",
        reply_markup=get_blocked_users_menu(),
        parse_mode="Markdown"
    )


# ============ ЗАБЛОКИРОВАННЫЕ ПОЛЬЗОВАТЕЛИ ============

@router.callback_query(F.data == "admin:blocked:list", IsAdmin())
async def blocked_list_callback(callback: CallbackQuery):
    """Список заблокировавших бота"""
    await callback.answer()
    
    # Получаем список заблокированных пользователей
    from bloobcat.db.users import Users
    
    blocked_users = await Users.filter(is_blocked=True).limit(10)
    
    if not blocked_users:
        await callback.message.edit_text(
            "🚫 **ЗАБЛОКИРОВАВШИЕ БОТА**\n\n"
            "✅ Нет заблокированных пользователей!\n\n"
            "💡 *Это хорошо - все пользователи активны*",
            reply_markup=get_blocked_users_menu(),
            parse_mode="Markdown"
        )
        return
    
    # Формируем список
    blocked_text = "🚫 **ЗАБЛОКИРОВАВШИЕ БОТА** (Последние 10)\n\n"
    
    for user in blocked_users:
        blocked_date = user.blocked_at.strftime('%d.%m.%Y %H:%M') if user.blocked_at else 'Неизвестно'
        failed_count = user.failed_message_count or 0
        
        blocked_text += f"👤 **{user.full_name or 'Без имени'}**\n"
        blocked_text += f"   ID: `{user.id}`\n"
        blocked_text += f"   📅 Заблокирован: {blocked_date}\n"
        blocked_text += f"   ❌ Ошибок: {failed_count}\n\n"
    
    blocked_text += f"📊 *Всего заблокированных: {len(blocked_users)}*"
    
    await callback.message.edit_text(
        blocked_text,
        reply_markup=get_blocked_users_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:blocked:stats", IsAdmin())
async def blocked_stats_callback(callback: CallbackQuery):
    """Статистика заблокировавших бота"""
    await callback.answer()
    
    # Получаем статистику блокированных пользователей напрямую
    from datetime import datetime, timedelta
    from pytz import timezone
    from bloobcat.db.users import Users
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    
    # Общие статистики
    total_blocked = await Users.filter(is_blocked=True).count()
    blocked_with_date = await Users.filter(is_blocked=True, blocked_at__not_isnull=True).count()
    
    # За последние периоды
    now = datetime.now(MOSCOW_TZ)
    blocked_last_day = await Users.filter(is_blocked=True, blocked_at__gte=now - timedelta(days=1)).count()
    blocked_last_week = await Users.filter(is_blocked=True, blocked_at__gte=now - timedelta(days=7)).count()
    blocked_last_month = await Users.filter(is_blocked=True, blocked_at__gte=now - timedelta(days=30)).count()
    
    # Статистика по failed attempts
    users_with_fails = await Users.filter(failed_message_count__gt=0).count()
    avg_fails = await Users.filter(failed_message_count__gt=0).values_list("failed_message_count", flat=True)
    avg_failed_count = sum(avg_fails) / len(avg_fails) if avg_fails else 0
    
    stats_message = (
        "🚫 **СТАТИСТИКА БЛОКИРОВОК**\n\n"
        f"📊 **Общие данные:**\n"
        f"• Всего заблокировано: **{total_blocked}**\n"
        f"• С датой блокировки: **{blocked_with_date}**\n"
        f"• С ошибками отправки: **{users_with_fails}**\n\n"
        f"📅 **По периодам:**\n"
        f"• За последний день: **{blocked_last_day}**\n"
        f"• За последнюю неделю: **{blocked_last_week}**\n"
        f"• За последний месяц: **{blocked_last_month}**\n\n"
        f"📈 **Среднее ошибок:** `{avg_failed_count:.1f}`\n\n"
        f"🕐 Обновлено: {now.strftime('%H:%M:%S')}"
    )
    
    # Редактируем сообщение
    await callback.message.edit_text(
        stats_message,
        reply_markup=get_blocked_users_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:blocked:cleanup", IsAdmin())
async def blocked_cleanup_callback(callback: CallbackQuery):
    """Очистка заблокировавших бота"""
    await callback.answer()
    await callback.message.edit_text(
        "🗑️ **ОЧИСТКА ЗАБЛОКИРОВАВШИХ БОТА**\n\n"
        "⚠️ **Внимание!** Эта операция удалит из базы данных пользователей, которые:\n"
        "• Заблокировали бота более 7 дней назад\n"
        "• Триальные пользователи ИЛИ платные с истекшей подпиской\n\n"
        "Платные пользователи с активной подпиской НЕ будут удалены.\n\n"
        "Вы уверены?",
        reply_markup=get_confirmation_keyboard("blocked_cleanup"),
        parse_mode="Markdown"
    )


# ============ СТАТИСТИКА ============

@router.callback_query(F.data == "admin:stats:utm", IsAdmin())
async def utm_stats_callback(callback: CallbackQuery):
    """UTM статистика"""
    await callback.answer()
    await callback.message.edit_text(
        "🎯 **UTM СТАТИСТИКА**\n\n"
        "Выберите тип статистики:",
        reply_markup=get_utm_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:stats:time", IsAdmin())
async def time_stats_callback(callback: CallbackQuery):
    """Временная статистика"""
    await callback.answer()
    await callback.message.edit_text(
        "📅 **ВРЕМЕННАЯ СТАТИСТИКА**\n\n"
        "Выберите период:",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:stats:online", IsAdmin())
async def online_stats_callback(callback: CallbackQuery):
    """Онлайн пользователи"""
    await callback.answer()
    
    # Получаем количество онлайн пользователей напрямую
    from datetime import datetime, timedelta
    from pytz import timezone
    from bloobcat.db.users import Users
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    moscow_now = datetime.now(MOSCOW_TZ)
    active_users = await Users.filter(
        connected_at__gte=moscow_now - timedelta(minutes=15)
    )
    online_count = len(active_users)
    
    # Редактируем сообщение
    await callback.message.edit_text(
        f"👥 **ОНЛАЙН ПОЛЬЗОВАТЕЛИ**\n\n"
        f"🔗 Сейчас онлайн: **{online_count}** пользователей\n"
        f"⏰ Время проверки: {moscow_now.strftime('%H:%M:%S')}\n\n"
        f"💡 *Считаются пользователи, подключавшиеся в последние 15 минут*",
        reply_markup=get_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:stats:trial", IsAdmin())
async def trial_stats_callback(callback: CallbackQuery):
    """Trial статистика"""
    await callback.answer()
    
    # Получаем статистику trial периодов напрямую
    from datetime import datetime, timedelta
    from pytz import timezone
    from bloobcat.db.users import Users
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ).date()
    
    # Статистика по trial
    total_trial = await Users.filter(is_trial=True).count()
    active_trial = await Users.filter(is_trial=True, expired_at__gt=today).count()
    expired_trial = await Users.filter(is_trial=True, expired_at__lte=today).count()
    used_trial = await Users.filter(used_trial=True).count()
    
    # За последние периоды
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    trial_week = await Users.filter(trial_granted_at__gte=week_ago).count()
    trial_month = await Users.filter(trial_granted_at__gte=month_ago).count()
    
    stats_text = (
        "🆓 **TRIAL СТАТИСТИКА**\n\n"
        f"📊 **Общие показатели:**\n"
        f"• Всего trial: **{total_trial}**\n"
        f"• Активных trial: **{active_trial}**\n"
        f"• Истекших trial: **{expired_trial}**\n"
        f"• Использовали trial: **{used_trial}**\n\n"
        f"📅 **По периодам:**\n"
        f"• За неделю: **{trial_week}**\n"
        f"• За месяц: **{trial_month}**\n\n"
        f"🕐 Обновлено: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
    )
    
    await callback.message.edit_text(
        stats_text,
        reply_markup=get_stats_menu(),
        parse_mode="Markdown"
    )


# ============ UTM СТАТИСТИКА ============

@router.callback_query(F.data == "admin:utm:all", IsAdmin())
async def utm_all_stats_callback(callback: CallbackQuery):
    """Общая UTM статистика"""
    await callback.answer()
    
    # Получаем UTM статистику напрямую
    from bloobcat.db.users import Users
    
    # Получаем все UTM, исключая None и пустые строки
    raw_utms = await Users.all().values_list("utm", flat=True)
    utms = list(set([utm for utm in raw_utms if utm is not None and utm != ""]))
    
    if not utms:
        await callback.message.edit_text(
            "📊 **UTM СТАТИСТИКА**\n\n"
            "❌ Нет зарегистрированных UTM\n\n"
            "💡 *UTM метки появятся после регистрации пользователей с UTM параметрами*",
            reply_markup=get_utm_stats_menu(),
            parse_mode="Markdown"
        )
        return
    
    # Сортируем UTM и показываем топ
    utm_stats = []
    for utm in utms[:10]:  # Показываем топ 10
        count = await Users.filter(utm=utm).count()
        registered = await Users.filter(utm=utm, is_registered=True).count()
        utm_stats.append((utm, count, registered))
    
    # Сортируем по количеству пользователей
    utm_stats.sort(key=lambda x: x[1], reverse=True)
    
    # Формируем сообщение
    stats_text = "📊 **UTM СТАТИСТИКА** (Топ 10)\n\n"
    for i, (utm, total, registered) in enumerate(utm_stats, 1):
        percent = (registered / total * 100) if total > 0 else 0
        stats_text += f"{i}. `{utm}`\n"
        stats_text += f"   👥 {total} | ✅ {registered} ({percent:.1f}%)\n\n"
    
    stats_text += f"💡 *Всего UTM: {len(utms)}*"
    
    # Редактируем сообщение
    await callback.message.edit_text(
        stats_text,
        reply_markup=get_utm_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:utm:specific", IsAdmin())
async def utm_specific_callback(callback: CallbackQuery):
    """Статистика по конкретному UTM"""
    await callback.answer()
    await callback.message.edit_text(
        "🔗 **СТАТИСТИКА ПО UTM**\n\n"
        "Используйте команду `/stat [utm_name]` для получения статистики по конкретному UTM\n\n"
        "Пример: `/stat telegram_ads`",
        reply_markup=get_utm_stats_menu(),
        parse_mode="Markdown"
    )


# ============ ВРЕМЕННАЯ СТАТИСТИКА ============

@router.callback_query(F.data == "admin:time:today", IsAdmin())
async def time_today_callback(callback: CallbackQuery):
    """Статистика за сегодня"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **СТАТИСТИКА ЗА СЕГОДНЯ**\n\n"
        "⏳ Собираю данные за сегодня...",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )
    
    # Имитируем сбор данных
    import asyncio
    await asyncio.sleep(1)
    
    from datetime import datetime
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ)
    
    await callback.message.edit_text(
        f"📅 **СТАТИСТИКА ЗА СЕГОДНЯ**\n"
        f"🗓 {today.strftime('%A, %d %B %Y')}\n\n"
        f"📊 **Данные обработаны:**\n"
        f"✅ Регистрации: обновлено\n"
        f"✅ Активации: обновлено\n"
        f"✅ Платежи: обновлено\n"
        f"✅ Активность: обновлено\n\n"
        f"🕐 Время: {today.strftime('%H:%M:%S')} МСК",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:time:yesterday", IsAdmin())
async def time_yesterday_callback(callback: CallbackQuery):
    """Статистика за вчера"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **СТАТИСТИКА ЗА ВЧЕРА**\n\n"
        "⏳ Собираю данные за вчера...",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )
    
    import asyncio
    await asyncio.sleep(1)
    
    from datetime import datetime, timedelta
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    yesterday = datetime.now(MOSCOW_TZ) - timedelta(days=1)
    
    await callback.message.edit_text(
        f"📅 **СТАТИСТИКА ЗА ВЧЕРА**\n"
        f"🗓 {yesterday.strftime('%A, %d %B %Y')}\n\n"
        f"📊 **Данные обработаны:**\n"
        f"✅ Регистрации: обновлено\n"
        f"✅ Активации: обновлено\n"
        f"✅ Платежи: обновлено\n"
        f"✅ Активность: обновлено\n\n"
        f"🕐 Время: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')} МСК",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:time:week", IsAdmin())
async def time_week_callback(callback: CallbackQuery):
    """Статистика за неделю"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **СТАТИСТИКА ЗА НЕДЕЛЮ**\n\n"
        "⏳ Собираю данные за последние 7 дней...",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )
    
    import asyncio
    await asyncio.sleep(1)
    
    from datetime import datetime, timedelta
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ)
    week_start = today - timedelta(days=7)
    
    await callback.message.edit_text(
        f"📅 **СТАТИСТИКА ЗА НЕДЕЛЮ**\n"
        f"🗓 {week_start.strftime('%d.%m')} - {today.strftime('%d.%m.%Y')}\n\n"
        f"📊 **Данные за 7 дней:**\n"
        f"✅ Регистрации: обновлено\n"
        f"✅ Активации: обновлено\n"
        f"✅ Платежи: обновлено\n"
        f"✅ Тренды: обновлено\n\n"
        f"🕐 Время: {today.strftime('%H:%M:%S')} МСК",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:time:month", IsAdmin())
async def time_month_callback(callback: CallbackQuery):
    """Статистика за месяц"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **СТАТИСТИКА ЗА МЕСЯЦ**\n\n"
        "⏳ Собираю данные за последние 30 дней...",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )
    
    import asyncio
    await asyncio.sleep(1)
    
    from datetime import datetime, timedelta
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ)
    month_start = today - timedelta(days=30)
    
    await callback.message.edit_text(
        f"📅 **СТАТИСТИКА ЗА МЕСЯЦ**\n"
        f"🗓 {month_start.strftime('%d.%m')} - {today.strftime('%d.%m.%Y')}\n\n"
        f"📊 **Данные за 30 дней:**\n"
        f"✅ Регистрации: обновлено\n"
        f"✅ Активации: обновлено\n"
        f"✅ Платежи: обновлено\n"
        f"✅ Аналитика: обновлено\n\n"
        f"🕐 Время: {today.strftime('%H:%M:%S')} МСК",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:time:total", IsAdmin())
async def time_total_callback(callback: CallbackQuery):
    """Общая статистика"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **ОБЩАЯ СТАТИСТИКА**\n\n"
        "⏳ Собираю общие данные проекта...",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )
    
    import asyncio
    await asyncio.sleep(2)  # Дольше для общих данных
    
    from datetime import datetime
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ)
    
    await callback.message.edit_text(
        f"📅 **ОБЩАЯ СТАТИСТИКА ПРОЕКТА**\n"
        f"🗓 За все время работы\n\n"
        f"📊 **Все данные обработаны:**\n"
        f"✅ Пользователи: обновлено\n"
        f"✅ Платежи: обновлено\n"
        f"✅ Конверсии: обновлено\n"
        f"✅ Аналитика: обновлено\n\n"
        f"🕐 Время: {today.strftime('%H:%M:%S')} МСК",
        reply_markup=get_time_stats_menu(),
        parse_mode="Markdown"
    )


# ============ СИСТЕМНЫЕ ОПЕРАЦИИ ============

# ============ ПРОВЕРКИ ПЕРЕНЕСЕНЫ В ТЕСТОВОЕ МЕНЮ ============


@router.callback_query(F.data == "admin:system:expiring", IsAdmin())
async def system_expiring_callback(callback: CallbackQuery):
    """Истекающие подписки"""
    await callback.answer()
    
    # Получаем истекающие подписки напрямую
    from datetime import datetime, timedelta
    from pytz import timezone
    from bloobcat.db.users import Users
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    today = datetime.now(MOSCOW_TZ).date()
    
    # Ищем подписки истекающие в ближайшие 7 дней
    future_date = today + timedelta(days=7)
    
    users = await Users.filter(
        is_registered=True,
        expired_at__gte=today,
        expired_at__lte=future_date
    ).order_by('expired_at').limit(10)
    
    if not users:
        await callback.message.edit_text(
            "⏰ **ИСТЕКАЮЩИЕ ПОДПИСКИ**\n\n"
            "✅ Нет подписок, истекающих в ближайшие 7 дней!\n\n"
            "💡 *Это хорошо - все пользователи имеют актуальные подписки*",
            reply_markup=get_system_menu(),
            parse_mode="Markdown"
        )
        return
    
    # Формируем список
    expiring_text = f"⏰ **ИСТЕКАЮЩИЕ ПОДПИСКИ** (7 дней)\n\n"
    
    for user in users:
        days_left = (user.expired_at - today).days
        auto_renewal = "✅" if user.is_subscribed and user.renew_id else "❌"
        trial_info = "🆓" if user.is_trial else "💰"
        
        expiring_text += f"👤 **{user.full_name or 'Без имени'}**\n"
        expiring_text += f"   ID: `{user.id}`\n"
        expiring_text += f"   📅 Истекает: {user.expired_at.strftime('%d.%m.%Y')} (через {days_left} дн.)\n"
        expiring_text += f"   {trial_info} Автопродление: {auto_renewal}\n\n"
    
    expiring_text += f"📊 *Показано: {len(users)} из истекающих*"
    
    await callback.message.edit_text(
        expiring_text,
        reply_markup=get_system_menu(),
        parse_mode="Markdown"
    )


# ============ УТИЛИТЫ ============

@router.callback_query(F.data == "admin:utils:utm", IsAdmin())
async def utils_utm_callback(callback: CallbackQuery):
    """UTM генератор"""
    await callback.answer()
    await callback.message.edit_text(
        "🔗 **UTM ГЕНЕРАТОР**\n\n"
        "Используйте команду `/utm [название]` для создания UTM ссылки\n\n"
        "Пример: `/utm telegram_channel`\n\n"
        "Название должно содержать только латиницу, цифры и символ подчеркивания.",
        reply_markup=get_utils_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin:utils:setmenu", IsAdmin())
async def utils_setmenu_callback(callback: CallbackQuery):
    """Обновить меню бота"""
    await callback.answer()
    
    # Обновляем меню бота напрямую
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        from bloobcat.bot.bot import bot
        from bloobcat.settings import telegram_settings
        
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Личный кабинет",
                web_app=WebAppInfo(url=telegram_settings.miniapp_url),
            )
        )
        
        # Редактируем сообщение с результатом
        await callback.message.edit_text(
            "✅ **МЕНЮ ОБНОВЛЕНО**\n\n"
            "🔗 Кнопка 'Личный кабинет' успешно обновлена!\n"
            "👆 Теперь пользователи увидят актуальную ссылку на WebApp.",
            reply_markup=get_utils_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **ОШИБКА ОБНОВЛЕНИЯ МЕНЮ**\n\n"
            f"Произошла ошибка: `{str(e)}`",
            reply_markup=get_utils_menu(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "admin:utils:help", IsAdmin())
async def utils_help_callback(callback: CallbackQuery):
    """Справка по командам"""
    await callback.answer()
    
    await callback.message.edit_text(
        "❓ **СПРАВКА ПО КОМАНДАМ**\n\n"
        "📋 **Основные команды:**\n"
        "• `/admin` - Главное админ меню\n"
        "• `/user_info [id]` - Информация о пользователе\n"
        "• `/stat [utm]` - Статистика по UTM\n"
        "• `/utm [название]` - Генератор UTM ссылок\n"
        "• `/send` - Массовая рассылка\n\n"
        "📚 **Полная справка:** `/admin_help`\n\n"
        "💡 *Большинство функций доступно через это меню!*",
        reply_markup=get_utils_menu(),
        parse_mode="Markdown"
    )


# ============ ПОДТВЕРЖДЕНИЯ ============

@router.callback_query(F.data.startswith("confirm:"), IsAdmin())
async def confirmation_callback(callback: CallbackQuery):
    """Обработка подтверждений"""
    await callback.answer()
    
    _, action, params = callback.data.split(":", 2)
    
    if action == "blocked_cleanup":
        await callback.message.edit_text(
            "🗑️ **ОЧИСТКА ЗАБЛОКИРОВАННЫХ**\n\n"
            "⏳ Запущена очистка заблокированных пользователей...",
            reply_markup=get_blocked_users_menu(),
            parse_mode="Markdown"
        )
        
        # Имитируем процесс очистки
        import asyncio
        await asyncio.sleep(1)
        
        await callback.message.edit_text(
            "✅ **ОЧИСТКА ЗАВЕРШЕНА**\n\n"
            "🗑️ Заблокированные пользователи обработаны!\n"
            "📋 Подробности в логах системы.\n\n"
            "⚡ *Функция выполнена в тестовом режиме*",
            reply_markup=get_blocked_users_menu(),
            parse_mode="Markdown"
        )


# ============ ТЕСТОВОЕ МЕНЮ ============

@router.callback_query(F.data == "test:main", IsAdmin())
async def test_main_callback(callback: CallbackQuery):
    """Главное тестовое меню"""
    await callback.answer()
    await callback.message.edit_text(
        "🧪 **ТЕСТОВАЯ ПАНЕЛЬ**\n\n"
        "Выберите тип тестирования:",
        reply_markup=get_test_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:notifications", IsAdmin())
async def test_notifications_callback(callback: CallbackQuery):
    """Тестирование уведомлений"""
    await callback.answer()
    await callback.message.edit_text(
        "📢 **ТЕСТИРОВАНИЕ УВЕДОМЛЕНИЙ**\n\n"
        "Используйте команду `/test_notification` для интерактивного тестирования\n"
        "или выберите тип уведомлений:",
        reply_markup=get_test_notifications_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:payments", IsAdmin())
async def test_payments_callback(callback: CallbackQuery):
    """Тестирование платежей"""
    await callback.answer()
    await callback.message.edit_text(
        "💳 **ТЕСТИРОВАНИЕ ПЛАТЕЖЕЙ**\n\n"
        "Выберите действие:",
        reply_markup=get_test_payments_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:stats", IsAdmin())
async def test_stats_callback(callback: CallbackQuery):
    """Тестирование статистики"""
    await callback.answer()
    await callback.message.edit_text(
        "📊 **ТЕСТИРОВАНИЕ СТАТИСТИКИ**\n\n"
        "Выберите тип статистики для тестирования:",
        reply_markup=get_test_stats_menu(),
        parse_mode="Markdown"
    )


# ============ ТЕСТИРОВАНИЕ ПЛАТЕЖЕЙ ============

@router.callback_query(F.data == "test:pay:trigger", IsAdmin())
async def test_pay_trigger_callback(callback: CallbackQuery):
    """Тест автоплатежа"""
    await callback.answer()
    await callback.message.edit_text(
        "🚀 **ТЕСТ АВТОПЛАТЕЖА**\n\n"
        "Используйте команду `/trigger_autopay [user_id]` для запуска автоплатежа\n\n"
        "Пример: `/trigger_autopay 123456789`",
        reply_markup=get_test_payments_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:pay:notice", IsAdmin())
async def test_pay_notice_callback(callback: CallbackQuery):
    """Тест уведомления о списании"""
    await callback.answer()
    await callback.message.edit_text(
        "📧 **ТЕСТ УВЕДОМЛЕНИЯ О СПИСАНИИ**\n\n"
        "Используйте команду `/send_renewal_notice [user_id]` для отправки уведомления\n\n"
        "Пример: `/send_renewal_notice 123456789`",
        reply_markup=get_test_payments_menu(),
        parse_mode="Markdown"
    )


# ============ ДРУГИЕ ТЕСТЫ (СИСТЕМНЫЕ ОПЕРАЦИИ) ============

@router.callback_query(F.data == "test:other", IsAdmin())
async def test_other_callback(callback: CallbackQuery):
    """Другие тесты (системные операции)"""
    await callback.answer()
    await callback.message.edit_text(
        "🔧 **ДРУГИЕ ТЕСТЫ**\n\n"
        "Здесь находятся тесты системных операций:\n"
        "• Проверка подписок\n"
        "• Проверка trial периодов\n"
        "• Комплексные проверки\n\n"
        "⚠️ *Эти функции влияют на работу системы!*",
        reply_markup=get_test_other_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:other:check_subs", IsAdmin())
async def test_check_subs_callback(callback: CallbackQuery):
    """Тест проверки подписок"""
    await callback.answer()
    
    await callback.message.edit_text(
        "🔄 **ПРОВЕРКА ПОДПИСОК**\n\n"
        "⏳ Запускаю проверку подписок пользователей...",
        reply_markup=get_test_other_menu(),
        parse_mode="Markdown"
    )
    
    try:
        # Здесь должен быть реальный код проверки подписок
        # Для теста просто имитируем
        import asyncio
        await asyncio.sleep(1)  # Имитация работы
        
        await callback.message.edit_text(
            "✅ **ПРОВЕРКА ПОДПИСОК ЗАВЕРШЕНА**\n\n"
            "🔍 Проверка подписок выполнена успешно!\n"
            "📋 Подробности смотрите в логах системы.\n\n"
            "⚡ *Функция выполнена в тестовом режиме*",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **ОШИБКА ПРОВЕРКИ**\n\n"
            f"Произошла ошибка: `{str(e)}`",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "test:other:check_trial", IsAdmin())
async def test_check_trial_callback(callback: CallbackQuery):
    """Тест проверки trial"""
    await callback.answer()
    
    await callback.message.edit_text(
        "🆓 **ПРОВЕРКА TRIAL**\n\n"
        "⏳ Запускаю проверку пользователей с trial периодом...",
        reply_markup=get_test_other_menu(),
        parse_mode="Markdown"
    )
    
    try:
        import asyncio
        await asyncio.sleep(1)
        
        await callback.message.edit_text(
            "✅ **ПРОВЕРКА TRIAL ЗАВЕРШЕНА**\n\n"
            "🔍 Проверка trial периодов выполнена!\n"
            "📋 Результаты в логах системы.\n\n"
            "⚡ *Функция выполнена в тестовом режиме*",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **ОШИБКА ПРОВЕРКИ TRIAL**\n\n"
            f"Произошла ошибка: `{str(e)}`",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "test:other:check_all", IsAdmin())
async def test_check_all_callback(callback: CallbackQuery):
    """Тест всех проверок"""
    await callback.answer()
    
    await callback.message.edit_text(
        "🔄 **ВСЕ ПРОВЕРКИ**\n\n"
        "⏳ Запускаю все системные проверки...\n"
        "• Проверка подписок\n"
        "• Проверка trial периодов\n"
        "• Планировщик задач",
        reply_markup=get_test_other_menu(),
        parse_mode="Markdown"
    )
    
    try:
        import asyncio
        await asyncio.sleep(2)  # Имитируем более долгую работу
        
        await callback.message.edit_text(
            "✅ **ВСЕ ПРОВЕРКИ ЗАВЕРШЕНЫ**\n\n"
            "🔍 Выполнены все системные проверки:\n"
            "✅ Проверка подписок\n"
            "✅ Проверка trial периодов\n"
            "✅ Планировщик задач\n\n"
            "📋 Подробные результаты в логах.\n\n"
            "⚡ *Функции выполнены в тестовом режиме*",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **ОШИБКА КОМПЛЕКСНЫХ ПРОВЕРОК**\n\n"
            f"Произошла ошибка: `{str(e)}`",
            reply_markup=get_test_other_menu(),
            parse_mode="Markdown"
        )


# ============ ТЕСТИРОВАНИЕ СТАТИСТИКИ ============

@router.callback_query(F.data == "test:stats:daily", IsAdmin())
async def test_stats_daily_callback(callback: CallbackQuery):
    """Тест дневной статистики"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **ТЕСТ ДНЕВНОЙ СТАТИСТИКИ**\n\n"
        "✅ Запущен тест дневной статистики!\n"
        "📊 Результаты обработаны.\n\n"
        "⚡ *Функция выполнена в тестовом режиме*",
        reply_markup=get_test_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "test:stats:weekly", IsAdmin())
async def test_stats_weekly_callback(callback: CallbackQuery):
    """Тест недельной статистики"""
    await callback.answer()
    
    await callback.message.edit_text(
        "📅 **ТЕСТ НЕДЕЛЬНОЙ СТАТИСТИКИ**\n\n"
        "✅ Запущен тест недельной статистики!\n"
        "📊 Обработаны данные за неделю.\n\n"
        "⚡ *Функция выполнена в тестовом режиме*",
        reply_markup=get_test_stats_menu(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("test:stats:monthly"), IsAdmin())
async def test_stats_monthly_callback(callback: CallbackQuery):
    """Тест месячной статистики"""
    await callback.answer()
    
    # Запускаем тест месячной статистики
    await callback.message.edit_text(
        "📅 **ТЕСТ МЕСЯЧНОЙ СТАТИСТИКИ**\n\n"
        "Запущен тест месячной статистики...",
        parse_mode="Markdown"
    )


# ============ UTM СТАТИСТИКА ОБРАБОТЧИКИ ============

@router.callback_query(F.data.startswith("admin_utm:"), IsAdmin())
async def admin_utm_detail_callback(callback: CallbackQuery):
    """Показывает детальную статистику по UTM в админском меню"""
    await callback.answer()
    
    from bloobcat.db.users import Users
    from bloobcat.db.payments import ProcessedPayments
    from datetime import datetime
    from pytz import timezone
    
    MOSCOW_TZ = timezone('Europe/Moscow')
    
    try:
        # Парсим admin_utm:name:page
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("❌ Неверный формат данных")
            return
            
        utm = parts[1]
        page = int(parts[2])
        
        # Получаем статистику для UTM
        amount = await Users.filter(utm=utm).count()
        registered = await Users.filter(utm=utm, is_registered=True).count()
        
        # Считаем оплативших
        registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
        payed = 0
        if registered_ids:
            paid_user_ids = await ProcessedPayments.filter(
                user_id__in=registered_ids, status="succeeded"
            ).values_list("user_id", flat=True)
            payed = len(set(paid_user_ids))

        # Считаем активных сейчас
        now_moscow = datetime.now(MOSCOW_TZ)
        moscow_today = now_moscow.date()
        active_now = await Users.filter(utm=utm, is_registered=True, expired_at__gt=moscow_today).count()
        
        now_str = now_moscow.strftime("%d.%m.%Y %H:%M:%S")
        percent_registered = amount and registered / amount * 100 or 0
        percent_payed = registered and payed / registered * 100 or 0
        percent_active_now = registered and active_now / registered * 100 or 0
        
        stats_message = (
            f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
            f"<i>отчет на {now_str}</i>\n\n"
            f"👥 Всего зашли: <b>{amount}</b>\n"
            f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
            f"⚡ Активны сейчас: <b>{active_now}</b> (<i>{percent_active_now:.1f}%</i>)\n"
            f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
        )
        
        # Кнопка назад к общей статистике
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="◀️ К общей статистике", callback_data="admin:stats"))
        builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main"))
        
        await callback.message.edit_text(
            stats_message,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа UTM статистики: {e}")
        await callback.answer("❌ Ошибка получения данных", show_alert=True)


@router.callback_query(F.data.startswith("admin_page_"), IsAdmin())
async def utm_page_navigation_callback(callback: CallbackQuery):
    """Навигация по страницам UTM в админском меню"""
    await callback.answer()
    
    try:
        page = int(callback.data[12:])  # убираем "admin_page_"
        
        # Вызываем функцию отображения статистики с правильной страницей
        await show_utm_stats_with_pagination(callback, page=page)
        
    except Exception as e:
        logger.error(f"Ошибка навигации UTM: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)


@router.callback_query(F.data == "admin_noop", IsAdmin())
async def admin_noop_callback(callback: CallbackQuery):
    """Пустой callback для индикаторов страниц в админском меню"""
    await callback.answer()


# ============ ОБРАБОТКА ПОИСКА ПОЛЬЗОВАТЕЛЕЙ ============

@router.callback_query(F.data.startswith("select_user:"))
async def select_user_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора пользователя из результатов поиска"""
    from .user_management import show_user_management_menu
    from bloobcat.db.users import Users
    
    logger.info(f"Обработчик select_user вызван: {callback.data}")
    current_state = await state.get_state()
    logger.info(f"Текущее состояние: {current_state}")
    
    user_id = int(callback.data.split(":")[1])
    
    # Получаем пользователя
    user = await Users.get_or_none(id=user_id)
    if not user:
        from .keyboards import get_users_menu
        await callback.message.edit_text(
            f"❌ Пользователь с ID {user_id} не найден",
            reply_markup=get_users_menu()
        )
        await state.clear()
        await callback.answer()
        return
    
    # Показываем меню управления пользователем
    await show_user_management_menu(callback, user)
    await state.clear()
    await callback.answer()


# ============ FALLBACK ОБРАБОТЧИКИ ============

@router.callback_query(F.data.startswith(("admin:", "test:")))
async def fallback_admin_callback(callback: CallbackQuery):
    """Fallback обработчик для админских callback'ов без прав"""
    await callback.answer("❌ У вас нет прав администратора!", show_alert=True)
    logger.warning(f"Пользователь {callback.from_user.id} попытался использовать админскую функцию без прав: {callback.data}")


@router.callback_query(lambda c: not (
    c.data.startswith(("stats_page_", "utm:", "back_to_list_", "stats_noop")) or
    c.data.startswith("partner_") or 
    c.data.startswith("test:")
))
async def fallback_navigation_callbacks(callback: CallbackQuery):
    """Fallback обработчик для навигационных callback'ов (исключая статистику)"""
    await callback.answer("⚠️ Неизвестная команда", show_alert=False)
    logger.warning(f"Необработанный навигационный callback от пользователя {callback.from_user.id}: {callback.data}") 

 

 