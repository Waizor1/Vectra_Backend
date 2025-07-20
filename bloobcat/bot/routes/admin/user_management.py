from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bloobcat.bot.routes.admin.functions import IsAdmin, search_user
from bloobcat.bot.routes.admin.navigation import UserSearchState, create_fake_message
from bloobcat.logger import get_logger
from .keyboards import get_user_management_menu, get_users_menu, get_main_admin_menu, get_confirmation_keyboard
from bloobcat.db.users import Users
from bloobcat.settings import app_settings

logger = get_logger("bot_admin_user_management")
router = Router()


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
    
    # Поиск пользователя
    user = await search_user(user_input)
    if not user:
        await message.answer(
            f"❌ **Пользователь не найден!**\n\n"
            f"Поиск по: `{user_input}`\n\n"
            f"Попробуйте еще раз или отмените поиск командой /cancel",
            parse_mode="Markdown"
        )
        return
    
    # Показываем меню управления пользователем
    await show_user_management_menu(message, user)
    await state.clear()


async def show_user_management_menu(message_or_callback, user):
    """Показывает меню управления конкретным пользователем"""
    # Получаем детальную информацию о пользователе
    user_info = await get_user_detailed_info(user)
    
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


async def get_user_detailed_info(user):
    """Получает детальную информацию о пользователе"""
    from datetime import datetime, date
    from pytz import timezone
    
    moscow_tz = timezone('Europe/Moscow')
    moscow_now = datetime.now(moscow_tz)
    today = moscow_now.date()
    
    # Статус регистрации
    reg_status = "✅ Зарегистрирован" if user.is_registered else "❌ Не зарегистрирован"
    
    # Статус подписки
    if not user.is_registered:
        sub_status = "❌ Не активирован"
    elif user.expired_at and user.expired_at > today:
        days_left = (user.expired_at - today).days
        trial_info = " (trial)" if user.is_trial else ""
        sub_status = f"✅ Активна{trial_info} (осталось {days_left} дн.)"
    else:
        sub_status = "🔴 Истекла"
    
    # Автопродление
    auto_renewal = "✅ Включено" if user.is_subscribed and user.renew_id else "❌ Отключено"
    
    # Trial статус
    trial_status = "✅ Использован" if user.used_trial else "❌ Не использован"
    
    # Статус блокировки бота
    block_status = "🚫 Заблокировал бота" if user.is_blocked else "✅ Активен"
    
    # Даты
    created_at = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "Нет данных"
    connected_at = user.connected_at.strftime("%d.%m.%Y %H:%M") if user.connected_at else "Никогда"
    expired_at = user.expired_at.strftime("%d.%m.%Y") if user.expired_at else "Нет данных"
    
    info = (
        f"**Статусы:**\n"
        f"• Регистрация: {reg_status}\n"
        f"• Подписка: {sub_status}\n"
        f"• Автопродление: {auto_renewal}\n"
        f"• Trial период: {trial_status}\n"
        f"• Статус бота: {block_status}\n\n"
        f"**Даты:**\n"
        f"• Создан: {created_at}\n"
        f"• Последнее подключение: {connected_at}\n"
        f"• Истекает: {expired_at}"
    )
    
    if user.renew_id:
        info += f"\n\n**Платежи:**\n• ID платежа: `{user.renew_id}`"
    
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
    days_left = (user.expired_at - today).days if user.expired_at else 0
    
    text = (
        f"💰 **УПРАВЛЕНИЕ ПОДПИСКОЙ**\n\n"
        f"👤 Пользователь: {user.full_name} (`{user.id}`)\n\n"
        f"**Текущий статус:**\n"
        f"• Зарегистрирован: {'✅' if user.is_registered else '❌'}\n"
        f"• Дата истечения: {user.expired_at.strftime('%d.%m.%Y') if user.expired_at else 'Нет'}\n"
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


async def show_block_management(callback, user):
    """Управление статусом блокировки бота"""
    text = (
        f"🚫 **УПРАВЛЕНИЕ СТАТУСОМ БЛОКИРОВКИ**\n\n"
        f"👤 Пользователь: {user.full_name} (`{user.id}`)\n\n"
        f"**Текущий статус:**\n"
        f"• Заблокировал бота: {'✅ Да' if user.is_blocked else '❌ Нет'}\n"
        f"• Дата блокировки: {user.blocked_at.strftime('%d.%m.%Y %H:%M') if user.blocked_at else 'Нет'}\n"
        f"• Неудачных попыток: {user.failed_message_count}\n\n"
        f"**Доступные команды:**\n"
        f"• `/unblock_user {user.id}` - Убрать флаг блокировки\n"
        f"• `/block_user {user.id} причина` - Пометить как заблокировавшего\n\n"
        f"**Примечание:** Это управление флагом в БД, а не реальной блокировкой пользователя."
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_management_menu(user.id, user.full_name),
        parse_mode="Markdown"
    )


async def show_delete_confirmation(callback, user):
    """Подтверждение удаления пользователя"""
    text = (
        f"🗑️ **УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ**\n\n"
        f"⚠️ **ВНИМАНИЕ!** Вы собираетесь полностью удалить пользователя:\n\n"
        f"👤 **{user.full_name}** (`{user.id}`)\n"
        f"• Username: {f'@{user.username}' if user.username else 'Нет'}\n"
        f"• Зарегистрирован: {'✅' if user.is_registered else '❌'}\n"
        f"• Trial использован: {'✅' if user.used_trial else '❌'}\n\n"
        f"**Это действие:**\n"
        f"• Удалит пользователя из базы данных\n"
        f"• Удалит все его платежи\n"
        f"• Позволит ему снова получить trial период\n"
        f"• **НЕОБРАТИМО!**\n\n"
        f"Вы уверены?"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_confirmation_keyboard("delete_user", str(user.id)),
        parse_mode="Markdown"
    )


# ============ ПОДТВЕРЖДЕНИЯ ДЕЙСТВИЙ ============

@router.callback_query(F.data.startswith("confirm:delete_user:"), IsAdmin())
async def confirm_delete_user(callback: CallbackQuery):
    """Подтверждение удаления пользователя"""
    await callback.answer()
    
    user_id = int(callback.data.split(":")[-1])
    
    # Используем существующую функцию удаления
    from .check_subs import admin_delete_user
    from aiogram.filters import CommandObject
    
    # Создаем фейковый CommandObject
    class FakeCommand:
        def __init__(self, args):
            self.args = args
    
    fake_message = create_fake_message(callback)
    fake_command = FakeCommand(str(user_id))
    
    await admin_delete_user(fake_message, fake_command) 