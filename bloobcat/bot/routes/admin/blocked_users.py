from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from datetime import datetime, timedelta

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.scheduler import cleanup_blocked_users
from bloobcat.settings import app_settings

logger = get_logger("admin_blocked_users")
router = Router()


@router.message(Command("blocked_users"), IsAdmin())
async def list_blocked_users(message: Message):
    """
    Показать список заблокированных пользователей.
    Команда: /blocked_users [limit=10]
    """
    try:
        # Получаем лимит из аргументов команды
        args = message.text.split()
        limit = 10  # По умолчанию показываем 10 пользователей
        if len(args) > 1 and args[1].isdigit():
            limit = min(int(args[1]), 50)  # Максимум 50
        
        # Получаем заблокированных пользователей
        blocked_users = await Users.filter(is_blocked=True).order_by('-blocked_at').limit(limit)
        
        if not blocked_users:
            await message.answer("📋 Заблокированных пользователей нет.")
            return
        
        # Формируем сообщение
        response = f"🚫 **Заблокированные пользователи** (показано {len(blocked_users)}):\n\n"
        
        for user in blocked_users:
            # Форматируем время блокировки
            blocked_time = user.blocked_at.strftime('%d.%m.%Y %H:%M') if user.blocked_at else "неизвестно"
            
            # Дополнительная информация
            failed_count = user.failed_message_count
            last_failed = user.last_failed_message_at.strftime('%d.%m.%Y %H:%M') if user.last_failed_message_at else "нет данных"
            
            response += (
                f"👤 **{user.full_name}** (`{user.id}`)\n"
                f"   🚫 Заблокирован: {blocked_time}\n"
                f"   📊 Неудачных попыток: {failed_count}\n"
                f"   ⏰ Последняя попытка: {last_failed}\n\n"
            )
        
        await message.answer(response, parse_mode="Markdown")
        logger.info(f"Администратор {message.from_user.id} запросил список заблокированных пользователей")
        
    except Exception as e:
        error_message = f"Ошибка при получении списка заблокированных пользователей: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("blocked_stats"), IsAdmin())
async def blocked_users_statistics(message: Message):
    """
    Показать статистику по заблокированным пользователям.
    Команда: /blocked_stats
    """
    try:
        await message.answer("⏳ Собираю статистику по заблокированным пользователям...")
        
        stats = await Users.get_blocked_users_stats()
        
        if "error" in stats:
            await message.answer(f"❌ Ошибка при получении статистики: {stats['error']}")
            return
        
        # Формируем сообщение со статистикой
        stats_message = (
            "📊 **Статистика заблокированных пользователей**\n\n"
            f"🚫 Всего заблокированных: **{stats['total_blocked']}**\n"
            f"📅 Заблокированных за последние 24ч: **{stats['blocked_last_24h']}**\n\n"
            
            "🗑 **Готовых к очистке:** **{ready_for_cleanup}**\n"
            f"  ├─ 🆓 Триальных: **{stats.get('blocked_trial_ready', 0)}**\n"
            f"  └─ 💎 Платных с истекшей подпиской: **{stats.get('blocked_paid_expired_ready', 0)}**\n\n"
            
            f"🔒 **Защищённых от очистки:**\n"
            f"  └─ 💎 Платных с активной подпиской: **{stats.get('blocked_paid_active', 0)}**\n\n"
            
            "⚙️ **Настройки системы:**\n"
            f"🔄 Автоочистка: {'✅ Включена' if stats['cleanup_enabled'] else '❌ Отключена'}\n"
            f"📆 Очистка через: **{stats['cleanup_days']}** дней\n"
            f"🔢 Максимум попыток: **{stats['max_failed_attempts']}**\n\n"
            
            "ℹ️ **Логика очистки:**\n"
            "• Триальные: удаляются через 7 дней после блокировки\n"
            "• Платные: удаляются только если подписка истекла > 7 дней назад\n"
            "• Платные с активной подпиской НЕ удаляются никогда\n"
        ).format(ready_for_cleanup=stats['ready_for_cleanup'])
        
        await message.answer(stats_message, parse_mode="Markdown")
        logger.info(f"Администратор {message.from_user.id} запросил статистику заблокированных пользователей")
        
    except Exception as e:
        error_message = f"Ошибка при получении статистики: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("cleanup_blocked"), IsAdmin())
async def manual_cleanup_blocked(message: Message):
    """
    Ручная очистка заблокированных пользователей.
    Команда: /cleanup_blocked
    """
    try:
        # Показываем предварительную статистику с учетом финальной логики
        cutoff_date = datetime.now() - timedelta(days=app_settings.blocked_user_cleanup_days)
        today = datetime.now().date()
        subscription_cutoff_date = today - timedelta(days=app_settings.blocked_user_cleanup_days)
        
        # Считаем по финальной логике
        # СЛУЧАЙ 1: Триальные пользователи
        trial_users_to_cleanup = await Users.filter(
            is_blocked=True,
            blocked_at__lte=cutoff_date,
            is_trial=True
        ).count()
        
        # СЛУЧАЙ 2: Платные пользователи с истекшей подпиской
        paid_expired_to_cleanup = await Users.filter(
            is_blocked=True,
            blocked_at__lte=cutoff_date,
            is_trial=False,
            expired_at__lte=subscription_cutoff_date
        ).count()
        
        total_to_cleanup = trial_users_to_cleanup + paid_expired_to_cleanup
        
        # Платные с активной подпиской (которых НЕ удаляем)
        paid_active_preserved = await Users.filter(
            is_blocked=True,
            blocked_at__lte=cutoff_date,
            is_trial=False,
            expired_at__gt=subscription_cutoff_date
        ).count()
        
        if total_to_cleanup == 0:
            if paid_active_preserved > 0:
                await message.answer(f"ℹ️ Нет пользователей готовых к очистке.\n💎 Сохраняем {paid_active_preserved} платных пользователей с активной подпиской.")
            else:
                await message.answer("ℹ️ Нет пользователей готовых к очистке.")
            return
        
        progress_message = await message.answer(
            f"🗑 **Начинаю очистку {total_to_cleanup} пользователей:**\n"
            f"  ├─ 🆓 Триальных: **{trial_users_to_cleanup}**\n"
            f"  └─ 💎 Платных с истекшей подпиской: **{paid_expired_to_cleanup}**\n\n"
            f"🔒 Сохраняю **{paid_active_preserved}** платных с активной подпиской",
            parse_mode="Markdown"
        )
        
        # Выполняем очистку
        await cleanup_blocked_users()
        
        # Проверяем результат
        remaining_trial = await Users.filter(
            is_blocked=True,
            blocked_at__lte=cutoff_date,
            is_trial=True
        ).count()
        
        remaining_paid_expired = await Users.filter(
            is_blocked=True,
            blocked_at__lte=cutoff_date,
            is_trial=False,
            expired_at__lte=subscription_cutoff_date
        ).count()
        
        remaining_total = remaining_trial + remaining_paid_expired
        
        cleaned_count = total_to_cleanup - remaining_total
        trial_cleaned = trial_users_to_cleanup - remaining_trial
        paid_cleaned = paid_expired_to_cleanup - remaining_paid_expired
        
        result_message = (
            f"✅ **Очистка завершена!**\n\n"
            f"🗑 **Удалено:** **{cleaned_count}** пользователей\n"
            f"  ├─ 🆓 Триальных: **{trial_cleaned}**\n"
            f"  └─ 💎 Платных с истекшей подпиской: **{paid_cleaned}**\n\n"
            f"📊 **Осталось к очистке:** **{remaining_total}**\n"
            f"💎 **Сохранено платных с активной подпиской:** **{paid_active_preserved}**"
        )
        
        await progress_message.edit_text(result_message, parse_mode="Markdown")
        logger.info(f"Администратор {message.from_user.id} запустил ручную очистку заблокированных пользователей: {cleaned_count} очищено")
        
    except Exception as e:
        error_message = f"Ошибка при очистке заблокированных пользователей: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("unblock_user"), IsAdmin())
async def unblock_user(message: Message):
    """
    Разблокировать пользователя (для восстановления или тестирования).
    Команда: /unblock_user [user_id]
    """
    try:
        # Получаем ID пользователя из аргументов команды
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "❌ **Ошибка**: Необходимо указать ID пользователя.\n"
                "**Пример**: `/unblock_user 123456789`",
                parse_mode="Markdown"
            )
            return
        
        user_id = int(args[1])
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"❌ Пользователь с ID `{user_id}` не найден.", parse_mode="Markdown")
            return
        
        if not user.is_blocked:
            await message.answer(
                f"ℹ️ Пользователь **{user.full_name}** (`{user_id}`) не заблокирован.",
                parse_mode="Markdown"
            )
            return
        
        # Сохраняем информацию для логирования
        blocked_at = user.blocked_at
        failed_count = user.failed_message_count
        
        # Разблокируем пользователя
        user.is_blocked = False
        user.blocked_at = None
        user.failed_message_count = 0
        user.last_failed_message_at = None
        await user.save()
        
        # Перепланируем задачи для пользователя
        from bloobcat.scheduler import schedule_user_tasks
        await schedule_user_tasks(user)
        
        success_message = (
            f"✅ **Пользователь разблокирован!**\n\n"
            f"👤 **{user.full_name}** (`{user_id}`)\n"
            f"📅 Был заблокирован: {blocked_at.strftime('%d.%m.%Y %H:%M') if blocked_at else 'неизвестно'}\n"
            f"🔢 Было неудачных попыток: {failed_count}\n\n"
            f"🔄 Задачи планировщика перенастроены."
        )
        
        await message.answer(success_message, parse_mode="Markdown")
        logger.info(f"Администратор {message.from_user.id} разблокировал пользователя {user_id}")
        
    except Exception as e:
        error_message = f"Ошибка при разблокировке пользователя: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("block_user"), IsAdmin())
async def block_user(message: Message):
    """
    Принудительно заблокировать пользователя (для тестирования).
    Команда: /block_user [user_id] [reason]
    """
    try:
        # Получаем аргументы команды
        args = message.text.split(maxsplit=2)
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "❌ **Ошибка**: Необходимо указать ID пользователя.\n"
                "**Пример**: `/block_user 123456789 причина блокировки`",
                parse_mode="Markdown"
            )
            return
        
        user_id = int(args[1])
        reason = args[2] if len(args) > 2 else "Заблокирован администратором"
        
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        
        if not user:
            await message.answer(f"❌ Пользователь с ID `{user_id}` не найден.", parse_mode="Markdown")
            return
        
        if user.is_blocked:
            await message.answer(
                f"ℹ️ Пользователь **{user.full_name}** (`{user_id}`) уже заблокирован.",
                parse_mode="Markdown"
            )
            return
        
        # Блокируем пользователя
        from bloobcat.scheduler import cancel_user_tasks
        
        user.is_blocked = True
        user.blocked_at = datetime.now()
        user.failed_message_count += 1
        user.last_failed_message_at = datetime.now()
        await user.save()
        
        # Отменяем все задачи
        cancel_user_tasks(user_id)
        
        success_message = (
            f"🚫 **Пользователь заблокирован!**\n\n"
            f"👤 **{user.full_name}** (`{user_id}`)\n"
            f"📝 Причина: {reason}\n"
            f"📅 Дата блокировки: {user.blocked_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"🔄 Все задачи планировщика отменены."
        )
        
        await message.answer(success_message, parse_mode="Markdown")
        logger.warning(f"Администратор {message.from_user.id} принудительно заблокировал пользователя {user_id}. Причина: {reason}")
        
    except Exception as e:
        error_message = f"Ошибка при блокировке пользователя: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message) 