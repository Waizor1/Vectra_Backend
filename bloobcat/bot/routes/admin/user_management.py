from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bloobcat.bot.routes.admin.functions import IsAdmin, search_user, search_users
from bloobcat.bot.routes.admin.navigation import UserSearchState, create_fake_message
from bloobcat.logger import get_logger
from .keyboards import get_user_management_menu, get_users_menu, get_main_admin_menu, get_confirmation_keyboard
from bloobcat.db.users import Users, normalize_date
from bloobcat.settings import app_settings
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

logger = get_logger("bot_admin_user_management")
router = Router()

# Настройки пагинации
USERS_PER_PAGE = 5


# ============ ПОИСК ПОЛЬЗОВАТЕЛЯ ============

@router.message(UserSearchState.waiting_for_user_id, IsAdmin())
async def process_user_search(message: Message, state: FSMContext):
    """Обработка поиска пользователя по ID или username"""
    user_input = message.text.strip()
    
    if user_input in ["/cancel", "❌ Отменить"]:
        await message.answer(
            "❌ Поиск пользователя отменен",
            reply_markup=get_users_menu()
        )
        await state.clear()
        return
    
    # Получаем исходное сообщение для редактирования
    state_data = await state.get_data()
    original_message_id = state_data.get("search_message_id")
    
    # Поиск пользователей
    users = await search_users(user_input)
    
    if not users:
        # Создаем клавиатуру с кнопкой "Главное меню"
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Главное меню", callback_data="admin:main")
        
        text = (
            f"❌ **Пользователь не найден!**\n\n"
            f"Поиск по: `{user_input}`\n\n"
            f"Попробуйте другой запрос или вернитесь в главное меню"
        )
        
        # Пытаемся отредактировать исходное сообщение
        if original_message_id:
            try:
                await message.bot.edit_message_text(
                    text=text,
                    chat_id=message.chat.id,
                    message_id=original_message_id,
                    reply_markup=kb.as_markup(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                # Если не удалось отредактировать - отправляем новое
                logger.warning(f"Не удалось отредактировать сообщение {original_message_id}: {e}")
                await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        
        # Удаляем сообщение пользователя
        try:
            await message.delete()
        except:
            pass
        return
    
    # Если найден только один пользователь - сразу показываем его
    if len(users) == 1:
        await show_user_management_menu(message, users[0])
        await state.clear()
        # Удаляем сообщение пользователя
        try:
            await message.delete()
        except:
            pass
        return
    
    # Если найдено несколько - показываем список с пагинацией
    await state.update_data(found_users=users, search_query=user_input)
    await state.set_state(UserSearchState.choosing_user)
    
    # Отправляем новое сообщение со списком (редактирование не работает стабильно)
    await show_search_results_page(message, state, page=1)
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass


async def show_search_results_page(message_or_callback, state: FSMContext, page: int):
    """Показывает страницу с результатами поиска"""
    state_data = await state.get_data()
    found_users = state_data.get("found_users", [])
    search_query = state_data.get("search_query", "")
    
    logger.info(f"show_search_results_page: найдено {len(found_users)} пользователей, запрос: {search_query}")
    
    total_users = len(found_users)
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    
    # Корректируем номер страницы
    page = max(1, min(page, total_pages))
    
    start_index = (page - 1) * USERS_PER_PAGE
    end_index = start_index + USERS_PER_PAGE
    users_on_page = found_users[start_index:end_index]
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопки пользователей
    for user in users_on_page:
        user_display = f"{user.full_name or 'Без имени'}"
        if user.username:
            user_display += f" (@{user.username})"
        kb.button(
            text=f"{user_display} - ID: {user.id}",
            callback_data=f"select_user:{user.id}"
        )
    
    # Кнопки навигации
    nav_buttons = []
    if total_pages > 1:
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(text="<<", callback_data=f"search_page:1"))
            nav_buttons.append(InlineKeyboardButton(text="<", callback_data=f"search_page:{page - 1}"))
        else:
            nav_buttons.extend([InlineKeyboardButton(text=" ", callback_data="ignore")] * 2)
        
        nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="ignore"))
        
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(text=">", callback_data=f"search_page:{page + 1}"))
            nav_buttons.append(InlineKeyboardButton(text=">>", callback_data=f"search_page:{total_pages}"))
        else:
            nav_buttons.extend([InlineKeyboardButton(text=" ", callback_data="ignore")] * 2)
    
    # Кнопка отмены
    kb.button(text="🏠 Главное меню", callback_data="admin:main")
    
    # Компонуем клавиатуру
    layout = []
    layout.extend([1] * len(users_on_page))  # Каждый пользователь в отдельной строке
    if nav_buttons:
        kb.row(*nav_buttons)  # Навигация в одной строке
    layout.append(1)  # Кнопка отмены в отдельной строке
    
    kb.adjust(*layout)
    
    text = (
        f"🔍 **Найдено пользователей: {total_users}**\n"
        f"Запрос: `{search_query}`\n\n"
        f"Страница {page}/{total_pages}. Выберите пользователя:"
    )
    
    # Всегда отправляем новое сообщение (для стабильности)
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(
            text, 
            reply_markup=kb.as_markup(), 
            parse_mode="Markdown"
        )
    else:
        await message_or_callback.answer(
            text, 
            reply_markup=kb.as_markup(), 
            parse_mode="Markdown"
        )


# Обработчик пагинации результатов поиска
@router.callback_query(UserSearchState.choosing_user, F.data.startswith("search_page:"))
async def search_page_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик переключения страниц результатов поиска"""
    page = int(callback.data.split(":")[1])
    await show_search_results_page(callback, state, page)
    await callback.answer()


# Обработчик отмены поиска
@router.callback_query(UserSearchState.choosing_user, F.data == "admin:main")
async def search_main_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик возврата в главное меню из поиска"""
    await callback.message.edit_text(
        "🔧 **АДМИН ПАНЕЛЬ**\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_main_admin_menu(),
        parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()


# Обработчик возврата в главное меню из состояния ожидания ввода
@router.callback_query(UserSearchState.waiting_for_user_id, F.data == "admin:main")
async def search_waiting_main_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик возврата в главное меню из состояния ожидания ввода"""
    await callback.message.edit_text(
        "🔧 **АДМИН ПАНЕЛЬ**\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_main_admin_menu(),
        parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()


async def show_user_management_menu(message_or_callback, user):
    """Показывает меню управления конкретным пользователем"""
    # Получаем краткую информацию о пользователе
    user_info = await get_user_brief_info(user)
    
    text = (
        f"👤 **УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЕМ**\n\n"
        f"**Основная информация:**\n"
        f"• ID: `{user.id}`\n"
        f"• Имя: {user.full_name}\n"
        f"• Username: {f'@{user.username}' if user.username else 'Нет'}\n\n"
        f"{user_info}"
    )
    
    keyboard = get_user_management_menu(user.id, user.full_name)
    
    if hasattr(message_or_callback, 'edit_text'):
        # Это callback query
        await message_or_callback.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        # Это message
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="Markdown")


async def get_user_brief_info(user):
    """Получает краткую информацию о пользователе"""
    from datetime import datetime, date
    from pytz import timezone
    
    moscow_tz = timezone('Europe/Moscow')
    moscow_now = datetime.now(moscow_tz)
    today = moscow_now.date()
    
    # Статус регистрации
    reg_status = "✅ Зарегистрирован" if user.is_registered else "❌ Не зарегистрирован"
    
    # Статус подписки
    user_expired_at = normalize_date(user.expired_at)
    if not user.is_registered:
        sub_status = "❌ Не активирован"
    elif user_expired_at and user_expired_at > today:
        days_left = (user_expired_at - today).days
        trial_info = " (trial)" if user.is_trial else ""
        sub_status = f"✅ Активна{trial_info} (осталось {days_left} дн.)"
    else:
        sub_status = "🔴 Истекла"
    
    # Статус автопродления
    auto_renewal = "✅ Включено" if user.is_subscribed and user.renew_id else "❌ Отключено"
    
    # Баланс
    balance_info = f"💰 Баланс: {user.balance}₽"
    
    # Рефералы
    referrals_info = f"👥 Рефералов: {user.referrals}"
    
    info = (
        f"📊 **Статус:**\n"
        f"• Регистрация: {reg_status}\n"
        f"• Подписка: {sub_status}\n"
        f"• Автопродление: {auto_renewal}\n\n"
        f"💼 **Финансы:**\n"
        f"• {balance_info}\n"
        f"• {referrals_info}\n"
    )
    
    return info


# ============ УПРАВЛЕНИЕ КОНКРЕТНЫМ ПОЛЬЗОВАТЕЛЕМ ============

@router.callback_query(F.data.startswith("admin:user:"), IsAdmin())
async def user_action_callback(callback: CallbackQuery):
    """Обработка действий с конкретным пользователем"""
    await callback.answer()
    
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.edit_text(
            "❌ Неверный формат callback данных",
            reply_markup=get_main_admin_menu()
        )
        return
    
    user_id = int(parts[2])
    action = parts[3]
    
    # Получаем пользователя
    user = await Users.get_or_none(id=user_id)
    if not user:
        await callback.message.edit_text(
            f"❌ Пользователь с ID {user_id} не найден",
            reply_markup=get_users_menu()
        )
        return
    
    if action == "info":
        await show_user_info_detail(callback, user)
    elif action == "subscription":
        await show_subscription_management(callback, user)
    elif action == "renewal":
        await show_renewal_management(callback, user)
    elif action == "trial":
        await show_trial_management(callback, user)
    elif action == "block":
        await show_block_management(callback, user)
    elif action == "delete":
        await show_delete_confirmation(callback, user)


async def show_user_info_detail(callback, user):
    """Показывает детальную информацию о пользователе"""
    from .check_subs import admin_user_info
    
    # Используем существующую функцию
    fake_message = create_fake_message(callback, f"/user_info {user.id}")
    await admin_user_info(fake_message)


async def show_subscription_management(callback, user):
    """Управление подпиской пользователя"""
    from datetime import date

    today = date.today()
    user_expired_at = normalize_date(user.expired_at)
    days_left = (user_expired_at - today).days if user_expired_at else 0

    text = (
        f"💰 **УПРАВЛЕНИЕ ПОДПИСКОЙ**\n\n"
        f"👤 Пользователь: {user.full_name} (`{user.id}`)\n\n"
        f"**Текущий статус:**\n"
        f"• Зарегистрирован: {'✅' if user.is_registered else '❌'}\n"
        f"• Дата истечения: {user_expired_at.strftime('%d.%m.%Y') if user_expired_at else 'Нет'}\n"
        f"• Дней осталось: {days_left if days_left > 0 else 0}\n"
        f"• Тип: {'🆓 Trial' if user.is_trial else '💰 Платная'}\n\n"
        f"**Доступные команды:**\n"
        f"• `/set_registered {user.id} 1` - Активировать\n"
        f"• `/set_registered {user.id} 0` - Деактивировать\n"
        f"• `/days {user.id} +30` - Добавить 30 дней\n"
        f"• `/balance {user.id} +500` - Добавить 500₽ на баланс"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_management_menu(user.id, user.full_name),
        parse_mode="Markdown"
    )


async def show_renewal_management(callback, user):
    """Управление автопродлением"""
    text = (
        f"🔄 **УПРАВЛЕНИЕ АВТОПРОДЛЕНИЕМ**\n\n"
        f"👤 Пользователь: {user.full_name} (`{user.id}`)\n\n"
        f"**Текущий статус:**\n"
        f"• Автопродление: {'✅ Включено' if user.is_subscribed else '❌ Отключено'}\n"
        f"• ID платежа: {f'`{user.renew_id}`' if user.renew_id else 'Нет'}\n\n"
        f"**Доступные команды:**\n"
        f"• `/set_auto_renewal {user.id} 1 payment_id` - Включить\n"
        f"• `/set_auto_renewal {user.id} 0` - Отключить\n\n"
        f"**Тестирование:**\n"
        f"• `/trigger_autopay {user.id}` - Запустить автоплатеж\n"
        f"• `/send_renewal_notice {user.id}` - Отправить уведомление"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_management_menu(user.id, user.full_name),
        parse_mode="Markdown"
    )


async def show_trial_management(callback, user):
    """Управление trial периодом"""
    text = (
        f"🆓 **УПРАВЛЕНИЕ TRIAL ПЕРИОДОМ**\n\n"
        f"👤 Пользователь: {user.full_name} (`{user.id}`)\n\n"
        f"**Текущий статус:**\n"
        f"• Trial период: {'✅ Активен' if user.is_trial else '❌ Неактивен'}\n"
        f"• Trial использован: {'✅ Да' if user.used_trial else '❌ Нет'}\n\n"
        f"**Доступные команды:**\n"
        f"• `/set_trial {user.id}` - Предоставить trial ({app_settings.trial_days} дн.)\n"
        f"• `/set_trial {user.id} 14` - Предоставить trial на 14 дней\n"
        f"• `/set_trial {user.id} 7 force` - Принудительно (даже если использован)\n"
        f"• `/reset_trial {user.id}` - Сбросить флаг использования"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_management_menu(user.id, user.full_name),
        parse_mode="Markdown"
    )