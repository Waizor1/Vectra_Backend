import datetime

from fastadmin import (
    DashboardWidgetAdmin,
    DashboardWidgetType,
    WidgetType,
    register_widget,
)
from tortoise import Tortoise


@register_widget
class InactiveUsersDashboardWidgetAdmin(DashboardWidgetAdmin):
    title = "Неактивные пользователи"
    dashboard_widget_type = DashboardWidgetType.ChartLine

    x_field = "date"
    y_field = "count"
    x_field_filter_widget_type = WidgetType.DatePicker
    x_field_filter_widget_props: dict[str, str] = {"picker": "day"}  # noqa: RUF012
    x_field_periods = ["day", "week", "month", "year"]  # noqa: RUF012

    async def get_data(
        self,
        min_x_field: str | None = None,
        max_x_field: str | None = None,
        period_x_field: str | None = None,
    ) -> dict:
        conn = Tortoise.get_connection("default")

        if not min_x_field:
            min_x_field_date = datetime.datetime.now(
                tz=datetime.UTC
            ) - datetime.timedelta(days=360)
        else:
            min_x_field_date = datetime.datetime.fromisoformat(min_x_field)
        if not max_x_field:
            # По умолчанию берем СЕГОДНЯШНИЙ день (UTC)
            max_x_field_date = datetime.datetime.now(tz=datetime.UTC)
        else:
            max_x_field_date = datetime.datetime.fromisoformat(max_x_field)

        # Приводим даты к UTC
        min_x_field_date = min_x_field_date.astimezone(datetime.timezone.utc)
        max_x_field_date = max_x_field_date.astimezone(datetime.timezone.utc)

        # --- Находим самую раннюю дату регистрации --- 
        min_reg_date_result = await conn.execute_query_dict("SELECT MIN(registration_date) as min_date FROM users")
        actual_start_date = min_x_field_date # По умолчанию начинаем с выбранной даты
        if min_reg_date_result and min_reg_date_result[0]["min_date"]:
            min_registration_date_db = min_reg_date_result[0]["min_date"]
            if min_registration_date_db.tzinfo is None:
                min_registration_date_db = min_registration_date_db.replace(tzinfo=datetime.timezone.utc)
            else:
                min_registration_date_db = min_registration_date_db.astimezone(datetime.timezone.utc)
            actual_start_date = max(min_x_field_date, min_registration_date_db)
        # ---------------------------------------------

        if not period_x_field or period_x_field not in (
            self.x_field_periods or []
        ):
            period_x_field = "day"

        results = await conn.execute_query_dict(
            """
                WITH date_series AS (
                    SELECT generate_series(
                        $2::timestamptz,  -- min_x_field_date (UTC)
                        $3::timestamptz,  -- max_x_field_date (UTC)
                        ('1 ' || $1)::interval -- period_x_field ('day', 'week', 'month', 'year')
                    ) AS report_timestamp -- Используем timestamptz
                )
                SELECT
                    to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date, -- Приводим к дате только для отображения
                    -- Считаем пользователей, которые были зарегистрированы к этой дате,
                    -- но чья подписка истекла до или в эту дату (сравниваем date)
                    COUNT(u.id) AS count
                FROM date_series ds
                LEFT JOIN users u ON u.registration_date <= ds.report_timestamp
                                 AND u.expired_at <= ds.report_timestamp::date -- Сравниваем DATE с DATE
                                 AND u.is_registered = TRUE
                GROUP BY ds.report_timestamp -- Группируем по полному timestamp
                ORDER BY ds.report_timestamp;
            """,
            [period_x_field, actual_start_date, max_x_field_date],
        )
        return {
            "results": results,
            "min_x_field": actual_start_date.isoformat(),
            "max_x_field": max_x_field_date.isoformat(),
            "period_x_field": period_x_field,
        } 