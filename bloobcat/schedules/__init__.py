import asyncio
from bloobcat.logger import get_logger

# Импортируем задачи и хелперы из этого же пакета
from .subscriptions import check_subscriptions
from .trials import check_trial_users
from .helpers import periodic_task, daily_task

# Импортируем внешние задачи (полные пути)
from bloobcat.routes.remnawave.catcher import remnawave_updater

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
    remnawave_task = asyncio.create_task(
        periodic_task(remnawave_updater, 300, "remnawave_updater")  # Каждые 5 минут
    )
    subscriptions_task = asyncio.create_task(
        daily_task(check_subscriptions, hour=12, minute=0, task_name="check_subscriptions")
    )
    trial_users_task = asyncio.create_task(
        periodic_task(check_trial_users, 3600, "check_trial_users")  # Каждый час
    )
    
    # Сохраняем задачи для возможности их отмены
    active_tasks = [remnawave_task, subscriptions_task, trial_users_task]
    
    logger.info("Фоновые задачи запущены")
    
    return active_tasks 