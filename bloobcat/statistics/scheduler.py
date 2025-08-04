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

# Глобальные переменные для отслеживания активных задач
active_tasks = {
    "daily": None,
    "weekly": None, 
    "monthly": None
}


def get_next_daily_time() -> datetime:
    """Получает время для следующей дневной статистики (завтра в 23:59)"""
    tomorrow = date.today() + timedelta(days=1)
    return datetime.combine(tomorrow, time(23, 59)).replace(tzinfo=MOSCOW)


def get_next_weekly_time() -> datetime:
    """Получает время для следующей недельной статистики (следующее воскресенье в 23:59)"""
    now = datetime.now(MOSCOW)
    today = now.date()
    days_until_sunday = (6 - today.weekday()) % 7
    
    if days_until_sunday == 0:  # Сегодня воскресенье
        # Проверяем, не прошло ли уже время отправки (23:59)
        today_weekly_time = datetime.combine(today, time(23, 59)).replace(tzinfo=MOSCOW)
        if now < today_weekly_time:
            # Время еще не прошло, планируем на сегодня
            return today_weekly_time
        else:
            # Время уже прошло, планируем на следующее воскресенье
            days_until_sunday = 7
    
    next_sunday = today + timedelta(days=days_until_sunday)
    return datetime.combine(next_sunday, time(23, 59)).replace(tzinfo=MOSCOW)


def get_next_monthly_time() -> datetime:
    """Получает время для следующей месячной статистики (31 число или последний день месяца в 23:59)"""
    today = date.today()
    
    # Пробуем следующий месяц
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1)
    else:
        next_month = today.replace(month=today.month + 1)
    
    # Пытаемся установить 31 число, если нет - последний день месяца
    try:
        monthly_date = next_month.replace(day=31)
    except ValueError:
        last_day = calendar.monthrange(next_month.year, next_month.month)[1]
        monthly_date = next_month.replace(day=last_day)
    
    return datetime.combine(monthly_date, time(23, 59)).replace(tzinfo=MOSCOW)


async def send_daily_statistics():
    """Отправляет дневную статистику в канал и планирует следующую задачу"""
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
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_daily_statistics()


async def send_weekly_statistics():
    """Отправляет недельную статистику в канал и планирует следующую задачу"""
    try:
        logger.info("Generating weekly statistics...")
        
        # Статистика за закончившуюся неделю (воскресенье)
        end_date = date.today()
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_weekly_trends(end_date)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_weekly_report(trends_data)
        
        # Отправляем в канал
        await send_admin_message(report)
        
        logger.info(f"Weekly statistics for week ending {end_date} sent successfully")
        
    except Exception as e:
        logger.error(f"Error sending weekly statistics: {e}", exc_info=True)
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_weekly_statistics()


async def send_monthly_statistics():
    """Отправляет месячную статистику в канал и планирует следующую задачу"""
    try:
        logger.info("Generating monthly statistics...")
        
        # Статистика за текущий месяц
        today = date.today()
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
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_monthly_statistics()


def schedule_next_daily_statistics():
    """Планирует следующую задачу дневной статистики"""
    target_time = get_next_daily_time()
    logger.info(f"Scheduling next daily statistics for {target_time.isoformat()}")
    
    async def daily_runner():
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Daily statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_daily_statistics()
    
    # Отменяем предыдущую задачу если есть
    if active_tasks["daily"] and not active_tasks["daily"].done():
        active_tasks["daily"].cancel()
    
    # Создаем новую задачу
    active_tasks["daily"] = asyncio.create_task(daily_runner())


def schedule_next_weekly_statistics():
    """Планирует следующую задачу недельной статистики"""
    target_time = get_next_weekly_time()
    logger.info(f"Scheduling next weekly statistics for {target_time.isoformat()}")
    
    async def weekly_runner():
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Weekly statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_weekly_statistics()
    
    # Отменяем предыдущую задачу если есть
    if active_tasks["weekly"] and not active_tasks["weekly"].done():
        active_tasks["weekly"].cancel()
    
    # Создаем новую задачу
    active_tasks["weekly"] = asyncio.create_task(weekly_runner())


def schedule_next_monthly_statistics():
    """Планирует следующую задачу месячной статистики"""
    target_time = get_next_monthly_time()
    logger.info(f"Scheduling next monthly statistics for {target_time.isoformat()}")
    
    async def monthly_runner():
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Monthly statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_monthly_statistics()
    
    # Отменяем предыдущую задачу если есть
    if active_tasks["monthly"] and not active_tasks["monthly"].done():
        active_tasks["monthly"].cancel()
    
    # Создаем новую задачу
    active_tasks["monthly"] = asyncio.create_task(monthly_runner())


def setup_initial_statistics_tasks():
    """Настраивает начальные задачи автоматической отправки статистики"""
    logger.info("Setting up initial automatic statistics tasks...")
    
    now = datetime.now(MOSCOW)
    today = now.date()
    
    # Дневная статистика: сегодня в 23:59 или завтра если уже прошло
    daily_time = datetime.combine(today, time(23, 59)).replace(tzinfo=MOSCOW)
    if daily_time <= now:
        daily_time = get_next_daily_time()
    
    # Недельная статистика: ближайшее воскресенье в 23:59
    weekly_time = get_next_weekly_time()
    
    # Месячная статистика: 31 число текущего месяца или следующего
    try:
        monthly_date = today.replace(day=31)
    except ValueError:
        last_day = calendar.monthrange(today.year, today.month)[1]
        monthly_date = today.replace(day=last_day)
    
    monthly_time = datetime.combine(monthly_date, time(23, 59)).replace(tzinfo=MOSCOW)
    if monthly_time <= now:
        monthly_time = get_next_monthly_time()
    
    logger.info(f"Initial statistics tasks scheduled:")
    logger.info(f"  Daily: {daily_time.isoformat()}")
    logger.info(f"  Weekly: {weekly_time.isoformat()}")
    logger.info(f"  Monthly: {monthly_time.isoformat()}")
    
    # Запускаем первичные задачи
    async def initial_daily_runner():
        now = datetime.now(MOSCOW)
        delay = (daily_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial daily statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_daily_statistics()
    
    async def initial_weekly_runner():
        now = datetime.now(MOSCOW)
        delay = (weekly_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial weekly statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_weekly_statistics()
    
    async def initial_monthly_runner():
        now = datetime.now(MOSCOW)
        delay = (monthly_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial monthly statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_monthly_statistics()
    
    # Создаем начальные задачи
    active_tasks["daily"] = asyncio.create_task(initial_daily_runner())
    active_tasks["weekly"] = asyncio.create_task(initial_weekly_runner())
    active_tasks["monthly"] = asyncio.create_task(initial_monthly_runner())


async def statistics_scheduler():
    """Постоянно работающий планировщик статистики"""
    logger.info("Starting statistics scheduler...")
    
    # Настраиваем начальные задачи при запуске
    setup_initial_statistics_tasks()
    
    # Мониторим состояние задач каждые 30 минут
    while True:
        try:
            await asyncio.sleep(1800)  # 30 минут
            
            # Проверяем состояние задач и перезапускаем упавшие
            for task_type, task in active_tasks.items():
                if task is None or task.done():
                    if task and task.done() and not task.cancelled():
                        # Задача завершилась, проверяем на ошибки
                        try:
                            await task
                        except Exception as e:
                            logger.error(f"Statistics task '{task_type}' failed: {e}")
                    
                    # Перезапускаем задачу если она не активна
                    logger.warning(f"Statistics task '{task_type}' is not active, restarting...")
                    if task_type == "daily":
                        schedule_next_daily_statistics()
                    elif task_type == "weekly":
                        schedule_next_weekly_statistics()
                    elif task_type == "monthly":
                        schedule_next_monthly_statistics()
                        
        except Exception as e:
            logger.error(f"Error in statistics scheduler monitor: {e}")
            await asyncio.sleep(300)  # Подождем 5 минут при ошибке