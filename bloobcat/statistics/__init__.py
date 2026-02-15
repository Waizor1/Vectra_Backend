"""
Модуль автоматической статистики TVPN Bot

Обеспечивает:
- Ежедневную статистику в 23:59 МСК
- Еженедельную статистику в воскресенье в 23:59 МСК  
- Ежемесячную статистику 31 числа в 23:59 МСК
- Расчет трендов и отправку в админский канал
"""

from .collector import StatisticsCollector
from .trends import TrendsCalculator
from .formatter import StatisticsFormatter

__all__ = [
    "StatisticsCollector",
    "TrendsCalculator", 
    "StatisticsFormatter"
] 