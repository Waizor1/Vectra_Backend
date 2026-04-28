"""Test-only sqlite date/datetime adapter compatibility for Python 3.12+.

This module explicitly registers sqlite adapters/converters for ``date`` and
``datetime`` values using ISO serialization/parsing to avoid relying on
deprecated implicit defaults.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from threading import Lock

__all__ = ["register_sqlite_datetime_compat"]


_REGISTRATION_LOCK = Lock()
_SQLITE_DATETIME_COMPAT_REGISTERED = False


def _adapt_date(value: date) -> str:
    return value.isoformat()


def _adapt_datetime(value: datetime) -> str:
    return value.isoformat()


def _parse_iso_date(value: bytes) -> date:
    return date.fromisoformat(value.decode("utf-8"))


def _parse_iso_datetime(value: bytes) -> datetime:
    iso_value = value.decode("utf-8")
    if iso_value.endswith("Z"):
        iso_value = f"{iso_value[:-1]}+00:00"
    return datetime.fromisoformat(iso_value)


def register_sqlite_datetime_compat() -> None:
    """Register explicit sqlite date/datetime adapters and converters once."""

    global _SQLITE_DATETIME_COMPAT_REGISTERED

    if _SQLITE_DATETIME_COMPAT_REGISTERED:
        return

    with _REGISTRATION_LOCK:
        if _SQLITE_DATETIME_COMPAT_REGISTERED:
            return

        sqlite3.register_adapter(date, _adapt_date)
        sqlite3.register_adapter(datetime, _adapt_datetime)
        sqlite3.register_converter("DATE", _parse_iso_date)
        sqlite3.register_converter("DATETIME", _parse_iso_datetime)
        sqlite3.register_converter("TIMESTAMP", _parse_iso_datetime)

        _SQLITE_DATETIME_COMPAT_REGISTERED = True
