from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import TypeVar

DateLike = TypeVar("DateLike", date, datetime)


def add_months_safe(source: DateLike, months: int | float) -> DateLike:
    """Safely shifts a date or datetime by the given number of months.

    The day-of-month is clamped to the last valid day so ValueError is avoided.
    """
    if not isinstance(source, (date, datetime)):
        raise TypeError(f"add_months_safe expects date or datetime, got {type(source)!r}")

    months_int = int(months or 0)
    if months_int == 0:
        return source

    month_index = source.month - 1 + months_int
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    days_in_month = calendar.monthrange(year, month)[1]
    day = min(source.day, days_in_month)

    return source.replace(year=year, month=month, day=day)  # type: ignore[return-value]


__all__ = ["add_months_safe"]

