"""
Модуль форматирования отчетов статистики для отправки в Telegram
"""

from datetime import date, datetime
from typing import Dict, Any
from zoneinfo import ZoneInfo

from bloobcat.logger import get_logger

logger = get_logger("statistics_formatter")

MOSCOW = ZoneInfo("Europe/Moscow")

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

WEEKDAY_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
    4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}


class StatisticsFormatter:
    """Класс для форматирования отчетов статистики"""

    @staticmethod
    def format_currency(amount: float) -> str:
        """Форматирует сумму денег"""
        return f"{amount:,.0f}₽".replace(",", " ")

    @staticmethod
    def format_date_ru(target_date: date) -> str:
        """Форматирует дату на русском языке"""
        weekday = WEEKDAY_NAMES[target_date.weekday()]
        month = MONTH_NAMES[target_date.month]
        return f"{weekday}, {target_date.day} {month} {target_date.year}"

    @staticmethod
    def format_daily_report(trends_data: Dict[str, Any]) -> str:
        """Форматирует дневной отчет статистики"""
        current = trends_data["current"]
        trends_prev = trends_data["trends"]["vs_previous_day"]
        trends_week = trends_data["trends"]["vs_week_ago"]
        
        target_date = current["date"]
        formatted_date = StatisticsFormatter.format_date_ru(target_date)
        timestamp = datetime.now(MOSCOW).strftime("%H:%M")
        
        report = f"""📊 <b>Дневная статистика</b>
🗓 {formatted_date}
⏰ Отчет сформирован в {timestamp} МСК

<b>📈 Основные показатели:</b>
👥 Новые регистрации: {current['new_registrations']}
✅ Активации ключей: {current['new_activations']}
💰 Платежи: {current['payments_count']} на {StatisticsFormatter.format_currency(current['payments_sum'])}
💎 Новые платные: {current['new_paid_users']}
🔥 Активные пользователи: {current['active_users']}
🔄 Автосписание активно: {current.get('auto_renewal_users', 0)}
🟢 Онлайн за день: {current.get('online_users', 0)}

<b>📊 Тренды (vs вчера):</b>
👥 Регистрации: {trends_prev['new_registrations']}
✅ Активации: {trends_prev['new_activations']}
💰 Платежи: {trends_prev['payments_count']}
💎 Новые платные: {trends_prev['new_paid_users']}

<b>📈 Тренды (vs неделю назад):</b>
👥 Регистрации: {trends_week['new_registrations']}
✅ Активации: {trends_week['new_activations']}
💰 Платежи: {trends_week['payments_count']}
💎 Новые платные: {trends_week['new_paid_users']}

#статистика #день #{target_date.strftime('%d_%m_%Y')}"""
        
        return report

    @staticmethod
    def format_weekly_report(trends_data: Dict[str, Any]) -> str:
        """Форматирует недельный отчет статистики"""
        current = trends_data["current"]
        trends_prev = trends_data["trends"]["vs_previous_week"]
        trends_month = trends_data["trends"]["vs_month_ago"]
        
        start_date = current["start_date"]
        end_date = current["end_date"]
        timestamp = datetime.now(MOSCOW).strftime("%H:%M")
        
        report = f"""📊 <b>Недельная статистика</b>
🗓 {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m.%Y')}
⏰ Отчет сформирован в {timestamp} МСК

<b>📈 Основные показатели:</b>
👥 Новые регистрации: {current['new_registrations']}
✅ Активации ключей: {current['new_activations']}
💰 Платежи: {current['payments_count']} на {StatisticsFormatter.format_currency(current['payments_sum'])}
💎 Новые платные: {current['new_paid_users']}
🔥 Активные пользователи: {current['active_users']}
🔄 Автосписание активно: {current.get('auto_renewal_users', 0)}
🟢 Онлайн за неделю: {current.get('online_users', 0)}
📊 Средний дневной онлайн: {current.get('avg_daily_online', 0)}

<b>📊 Тренды (vs прошлую неделю):</b>
👥 Регистрации: {trends_prev['new_registrations']}
✅ Активации: {trends_prev['new_activations']}
💰 Платежи: {trends_prev['payments_count']}
💎 Новые платные: {trends_prev['new_paid_users']}

<b>📈 Тренды (vs месяц назад):</b>
👥 Регистрации: {trends_month['new_registrations']}
✅ Активации: {trends_month['new_activations']}
💰 Платежи: {trends_month['payments_count']}
💎 Новые платные: {trends_month['new_paid_users']}

#статистика #неделя #неделя_{end_date.strftime('%d_%m_%Y')}"""
        
        return report

    @staticmethod
    def format_monthly_report(trends_data: Dict[str, Any]) -> str:
        """Форматирует месячный отчет статистики"""
        current = trends_data["current"]
        trends_prev = trends_data["trends"]["vs_previous_month"]
        trends_year = trends_data["trends"]["vs_year_ago"]
        
        month_name = MONTH_NAMES[current["month"]]
        year = current["year"]
        timestamp = datetime.now(MOSCOW).strftime("%H:%M")
        
        report = f"""📊 <b>Месячная статистика</b>
🗓 {month_name} {year}
⏰ Отчет сформирован в {timestamp} МСК

<b>📈 Основные показатели:</b>
👥 Новые регистрации: {current['new_registrations']}
✅ Активации ключей: {current['new_activations']}
💰 Платежи: {current['payments_count']} на {StatisticsFormatter.format_currency(current['payments_sum'])}
💎 Новые платные: {current['new_paid_users']}
🔥 Активные пользователи: {current['active_users']}
🔄 Автосписание активно: {current.get('auto_renewal_users', 0)}
🟢 Онлайн за месяц: {current.get('online_users', 0)}
📊 Средний дневной онлайн: {current.get('avg_daily_online', 0)}

<b>📊 Тренды (vs прошлый месяц):</b>
👥 Регистрации: {trends_prev['new_registrations']}
✅ Активации: {trends_prev['new_activations']}
💰 Платежи: {trends_prev['payments_count']}
💎 Новые платные: {trends_prev['new_paid_users']}

<b>📈 Тренды (vs год назад):</b>
👥 Регистрации: {trends_year['new_registrations']}
✅ Активации: {trends_year['new_activations']}
💰 Платежи: {trends_year['payments_count']}
💎 Новые платные: {trends_year['new_paid_users']}

#статистика #месяц #{month_name.lower()}_{year}"""
        
        return report

    @staticmethod
    def format_test_report(stats_data: Dict[str, Any], period_type: str = "test") -> str:
        """Форматирует тестовый отчет для админских команд"""
        timestamp = datetime.now(MOSCOW).strftime("%H:%M")
        
        if period_type == "daily":
            target_date = stats_data["date"]
            formatted_date = StatisticsFormatter.format_date_ru(target_date)
            title = f"📊 <b>Тестовая дневная статистика</b>\n🗓 {formatted_date}"
        elif period_type == "weekly":
            start_date = stats_data["start_date"]
            end_date = stats_data["end_date"]
            title = f"📊 <b>Тестовая недельная статистика</b>\n🗓 {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m.%Y')}"
        elif period_type == "monthly":
            month_name = MONTH_NAMES[stats_data["month"]]
            year = stats_data["year"]
            title = f"📊 <b>Тестовая месячная статистика</b>\n🗓 {month_name} {year}"
        else:
            title = "📊 <b>Тестовая статистика</b>"
        
        report = f"""{title}
⏰ Тест выполнен в {timestamp} МСК

<b>📈 Показатели:</b>
👥 Новые регистрации: {stats_data['new_registrations']}
✅ Активации ключей: {stats_data['new_activations']}
💰 Платежи: {stats_data['payments_count']} на {StatisticsFormatter.format_currency(stats_data['payments_sum'])}
💎 Новые платные: {stats_data['new_paid_users']}
🔥 Активные пользователи: {stats_data['active_users']}
🔄 Автосписание активно: {stats_data.get('auto_renewal_users', 0)}

#тест #статистика"""
        
        return report 
