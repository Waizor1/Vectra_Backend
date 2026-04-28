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
from bloobcat.utils.dates import add_months_safe

logger = get_logger("statistics_scheduler")

MOSCOW = ZoneInfo("Europe/Moscow")

# Глобальные переменные для отслеживания активных задач
active_tasks = {
    "daily": None,
    "weekly": None, 
    "monthly": None
}


def get_next_daily_time(now: datetime | None = None) -> datetime:
    """Получает время для следующей дневной статистики.

    Важно: если текущая задача закончилась уже после полуночи, следующий daily
    report должен планироваться на текущий московский день в 23:59, а не
    перескакивать сразу на завтра.
    """
    current = now or datetime.now(MOSCOW)
    today_daily_time = datetime.combine(
        current.date(), time(23, 59), tzinfo=MOSCOW
    )
    if current < today_daily_time:
        return today_daily_time
    return datetime.combine(
        current.date() + timedelta(days=1), time(23, 59), tzinfo=MOSCOW
    )


def get_next_weekly_time(now: datetime | None = None) -> datetime:
    """Получает время для следующей недельной статистики (следующее воскресенье в 23:59)"""
    current = now or datetime.now(MOSCOW)
    today = current.date()
    days_until_sunday = (6 - today.weekday()) % 7
    
    if days_until_sunday == 0:  # Сегодня воскресенье
        # Проверяем, не прошло ли уже время отправки (23:59)
        today_weekly_time = datetime.combine(today, time(23, 59), tzinfo=MOSCOW)
        if current < today_weekly_time:
            # Время еще не прошло, планируем на сегодня
            return today_weekly_time
        else:
            # Время уже прошло, планируем на следующее воскресенье
            days_until_sunday = 7
    
    next_sunday = today + timedelta(days=days_until_sunday)
    return datetime.combine(next_sunday, time(23, 59), tzinfo=MOSCOW)


def get_next_monthly_time(now: datetime | None = None) -> datetime:
    """Получает время для следующей месячной статистики (31 число или последний день месяца в 23:59)"""
    current = now or datetime.now(MOSCOW)
    today = current.date()

    last_day = calendar.monthrange(today.year, today.month)[1]
    current_monthly_date = today.replace(day=last_day)
    current_monthly_time = datetime.combine(
        current_monthly_date, time(23, 59), tzinfo=MOSCOW
    )

    if current < current_monthly_time:
        return current_monthly_time

    next_month = add_months_safe(today, 1)
    next_month_last_day = calendar.monthrange(next_month.year, next_month.month)[1]
    monthly_date = next_month.replace(day=next_month_last_day)

    return datetime.combine(monthly_date, time(23, 59), tzinfo=MOSCOW)


async def send_daily_statistics(target_date: date | None = None) -> bool:
    """Отправляет дневную статистику в канал и планирует следующую задачу"""
    try:
        logger.info("Generating daily statistics...")
        
        # Статистика за сегодняшний день (функция запускается в 23:59)
        target_date = target_date or date.today()
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_daily_trends(target_date)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_daily_report(trends_data)
        
        # Отправляем в канал
        delivered = await send_admin_message(report)
        if delivered:
            logger.info(f"Daily statistics for {target_date} sent successfully")
        else:
            logger.error(f"Daily statistics delivery failed for {target_date}")
        return delivered
        
    except Exception as e:
        logger.error(f"Error sending daily statistics: {e}", exc_info=True)
        return False
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_daily_statistics()


async def send_weekly_statistics(end_date: date | None = None) -> bool:
    """Отправляет недельную статистику в канал и планирует следующую задачу"""
    try:
        logger.info("Generating weekly statistics...")
        
        # Статистика за закончившуюся неделю (воскресенье)
        end_date = end_date or date.today()
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_weekly_trends(end_date)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_weekly_report(trends_data)
        
        # Отправляем в канал
        delivered = await send_admin_message(report)
        if delivered:
            logger.info(
                f"Weekly statistics for week ending {end_date} sent successfully"
            )
        else:
            logger.error(f"Weekly statistics delivery failed for week ending {end_date}")
        return delivered
        
    except Exception as e:
        logger.error(f"Error sending weekly statistics: {e}", exc_info=True)
        return False
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_weekly_statistics()


async def send_monthly_statistics(target_date: date | None = None) -> bool:
    """Отправляет месячную статистику в канал и планирует следующую задачу"""
    try:
        logger.info("Generating monthly statistics...")
        
        # Статистика за текущий месяц
        target_date = target_date or date.today()
        target_month = target_date.month
        target_year = target_date.year
        
        # Собираем данные и тренды
        trends_data = await TrendsCalculator.calculate_monthly_trends(target_month, target_year)
        
        # Форматируем отчет
        report = StatisticsFormatter.format_monthly_report(trends_data)
        
        # Отправляем в канал
        delivered = await send_admin_message(report)
        if delivered:
            logger.info(
                f"Monthly statistics for {target_month}/{target_year} sent successfully"
            )
        else:
            logger.error(
                f"Monthly statistics delivery failed for {target_month}/{target_year}"
            )
        return delivered
        
    except Exception as e:
        logger.error(f"Error sending monthly statistics: {e}", exc_info=True)
        return False
    finally:
        # ВАЖНО: Планируем следующую задачу независимо от результата
        schedule_next_monthly_statistics()


def schedule_next_daily_statistics():
    """Планирует следующую задачу дневной статистики"""
    target_time = get_next_daily_time()
    logger.info(f"Scheduling next daily statistics for {target_time.isoformat()}")
    
    async def daily_runner():
        scheduled_date = target_time.date()
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Daily statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_daily_statistics(scheduled_date)
    
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
        scheduled_date = target_time.date()
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Weekly statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_weekly_statistics(scheduled_date)
    
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
        scheduled_date = target_time.date()
        now = datetime.now(MOSCOW)
        delay = (target_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Monthly statistics: sleeping for {delay:.1f} seconds until {target_time.isoformat()}")
            await asyncio.sleep(delay)
        await send_monthly_statistics(scheduled_date)
    
    # Отменяем предыдущую задачу если есть
    if active_tasks["monthly"] and not active_tasks["monthly"].done():
        active_tasks["monthly"].cancel()
    
    # Создаем новую задачу
    active_tasks["monthly"] = asyncio.create_task(monthly_runner())


def setup_initial_statistics_tasks():
    """Настраивает начальные задачи автоматической отправки статистики"""
    logger.info("Setting up initial automatic statistics tasks...")
    
    now = datetime.now(MOSCOW)
    
    # Дневная статистика: ближайшие 23:59 по Москве
    daily_time = get_next_daily_time(now)
    
    # Недельная статистика: ближайшее воскресенье в 23:59
    weekly_time = get_next_weekly_time(now)
    
    # Месячная статистика: используем исправленную функцию
    monthly_time = get_next_monthly_time(now)
    
    logger.info(f"Initial statistics tasks scheduled:")
    logger.info(f"  Daily: {daily_time.isoformat()}")
    logger.info(f"  Weekly: {weekly_time.isoformat()}")
    logger.info(f"  Monthly: {monthly_time.isoformat()}")
    
    # Запускаем первичные задачи
    async def initial_daily_runner():
        scheduled_date = daily_time.date()
        now = datetime.now(MOSCOW)
        delay = (daily_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial daily statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_daily_statistics(scheduled_date)
    
    async def initial_weekly_runner():
        scheduled_date = weekly_time.date()
        now = datetime.now(MOSCOW)
        delay = (weekly_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial weekly statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_weekly_statistics(scheduled_date)
    
    async def initial_monthly_runner():
        scheduled_date = monthly_time.date()
        now = datetime.now(MOSCOW)
        delay = (monthly_time - now).total_seconds()
        if delay > 0:
            logger.debug(f"Initial monthly statistics: sleeping for {delay:.1f} seconds")
            await asyncio.sleep(delay)
        await send_monthly_statistics(scheduled_date)
    
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
