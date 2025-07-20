"""
Модуль расчета трендов и сравнения статистики с предыдущими периодами
"""

from datetime import date, timedelta
from typing import Dict, Any, Optional, Tuple
from enum import Enum

from .collector import StatisticsCollector
from bloobcat.logger import get_logger

logger = get_logger("statistics_trends")


class TrendDirection(Enum):
    UP = "📈"
    DOWN = "📉"
    SAME = "➡️"


class TrendsCalculator:
    """Класс для расчета трендов статистических данных"""

    @staticmethod
    def calculate_percentage_change(current: float, previous: float) -> float:
        """Рассчитывает процентное изменение"""
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return ((current - previous) / previous) * 100

    @staticmethod
    def get_trend_direction(percentage_change: float) -> TrendDirection:
        """Определяет направление тренда"""
        if percentage_change > 0:
            return TrendDirection.UP
        elif percentage_change < 0:
            return TrendDirection.DOWN
        else:
            return TrendDirection.SAME

    @staticmethod
    def format_trend(current: int, previous: int, metric_name: str) -> str:
        """Форматирует тренд для отображения"""
        change = current - previous
        percentage_change = TrendsCalculator.calculate_percentage_change(current, previous)
        direction = TrendsCalculator.get_trend_direction(percentage_change)
        
        if change == 0:
            return f"{current} {direction.value}"
        
        sign = "+" if change > 0 else ""
        return f"{current} {direction.value} ({sign}{change}, {percentage_change:+.1f}%)"

    @staticmethod
    async def calculate_daily_trends(target_date: date) -> Dict[str, Any]:
        """Рассчитывает тренды для дневной статистики"""
        logger.debug(f"Calculating daily trends for {target_date}")
        
        # Текущая статистика
        current_stats = await StatisticsCollector.collect_daily_stats(target_date)
        
        # Статистика за предыдущий день
        previous_date = target_date - timedelta(days=1)
        previous_stats = await StatisticsCollector.collect_daily_stats(previous_date)
        
        # Статистика за прошлую неделю (тот же день недели)
        week_ago_date = target_date - timedelta(days=7)
        week_ago_stats = await StatisticsCollector.collect_daily_stats(week_ago_date)
        
        trends = {
            "current": current_stats,
            "previous_day": previous_stats,
            "week_ago": week_ago_stats,
            "trends": {}
        }
        
        # Тренды по сравнению с предыдущим днем
        trends["trends"]["vs_previous_day"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                previous_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                previous_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                previous_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                previous_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        # Тренды по сравнению с неделей назад
        trends["trends"]["vs_week_ago"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                week_ago_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                week_ago_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                week_ago_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                week_ago_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        return trends

    @staticmethod
    async def calculate_weekly_trends(end_date: date) -> Dict[str, Any]:
        """Рассчитывает тренды для недельной статистики"""
        logger.debug(f"Calculating weekly trends for week ending {end_date}")
        
        # Текущая неделя
        current_stats = await StatisticsCollector.collect_weekly_stats(end_date)
        
        # Предыдущая неделя
        previous_week_end = end_date - timedelta(days=7)
        previous_stats = await StatisticsCollector.collect_weekly_stats(previous_week_end)
        
        # Месяц назад (4 недели)
        month_ago_end = end_date - timedelta(days=28)
        month_ago_stats = await StatisticsCollector.collect_weekly_stats(month_ago_end)
        
        trends = {
            "current": current_stats,
            "previous_week": previous_stats,
            "month_ago": month_ago_stats,
            "trends": {}
        }
        
        # Тренды по сравнению с предыдущей неделей
        trends["trends"]["vs_previous_week"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                previous_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                previous_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                previous_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                previous_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        # Тренды по сравнению с месяцем назад
        trends["trends"]["vs_month_ago"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                month_ago_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                month_ago_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                month_ago_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                month_ago_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        return trends

    @staticmethod
    async def calculate_monthly_trends(target_month: int, target_year: int) -> Dict[str, Any]:
        """Рассчитывает тренды для месячной статистики"""
        logger.debug(f"Calculating monthly trends for {target_month}/{target_year}")
        
        # Текущий месяц
        current_stats = await StatisticsCollector.collect_monthly_stats(target_month, target_year)
        
        # Предыдущий месяц
        if target_month == 1:
            previous_month = 12
            previous_year = target_year - 1
        else:
            previous_month = target_month - 1
            previous_year = target_year
        
        previous_stats = await StatisticsCollector.collect_monthly_stats(previous_month, previous_year)
        
        # Прошлый год, тот же месяц
        year_ago_stats = await StatisticsCollector.collect_monthly_stats(target_month, target_year - 1)
        
        trends = {
            "current": current_stats,
            "previous_month": previous_stats,
            "year_ago": year_ago_stats,
            "trends": {}
        }
        
        # Тренды по сравнению с предыдущим месяцем
        trends["trends"]["vs_previous_month"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                previous_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                previous_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                previous_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                previous_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        # Тренды по сравнению с прошлым годом
        trends["trends"]["vs_year_ago"] = {
            "new_registrations": TrendsCalculator.format_trend(
                current_stats["new_registrations"], 
                year_ago_stats["new_registrations"], 
                "регистрации"
            ),
            "new_activations": TrendsCalculator.format_trend(
                current_stats["new_activations"], 
                year_ago_stats["new_activations"], 
                "активации"
            ),
            "payments_count": TrendsCalculator.format_trend(
                current_stats["payments_count"], 
                year_ago_stats["payments_count"], 
                "платежи"
            ),
            "new_paid_users": TrendsCalculator.format_trend(
                current_stats["new_paid_users"], 
                year_ago_stats["new_paid_users"], 
                "новые платные"
            )
        }
        
        return trends 