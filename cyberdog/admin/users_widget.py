"""
Модуль содержит импорты виджетов пользователей для обратной совместимости.
Каждый виджет теперь находится в отдельном файле.
"""

from cyberdog.admin.total_users_widget import TotalUsersDashboardWidgetAdmin
from cyberdog.admin.active_users_widget import ActiveUsersDashboardWidgetAdmin
from cyberdog.admin.inactive_users_widget import InactiveUsersDashboardWidgetAdmin

# Экспортируем классы для обратной совместимости
__all__ = [
    "TotalUsersDashboardWidgetAdmin",
    "ActiveUsersDashboardWidgetAdmin",
    "InactiveUsersDashboardWidgetAdmin",
]
