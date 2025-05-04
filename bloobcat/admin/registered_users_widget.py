import datetime
import logging

from fastadmin import (
    DashboardWidgetAdmin,
    DashboardWidgetType,
    WidgetType,
    register_widget,
)
from tortoise import Tortoise


@register_widget
class RegisteredUsersDashboardWidgetAdmin(DashboardWidgetAdmin):
    title = "Зарегистрировалось"
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

        # Определяем начальную и конечную даты из параметров или по умолчанию
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

        if not period_x_field or period_x_field not in self.x_field_periods:
            period_x_field = "day"

        # --- Находим самую раннюю дату регистрации, чтобы обрезать начало --- 
        min_reg_date_result = await conn.execute_query_dict("SELECT MIN(registration_date) as min_date FROM users")
        actual_start_date = min_x_field_date # По умолчанию начинаем с выбранной даты
        if min_reg_date_result and min_reg_date_result[0]["min_date"]:
            min_registration_date_db = min_reg_date_result[0]["min_date"]
            if min_registration_date_db.tzinfo is None:
                min_registration_date_db = min_registration_date_db.replace(tzinfo=datetime.timezone.utc)
            else:
                min_registration_date_db = min_registration_date_db.astimezone(datetime.timezone.utc)
            # Вычисляем "сырую" стартовую дату
            actual_start_date_raw = max(min_x_field_date, min_registration_date_db)
            # Округляем до начала UTC дня
            actual_start_date = datetime.datetime.combine(actual_start_date_raw.date(), datetime.time.min).replace(tzinfo=datetime.timezone.utc)
        # else:
        #     actual_start_date = min_x_field_date # Оставляем выбранную, если в БД нет дат
        # ----------------------------------------------------------------------

        logging.debug(f"Executing widget {self.__class__.__name__}: period='{period_x_field}', start='{actual_start_date.isoformat()}', end='{max_x_field_date.isoformat()}'")

        # Ensure generate_series includes the last day
        query_end_date = max_x_field_date + datetime.timedelta(seconds=1)

        results = await conn.execute_query_dict(
            """
                WITH date_series AS (
                    SELECT generate_series(
                        $2::timestamptz,  -- actual_start_date (UTC)
                        $3::timestamptz,  -- max_x_field_date (UTC)
                        ('1 ' || $1)::interval -- period_x_field
                    ) AS report_timestamp
                )
                SELECT
                    to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
                    COUNT(u.id) AS count -- Считаем регистрации за этот период
                FROM date_series ds
                LEFT JOIN users u ON u.registration_date >= ds.report_timestamp
                               AND u.registration_date < ds.report_timestamp + ('1 ' || $1)::interval
                GROUP BY ds.report_timestamp
                ORDER BY ds.report_timestamp;
            """,
            [period_x_field, actual_start_date, query_end_date],
        )
        logging.debug(f"Widget {self.__class__.__name__} results for end='{max_x_field_date.isoformat()}': {results}")
        return {
            "results": results,
            "min_x_field": actual_start_date.isoformat(), # Используем ту же дату, что и для запроса
            "max_x_field": max_x_field_date.isoformat(),
            "period_x_field": period_x_field,
        } 