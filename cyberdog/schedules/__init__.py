import asyncio
from cyberdog.logger import get_logger

# Импортируем задачи и хелперы из этого же пакета
from .subscriptions import check_subscriptions
from .trials import check_trial_users
from .helpers import periodic_task, daily_task

# Импортируем внешние задачи (полные пути)
from cyberdog.routes.marzban.catcher import marzban_updater # , reset_expired_users # Закомментировано, как в оригинале
from cyberdog.bot.alerts import alerts_worker

# Инициализируем логгер для модуля расписаний
logger = get_logger("schedules")

# Список активных задач для возможности их отмены
active_tasks = []

# Экспортируем для использования извне
__all__ = ['start_scheduler', 'active_tasks']

def start_scheduler():
    """Запускает все фоновые задачи"""
    global active_tasks
    
    # Очищаем список активных задач (на случай перезапуска)
    for task in active_tasks:
        if not task.done():
            task.cancel()
    active_tasks = []
    
    # Импорты внешних функций перенесены на уровень модуля
    
    # Создаем и запускаем задачи
    marzban_task = asyncio.create_task(
        periodic_task(marzban_updater, 300, "marzban_updater")  # Каждые 5 минут
    )
    # reset_task = asyncio.create_task(
    #     periodic_task(reset_expired_users, 1800, "reset_expired_users")  # Каждые 30 минут
    # )
    alerts_task = asyncio.create_task(
        daily_task(alerts_worker, hour=7, minute=0, task_name="alerts_worker")
    )
    subscriptions_task = asyncio.create_task(
        daily_task(check_subscriptions, hour=12, minute=0, task_name="check_subscriptions")
    )
    trial_users_task = asyncio.create_task(
        periodic_task(check_trial_users, 3600, "check_trial_users")  # Каждый час
    )
    
    # Сохраняем задачи для возможности их отмены
    active_tasks = [marzban_task, alerts_task, subscriptions_task, trial_users_task]  # Убираем reset_task из списка
    
    logger.info("Фоновые задачи запущены")
    
    return active_tasks 