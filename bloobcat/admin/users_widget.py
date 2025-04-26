"""
Модуль содержит импорты виджетов пользователей для обратной совместимости.
Каждый виджет теперь находится в отдельном файле.
"""

from bloobcat.admin.total_users_widget import TotalUsersDashboardWidgetAdmin
from bloobcat.admin.active_users_widget import ActiveUsersDashboardWidgetAdmin
from bloobcat.admin.inactive_users_widget import InactiveUsersDashboardWidgetAdmin
from bloobcat.admin.registered_users_widget import RegisteredUsersDashboardWidgetAdmin

# Экспортируем классы для обратной совместимости
__all__ = [
    "TotalUsersDashboardWidgetAdmin",
    "ActiveUsersDashboardWidgetAdmin",
    "InactiveUsersDashboardWidgetAdmin",
    "RegisteredUsersDashboardWidgetAdmin",
]
