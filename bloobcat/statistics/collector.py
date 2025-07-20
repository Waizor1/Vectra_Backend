"""
Модуль сбора статистических данных из базы данных
"""

from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.logger import get_logger

logger = get_logger("statistics_collector")

MOSCOW = ZoneInfo("Europe/Moscow")


class StatisticsCollector:
    """Класс для сбора статистических данных"""

    @staticmethod
    async def collect_daily_stats(target_date: date) -> Dict[str, Any]:
        """Собирает статистику за указанный день"""
        logger.debug(f"Collecting daily stats for {target_date}")
        
        # Определяем временные границы дня в московском времени
        start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=MOSCOW)
        
        # Новые регистрации
        new_registrations = await Users.filter(
            registration_date__gte=start_dt,
            registration_date__lt=end_dt
        ).count()
        
        # Активации ключей (пользователи, которые впервые подключились)
        new_activations = await Users.filter(
            connected_at__gte=start_dt,
            connected_at__lt=end_dt
        ).count()
        
        # Платежи
        payments = await ProcessedPayments.filter(
            processed_at__gte=start_dt,
            processed_at__lt=end_dt,
            status="succeeded"
        )
        
        payments_count = len(payments)
        payments_sum = sum(float(payment.amount) for payment in payments)
        
        # Активные пользователи (с активной подпиской на конец дня)
        active_users = await Users.filter(
            expired_at__gte=target_date,
            is_registered=True
        ).count()
        
        # Новые платные подписки (пользователи, впервые оплатившие)
        new_paid_users = 0
        for payment in payments:
            # Проверяем, первый ли это платеж пользователя
            first_payment = await ProcessedPayments.filter(
                user_id=payment.user_id,
                status="succeeded"
            ).order_by("processed_at").first()
            
            if first_payment and first_payment.id == payment.id:
                new_paid_users += 1
        
        return {
            "date": target_date,
            "new_registrations": new_registrations,
            "new_activations": new_activations,
            "payments_count": payments_count,
            "payments_sum": payments_sum,
            "new_paid_users": new_paid_users,
            "active_users": active_users
        }

    @staticmethod
    async def collect_weekly_stats(end_date: date) -> Dict[str, Any]:
        """Собирает статистику за неделю (7 дней включая end_date)"""
        start_date = end_date - timedelta(days=6)
        logger.debug(f"Collecting weekly stats for {start_date} to {end_date}")
        
        # Определяем временные границы недели в московском времени
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=MOSCOW)
        
        # Аналогично дневной статистике, но за неделю
        new_registrations = await Users.filter(
            registration_date__gte=start_dt,
            registration_date__lt=end_dt
        ).count()
        
        new_activations = await Users.filter(
            connected_at__gte=start_dt,
            connected_at__lt=end_dt
        ).count()
        
        payments = await ProcessedPayments.filter(
            processed_at__gte=start_dt,
            processed_at__lt=end_dt,
            status="succeeded"
        )
        
        payments_count = len(payments)
        payments_sum = sum(float(payment.amount) for payment in payments)
        
        active_users = await Users.filter(
            expired_at__gte=end_date,
            is_registered=True
        ).count()
        
        # Новые платные подписки за неделю
        new_paid_users = 0
        for payment in payments:
            first_payment = await ProcessedPayments.filter(
                user_id=payment.user_id,
                status="succeeded"
            ).order_by("processed_at").first()
            
            if first_payment and first_payment.id == payment.id:
                new_paid_users += 1
        
        return {
            "start_date": start_date,
            "end_date": end_date,
            "new_registrations": new_registrations,
            "new_activations": new_activations,
            "payments_count": payments_count,
            "payments_sum": payments_sum,
            "new_paid_users": new_paid_users,
            "active_users": active_users
        }

    @staticmethod
    async def collect_monthly_stats(target_month: int, target_year: int) -> Dict[str, Any]:
        """Собирает статистику за указанный месяц"""
        logger.debug(f"Collecting monthly stats for {target_month}/{target_year}")
        
        # Определяем границы месяца
        start_date = date(target_year, target_month, 1)
        if target_month == 12:
            next_month_start = date(target_year + 1, 1, 1)
        else:
            next_month_start = date(target_year, target_month + 1, 1)
        end_date = next_month_start - timedelta(days=1)
        
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(next_month_start, datetime.min.time()).replace(tzinfo=MOSCOW)
        
        # Аналогично дневной статистике, но за месяц
        new_registrations = await Users.filter(
            registration_date__gte=start_dt,
            registration_date__lt=end_dt
        ).count()
        
        new_activations = await Users.filter(
            connected_at__gte=start_dt,
            connected_at__lt=end_dt
        ).count()
        
        payments = await ProcessedPayments.filter(
            processed_at__gte=start_dt,
            processed_at__lt=end_dt,
            status="succeeded"
        )
        
        payments_count = len(payments)
        payments_sum = sum(float(payment.amount) for payment in payments)
        
        active_users = await Users.filter(
            expired_at__gte=end_date,
            is_registered=True
        ).count()
        
        # Новые платные подписки за месяц
        new_paid_users = 0
        for payment in payments:
            first_payment = await ProcessedPayments.filter(
                user_id=payment.user_id,
                status="succeeded"
            ).order_by("processed_at").first()
            
            if first_payment and first_payment.id == payment.id:
                new_paid_users += 1
        
        return {
            "start_date": start_date,
            "end_date": end_date,
            "month": target_month,
            "year": target_year,
            "new_registrations": new_registrations,
            "new_activations": new_activations,
            "payments_count": payments_count,
            "payments_sum": payments_sum,
            "new_paid_users": new_paid_users,
            "active_users": active_users
        }

    @staticmethod
    async def get_total_stats() -> Dict[str, Any]:
        """Получает общую статистику"""
        total_users = await Users.all().count()
        registered_users = await Users.filter(is_registered=True).count()
        
        # Активные пользователи (с действующей подпиской)
        today = date.today()
        active_users = await Users.filter(
            expired_at__gte=today,
            is_registered=True
        ).count()
        
        # Всего платежей
        total_payments = await ProcessedPayments.filter(status="succeeded").count()
        total_revenue = sum(
            float(payment.amount) 
            for payment in await ProcessedPayments.filter(status="succeeded")
        )
        
        return {
            "total_users": total_users,
            "registered_users": registered_users,
            "active_users": active_users,
            "total_payments": total_payments,
            "total_revenue": total_revenue
        } 