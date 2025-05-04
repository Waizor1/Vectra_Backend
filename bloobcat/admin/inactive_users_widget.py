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
            max_x_field_date = datetime.datetime.now(
                tz=datetime.UTC
            ) + datetime.timedelta(days=1)
        else:
            max_x_field_date = datetime.datetime.fromisoformat(max_x_field)

        if not period_x_field or period_x_field not in (
            self.x_field_periods or []
        ):
            period_x_field = "day"

        results = await conn.execute_query_dict(
            """
                WITH daily AS (
                    SELECT
                        date_trunc($1, registration_date)::date AS day,
                        COUNT(*) AS daily_total,
                        COUNT(*) FILTER (WHERE expired_at > date_trunc($1, registration_date)::date AND is_registered = TRUE) AS daily_active
                    FROM users
                    WHERE registration_date >= $2 AND registration_date <= $3
                    GROUP BY date_trunc($1, registration_date)::date
                )
                SELECT
                    to_char(day, 'DD/MM/YYYY') AS date,
                    SUM(daily_total - daily_active) OVER (ORDER BY day) AS count
                FROM daily
                ORDER BY day
            """,
            [period_x_field, min_x_field_date, max_x_field_date],
        )
        return {
            "results": results,
            "min_x_field": min_x_field_date.isoformat(),
            "max_x_field": max_x_field_date.isoformat(),
            "period_x_field": period_x_field,
        } 