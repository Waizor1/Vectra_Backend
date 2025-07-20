"""
Модуль автоматической статистики BloobCat VPN Bot

Обеспечивает:
- Ежедневную статистику в 23:59 МСК
- Еженедельную статистику в воскресенье в 23:59 МСК  
- Ежемесячную статистику 31 числа в 23:59 МСК
- Расчет трендов и отправку в админский канал
"""

from .collector import StatisticsCollector
from .trends import TrendsCalculator
from .formatter import StatisticsFormatter
from .scheduler import setup_statistics_tasks

__all__ = [
    "StatisticsCollector",
    "TrendsCalculator", 
    "StatisticsFormatter",
    "setup_statistics_tasks"
] 