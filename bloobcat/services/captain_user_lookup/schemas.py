from __future__ import annotations

from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, Field


class ActiveSubscription(BaseModel):
    """Модель активной подписки или покупки."""

    name: str = Field(..., description="Человекочитаемое название продукта")
    status: str = Field(..., description="Состояние подписки / покупки")
    months: int | None = Field(
        default=None, description="Срок подписки в месяцах, если доступен"
    )
    price: float | None = Field(
        default=None, description="Цена в валюте проекта, если доступна"
    )
    started_at: datetime | None = Field(
        default=None, description="Дата активации подписки"
    )
    expires_at: datetime | None = Field(
        default=None, description="Дата истечения подписки"
    )


class CaptainUserProfile(BaseModel):
    """Ответ Captain User Lookup."""

    telegram_id: int
    first_name: str
    last_name: str
    username: str
    email: str
    phone: str | None
    country: str | None
    status: str
    active_subscriptions: Sequence[ActiveSubscription]
    balance: float
    registered_at: datetime
    last_login: datetime
    remnawave: RemnaWaveSnapshot | None = None


class RemnaWaveSnapshot(BaseModel):
    uuid: str
    username: str | None = None
    status: str | None = None
    expire_at: datetime | None = None
    online_at: datetime | None = None
    hwid_limit: int | None = None
    traffic_limit_bytes: int | None = None
    subscription_url: str | None = None
    telegram_id: int | None = None
    email: str | None = None
    active_internal_squads: Sequence[str] | None = None


class ErrorResponse(BaseModel):
    error: str
