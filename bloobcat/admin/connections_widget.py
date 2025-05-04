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
class ConnectionDashboardWidgetAdmin(DashboardWidgetAdmin):
    title = "Подключения"
    dashboard_widget_type = DashboardWidgetType.ChartLine

    x_field = "date"
    x_field_filter_widget_type = WidgetType.DatePicker
    x_field_filter_widget_props: dict[str, str] = {"picker": "day"}  # noqa: RUF012
    x_field_periods = ["day", "week", "month", "year"]  # noqa: RUF012

    y_field = "count"

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

        # --- Находим самую раннюю дату подключения --- 
        min_conn_date_result = await conn.execute_query_dict('SELECT MIN("at") as min_date FROM connections')
        actual_start_date = min_x_field_date # По умолчанию начинаем с выбранной даты
        if min_conn_date_result and min_conn_date_result[0].get("min_date"):
            min_connection_db_val = min_conn_date_result[0]["min_date"]
            min_connection_dt_utc = None

            # Проверяем тип и конвертируем в datetime UTC
            if isinstance(min_connection_db_val, datetime.datetime):
                if min_connection_db_val.tzinfo is None:
                    # Если datetime без таймзоны, считаем что это UTC
                    min_connection_dt_utc = min_connection_db_val.replace(tzinfo=datetime.timezone.utc)
                else:
                    # Если datetime с таймзоной, конвертируем в UTC
                    min_connection_dt_utc = min_connection_db_val.astimezone(datetime.timezone.utc)
            elif isinstance(min_connection_db_val, datetime.date):
                # Если это date, конвертируем в datetime начала дня UTC
                min_connection_dt_utc = datetime.datetime.combine(min_connection_db_val, datetime.time.min).replace(tzinfo=datetime.timezone.utc)

            # Если удалось получить дату/время в UTC, сравниваем
            if min_connection_dt_utc:
                actual_start_date = max(min_x_field_date, min_connection_dt_utc)
        # -------------------------------------------

        if not period_x_field or period_x_field not in (
            self.x_field_periods or []
        ):
            period_x_field = "day"

        logging.warning(f"Executing widget {self.__class__.__name__}: period='{period_x_field}', start='{actual_start_date.isoformat()}', end='{max_x_field_date.isoformat()}'")

        results = await conn.execute_query_dict(
            """
                WITH date_series AS (
                    SELECT generate_series(
                        $2::timestamptz,  -- min_x_field_date (UTC)
                        $3::timestamptz,  -- max_x_field_date (UTC)
                        ('1 ' || $1)::interval -- period_x_field ('day', 'week', 'month', 'year')
                    ) AS report_timestamp
                )
                SELECT
                    to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
                    COUNT(c.id) AS count -- Считаем подключения за этот период
                FROM date_series ds
                LEFT JOIN connections c ON c."at" >= ds.report_timestamp
                                    AND c."at" < ds.report_timestamp + ('1 ' || $1)::interval -- Подключение внутри интервала
                GROUP BY ds.report_timestamp
                ORDER BY ds.report_timestamp;
            """,
            [period_x_field, actual_start_date, max_x_field_date],
        )
        # Убираем код, который пытался определить дату из результатов, т.к. generate_series теперь начинается с нужной даты
        # actual_min_date = min_x_field_date
        # if results: 
        #      try:
        #          first_date_str = results[0]['date']
        #          actual_min_date = datetime.datetime.strptime(first_date_str, '%d/%m/%Y').replace(tzinfo=datetime.timezone.utc)
        #      except (ValueError, KeyError, IndexError):
        #          pass 
        
        return {
            "results": results,
            "min_x_field": actual_start_date.isoformat(), # Возвращаем реальную стартовую дату
            "max_x_field": max_x_field_date.isoformat(),
            "period_x_field": period_x_field,
        }
