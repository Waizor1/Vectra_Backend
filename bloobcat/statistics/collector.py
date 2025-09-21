"""
Модуль сбора статистических данных из базы данных
"""

from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from tortoise import Tortoise

from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.connections import Connections  # Добавляем импорт
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
        query = """
            SELECT COUNT(*) as count FROM (
                SELECT MIN(at) as first_at
                FROM connections
                GROUP BY user_id
            ) as first_connections
            WHERE first_at = $1
        """
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query_dict(query, [target_date])
        new_activations = result[0]['count'] if result else 0
        
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

        # Пользователи с активным автосписанием
        auto_renewal_users = await Users.filter(
            expired_at__gte=target_date,
            is_registered=True,
            is_subscribed=True,
            renew_id__isnull=False
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
        
        # Онлайн-статистика за день
        online_stats = await StatisticsCollector.get_daily_online_stats(target_date)
        
        return {
            "date": target_date,
            "new_registrations": new_registrations,
            "new_activations": new_activations,
            "payments_count": payments_count,
            "payments_sum": payments_sum,
            "new_paid_users": new_paid_users,
            "active_users": active_users,
            "auto_renewal_users": auto_renewal_users,
            "online_users": online_stats["unique_online_users"],
            "total_connections": online_stats["total_connections"]
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
        
        # Активации ключей (пользователи, которые впервые подключились за неделю)
        query = """
            SELECT COUNT(*) as count FROM (
                SELECT MIN(at) as first_at
                FROM connections
                GROUP BY user_id
            ) as first_connections
            WHERE first_at >= $1 AND first_at <= $2
        """
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query_dict(query, [start_date, end_date])
        new_activations = result[0]['count'] if result else 0
        
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

        auto_renewal_users = await Users.filter(
            expired_at__gte=end_date,
            is_registered=True,
            is_subscribed=True,
            renew_id__isnull=False
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
        
        # Онлайн-статистика за неделю
        online_stats = await StatisticsCollector.get_weekly_online_stats(end_date)
        
        return {
            "start_date": start_date,
            "end_date": end_date,
            "new_registrations": new_registrations,
            "new_activations": new_activations,
            "payments_count": payments_count,
            "payments_sum": payments_sum,
            "new_paid_users": new_paid_users,
            "active_users": active_users,
            "auto_renewal_users": auto_renewal_users,
            "online_users": online_stats["unique_online_users"],
            "avg_daily_online": online_stats["avg_daily_online"],
            "total_connections": online_stats["total_connections"]
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
        
        # Активации ключей (пользователи, которые впервые подключились за месяц)
        query = """
            SELECT COUNT(*) as count FROM (
                SELECT MIN(at) as first_at
                FROM connections
                GROUP BY user_id
            ) as first_connections
            WHERE first_at >= $1 AND first_at <= $2
        """
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query_dict(query, [start_date, end_date])
        new_activations = result[0]['count'] if result else 0
        
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

        auto_renewal_users = await Users.filter(
            expired_at__gte=end_date,
            is_registered=True,
            is_subscribed=True,
            renew_id__isnull=False
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
        
        # Онлайн-статистика за месяц
        online_stats = await StatisticsCollector.get_monthly_online_stats(target_month, target_year)
        
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
            "active_users": active_users,
            "auto_renewal_users": auto_renewal_users,
            "online_users": online_stats["unique_online_users"],
            "avg_daily_online": online_stats["avg_daily_online"],
            "total_connections": online_stats["total_connections"]
        }

    @staticmethod
    async def get_online_stats_for_period(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
        """Получает онлайн-статистику за указанный период"""
        logger.debug(f"Collecting online stats for {start_dt} to {end_dt}")
        
        # Получаем уникальных пользователей, которые были онлайн в период
        query = """
            SELECT COUNT(DISTINCT user_id) as unique_users
            FROM connections
            WHERE at >= $1 AND at < $2
        """
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query_dict(query, [start_dt, end_dt])
        unique_online_users = result[0]['unique_users'] if result else 0
        
        # Получаем общее количество подключений за период
        total_connections = await Connections.filter(
            at__gte=start_dt,
            at__lt=end_dt
        ).count()
        
        return {
            "unique_online_users": unique_online_users,
            "total_connections": total_connections
        }

    @staticmethod
    async def get_daily_online_stats(target_date: date) -> Dict[str, Any]:
        """Получает дневную онлайн-статистику"""
        start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=MOSCOW)
        
        return await StatisticsCollector.get_online_stats_for_period(start_dt, end_dt)

    @staticmethod
    async def get_weekly_online_stats(end_date: date) -> Dict[str, Any]:
        """Получает недельную онлайн-статистику (средний дневной онлайн)"""
        start_date = end_date - timedelta(days=6)
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=MOSCOW)
        
        # Получаем статистику за всю неделю
        weekly_stats = await StatisticsCollector.get_online_stats_for_period(start_dt, end_dt)
        
        # Вычисляем средний дневной онлайн
        days_count = 7
        avg_daily_online = weekly_stats["unique_online_users"] / days_count if days_count > 0 else 0
        
        return {
            **weekly_stats,
            "avg_daily_online": round(avg_daily_online, 1),
            "days_count": days_count
        }

    @staticmethod
    async def get_monthly_online_stats(target_month: int, target_year: int) -> Dict[str, Any]:
        """Получает месячную онлайн-статистику (средний дневной онлайн)"""
        # Определяем границы месяца
        start_date = date(target_year, target_month, 1)
        if target_month == 12:
            next_month_start = date(target_year + 1, 1, 1)
        else:
            next_month_start = date(target_year, target_month + 1, 1)
        end_date = next_month_start - timedelta(days=1)
        
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=MOSCOW)
        end_dt = datetime.combine(next_month_start, datetime.min.time()).replace(tzinfo=MOSCOW)
        
        # Получаем статистику за весь месяц
        monthly_stats = await StatisticsCollector.get_online_stats_for_period(start_dt, end_dt)
        
        # Вычисляем количество дней в месяце
        days_count = (end_date - start_date).days + 1
        avg_daily_online = monthly_stats["unique_online_users"] / days_count if days_count > 0 else 0
        
        return {
            **monthly_stats,
            "avg_daily_online": round(avg_daily_online, 1),
            "days_count": days_count
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

        auto_renewal_users = await Users.filter(
            expired_at__gte=today,
            is_registered=True,
            is_subscribed=True,
            renew_id__isnull=False
        ).count()

        return {
            "total_users": total_users,
            "registered_users": registered_users,
            "active_users": active_users,
            "total_payments": total_payments,
            "total_revenue": total_revenue,
            "auto_renewal_users": auto_renewal_users
        }
