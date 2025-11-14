from __future__ import annotations

from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, Field


class ActiveSubscription(BaseModel):
    """Модель активной подписки или покупки."""

    name: str = Field(..., description="Человекочитаемое название продукта")
    status: str = Field(..., description="Состояние подписки")
    started_at: datetime = Field(..., description="ISO дата старта")
    expires_at: datetime | None = Field(
        default=None, description="ISO дата истечения, если применимо"
    )


class CaptainUserProfile(BaseModel):
    """Ответ Captain User Lookup."""

    telegram_id: int
    first_name: str
    last_name: str
    username: str
    email: str
    phone: str
    country: str
    status: str
    active_subscriptions: Sequence[ActiveSubscription]
    balance: float
    registered_at: datetime
    last_login: datetime


class ErrorResponse(BaseModel):
    error: str
