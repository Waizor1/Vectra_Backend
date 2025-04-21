import datetime

from fastadmin import (
    DashboardWidgetAdmin,
    DashboardWidgetType,
    WidgetType,
    register_widget,
)
from tortoise import Tortoise


@register_widget
class TotalUsersDashboardWidgetAdmin(DashboardWidgetAdmin):
    title = "Всего пользователей"
    dashboard_widget_type = DashboardWidgetType.ChartLine

    x_field = "date"
    y_field = "count"

    async def get_data(
        self,
        min_x_field: str | None = None,
        max_x_field: str | None = None,
        period_x_field: str | None = None,
    ) -> dict:
        conn = Tortoise.get_connection("default")
        
        sql_query = """
            WITH user_counts AS (
                SELECT 
                    registration_date::date AS dt,
                    COUNT(*) OVER (ORDER BY registration_date::date) AS count
                FROM users
                ORDER BY dt
            )
            SELECT 
                to_char(dt, 'DD/MM/YYYY') AS date,
                count
            FROM user_counts
        """
        
        results = await conn.execute_query_dict(sql_query, [])
        
        return {
            "results": results,
            "min_x_field": "2023-01-01",
            "max_x_field": "2025-12-31",
            "period_x_field": "day",
        } 