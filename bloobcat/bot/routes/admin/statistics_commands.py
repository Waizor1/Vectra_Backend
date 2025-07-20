"""
Админские команды для работы со статистикой
"""

from datetime import date, timedelta
import calendar

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.statistics.collector import StatisticsCollector
from bloobcat.statistics.trends import TrendsCalculator
from bloobcat.statistics.formatter import StatisticsFormatter
from bloobcat.statistics.scheduler import (
    send_daily_statistics, 
    send_weekly_statistics, 
    send_monthly_statistics
)
from bloobcat.bot.notifications.admin import send_admin_message
from bloobcat.logger import get_logger

logger = get_logger("admin_statistics_commands")
router = Router()


@router.message(Command("stats_today"), IsAdmin())
async def admin_stats_today(message: Message):
    """Команда для получения статистики за сегодня"""
    try:
        await message.answer("📊 Собираю статистику за сегодня...")
        
        today = date.today()
        stats = await StatisticsCollector.collect_daily_stats(today)
        report = StatisticsFormatter.format_test_report(stats, "daily")
        
        await message.answer(report, parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} requested today's statistics")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при получении статистики за сегодня: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error getting today's statistics: {e}")


@router.message(Command("stats_yesterday"), IsAdmin())
async def admin_stats_yesterday(message: Message):
    """Команда для получения статистики за вчера с трендами"""
    try:
        await message.answer("📊 Собираю статистику за вчера с трендами...")
        
        yesterday = date.today() - timedelta(days=1)
        trends_data = await TrendsCalculator.calculate_daily_trends(yesterday)
        report = StatisticsFormatter.format_daily_report(trends_data)
        
        await message.answer(report, parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} requested yesterday's statistics with trends")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при получении статистики за вчера: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error getting yesterday's statistics: {e}")


@router.message(Command("stats_week"), IsAdmin())
async def admin_stats_week(message: Message):
    """Команда для получения недельной статистики"""
    try:
        await message.answer("📊 Собираю недельную статистику...")
        
        # Неделя до вчера (последняя полная неделя)
        yesterday = date.today() - timedelta(days=1)
        # Находим последнюю субботу
        days_since_saturday = (yesterday.weekday() + 2) % 7
        last_saturday = yesterday - timedelta(days=days_since_saturday)
        
        trends_data = await TrendsCalculator.calculate_weekly_trends(last_saturday)
        report = StatisticsFormatter.format_weekly_report(trends_data)
        
        await message.answer(report, parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} requested weekly statistics")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при получении недельной статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error getting weekly statistics: {e}")


@router.message(Command("stats_month"), IsAdmin())
async def admin_stats_month(message: Message):
    """Команда для получения месячной статистики"""
    try:
        await message.answer("📊 Собираю месячную статистику...")
        
        today = date.today()
        # Статистика за предыдущий месяц
        if today.month == 1:
            target_month = 12
            target_year = today.year - 1
        else:
            target_month = today.month - 1
            target_year = today.year
        
        trends_data = await TrendsCalculator.calculate_monthly_trends(target_month, target_year)
        report = StatisticsFormatter.format_monthly_report(trends_data)
        
        await message.answer(report, parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} requested monthly statistics")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при получении месячной статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error getting monthly statistics: {e}")


@router.message(Command("test_daily_stats"), IsAdmin())
async def admin_test_daily_stats(message: Message):
    """Тестирует отправку дневной статистики в канал"""
    try:
        await message.answer("🧪 Тестирую отправку дневной статистики в канал...")
        
        await send_daily_statistics()
        
        await message.answer("✅ Дневная статистика отправлена в канал!")
        logger.info(f"Admin {message.from_user.id} tested daily statistics send")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при тестировании дневной статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error testing daily statistics: {e}")


@router.message(Command("test_weekly_stats"), IsAdmin())
async def admin_test_weekly_stats(message: Message):
    """Тестирует отправку недельной статистики в канал"""
    try:
        await message.answer("🧪 Тестирую отправку недельной статистики в канал...")
        
        await send_weekly_statistics()
        
        await message.answer("✅ Недельная статистика отправлена в канал!")
        logger.info(f"Admin {message.from_user.id} tested weekly statistics send")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при тестировании недельной статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error testing weekly statistics: {e}")


@router.message(Command("test_monthly_stats"), IsAdmin())
async def admin_test_monthly_stats(message: Message):
    """Тестирует отправку месячной статистики в канал"""
    try:
        await message.answer("🧪 Тестирую отправку месячной статистики в канал...")
        
        await send_monthly_statistics()
        
        await message.answer("✅ Месячная статистика отправлена в канал!")
        logger.info(f"Admin {message.from_user.id} tested monthly statistics send")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при тестировании месячной статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error testing monthly statistics: {e}")


@router.message(Command("stats_total"), IsAdmin())
async def admin_stats_total(message: Message):
    """Команда для получения общей статистики проекта"""
    try:
        await message.answer("📊 Собираю общую статистику проекта...")
        
        stats = await StatisticsCollector.get_total_stats()
        
        report = f"""📊 <b>Общая статистика проекта</b>

<b>👥 Пользователи:</b>
• Всего зарегистрировано: {stats['total_users']}
• Активировали бота: {stats['registered_users']}
• С активной подпиской: {stats['active_users']}

<b>💰 Платежи:</b>
• Всего успешных платежей: {stats['total_payments']}
• Общая выручка: {StatisticsFormatter.format_currency(stats['total_revenue'])}

<b>📈 Конверсия:</b>
• Активация бота: {(stats['registered_users'] / max(stats['total_users'], 1) * 100):.1f}%
• В платные: {(stats['active_users'] / max(stats['registered_users'], 1) * 100):.1f}%

#общая_статистика"""
        
        await message.answer(report, parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} requested total statistics")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при получении общей статистики: {str(e)}"
        await message.answer(error_msg)
        logger.error(f"Error getting total statistics: {e}") 