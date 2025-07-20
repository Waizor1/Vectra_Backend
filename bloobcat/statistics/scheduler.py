"""
Планировщик автоматической отправки статистики в канал логов
"""

import asyncio
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
import calendar

from .collector import StatisticsCollector
from .trends import TrendsCalculator
from .formatter import StatisticsFormatter
from bloobcat.bot.notifications.admin import send_admin_message
from bloobcat.logger import get_logger

logger = get_logger("statistics_scheduler")

MOSCOW = ZoneInfo("Europe/Moscow")


async def send_daily_statistics():
    """Отправляет дневную статистику в канал"""
    try:
        logger.info("Generating daily statistics...")
        
        # Статистика за сегодняшний день (функция запускается в 23:59)
        target_date = date.today()
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_daily_trends(target_date)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_daily_report(trends_data)
        
        # Отправляем в канал
        await send_admin_message(report)
        
        logger.info(f"Daily statistics for {target_date} sent successfully")
        
    except Exception as e:
        logger.error(f"Error sending daily statistics: {e}", exc_info=True)


async def send_weekly_statistics():
    """Отправляет недельную статистику в канал (воскресенье)"""
    try:
        logger.info("Generating weekly statistics...")
        
        # Сегодня должно быть воскресенье
        today = date.today()
        if today.weekday() != 6:  # 6 = воскресенье
            logger.warning(f"Weekly statistics called on non-Sunday: {today}")
            return
            
        # Неделя заканчивается сегодня (воскресенье)
        end_date = today
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_weekly_trends(end_date)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_weekly_report(trends_data)
        
        # Отправляем в канал
        await send_admin_message(report)
        
        logger.info(f"Weekly statistics for week ending {end_date} sent successfully")
        
    except Exception as e:
        logger.error(f"Error sending weekly statistics: {e}", exc_info=True)


async def send_monthly_statistics():
    """Отправляет месячную статистику в канал (31 число или последний день месяца)"""
    try:
        logger.info("Generating monthly statistics...")
        
        today = date.today()
        
        # Проверяем, что сегодня 31 число или последний день месяца
        last_day_of_month = calendar.monthrange(today.year, today.month)[1]
        if today.day != 31 and today.day != last_day_of_month:
            logger.warning(f"Monthly statistics called on wrong day: {today}")
            return
        
        # Статистика за текущий месяц (который завершается сегодня)
        target_month = today.month
        target_year = today.year
            
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_monthly_trends(target_month, target_year)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_monthly_report(trends_data)
        
        # Отправляем в канал
        await send_admin_message(report)
        
        logger.info(f"Monthly statistics for {target_month}/{target_year} sent successfully")
        
    except Exception as e:
        logger.error(f"Error sending monthly statistics: {e}", exc_info=True)


def schedule_statistics_coro(target_time: datetime, coro, description: str):
    """Планирует выполнение корутины статистики в определенное время"""
    now = datetime.now(MOSCOW)
    delay = (target_time - now).total_seconds()
    
    if delay <= 0:
        logger.warning(f"Statistics task '{description}' scheduled for past time {target_time.isoformat()}")
        return None
    
    logger.debug(f"Scheduling statistics task '{description}' at {target_time.isoformat()}")

    async def runner():
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await coro()
    
    task = asyncio.create_task(runner())
    return task


def setup_statistics_tasks():
    """Настраивает задачи автоматической отправки статистики"""
    logger.info("Setting up automatic statistics tasks...")
    
    now = datetime.now(MOSCOW)
    today = now.date()
    
    # Дневная статистика: каждый день в 23:59
    daily_time = datetime.combine(today, time(23, 59)).replace(tzinfo=MOSCOW)
    if daily_time <= now:
        daily_time += timedelta(days=1)
    
    schedule_statistics_coro(daily_time, send_daily_statistics, "daily_statistics")
    
    # Недельная статистика: каждое воскресенье в 23:59
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 23 and now.minute >= 59:
        days_until_sunday = 7
    
    next_sunday = today + timedelta(days=days_until_sunday)
    weekly_time = datetime.combine(next_sunday, time(23, 59)).replace(tzinfo=MOSCOW)
    
    schedule_statistics_coro(weekly_time, send_weekly_statistics, "weekly_statistics")
    
    # Месячная статистика: 31 число каждого месяца в 23:59
    # Если в текущем месяце нет 31 числа, то в последний день месяца
    try:
        monthly_date = today.replace(day=31)
    except ValueError:
        # В месяце нет 31 числа, берем последний день
        last_day = calendar.monthrange(today.year, today.month)[1]
        monthly_date = today.replace(day=last_day)
    
    # Если 31 число уже прошло в этом месяце, планируем на следующий месяц
    if monthly_date <= today or (monthly_date == today and now.hour >= 23 and now.minute >= 59):
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1)
        else:
            next_month = today.replace(month=today.month + 1)
        
        try:
            monthly_date = next_month.replace(day=31)
        except ValueError:
            last_day = calendar.monthrange(next_month.year, next_month.month)[1]
            monthly_date = next_month.replace(day=last_day)
    
    monthly_time = datetime.combine(monthly_date, time(23, 59)).replace(tzinfo=MOSCOW)
    
    schedule_statistics_coro(monthly_time, send_monthly_statistics, "monthly_statistics")
    
    logger.info(f"Statistics tasks scheduled:")
    logger.info(f"  Daily: {daily_time.isoformat()}")
    logger.info(f"  Weekly: {weekly_time.isoformat()}")
    logger.info(f"  Monthly: {monthly_time.isoformat()}")


async def statistics_scheduler():
    """Постоянно работающий планировщик статистики"""
    logger.info("Starting statistics scheduler...")
    
    # Настраиваем задачи при запуске
    setup_statistics_tasks()
    
    # Проверяем каждый час, нужно ли перепланировать задачи
    while True:
        try:
            await asyncio.sleep(3600)  # 1 час
            
            # Проверяем, наступил ли новый день
            now = datetime.now(MOSCOW)
            if now.hour == 0 and now.minute < 5:  # Первые 5 минут нового дня
                logger.debug("New day detected, re-scheduling statistics tasks...")
                setup_statistics_tasks()
                
        except Exception as e:
            logger.error(f"Error in statistics scheduler: {e}")
            await asyncio.sleep(300)  # Подождем 5 минут при ошибке 