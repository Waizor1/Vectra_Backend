from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, date
from typing import Any, Dict, Optional

from pydantic import BaseModel as FastAdminBaseModel, Field, field_validator, model_validator
from tortoise import fields, models


class PromoBatch(models.Model):
    """Группа промокодов для удобства управления и аудита."""
    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=255, description="Название партии/кампании")
    notes = fields.TextField(null=True, description="Заметки/описание партии")
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by: fields.ForeignKeyNullableRelation["Admin"] = fields.ForeignKeyField(
        "models.Admin", null=True, related_name="promo_batches", on_delete=fields.SET_NULL
    )

    class Meta:
        table = "promo_batches"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.title} ({self.id})"


class PromoCode(models.Model):
    """
    Промокод. Храним только HMAC от исходного текста кода (code_hmac), сам код нигде не сохраняется.
    Эффекты кодируются в JSON, чтобы можно было добавлять новые типы без миграций.
    """

    id = fields.IntField(primary_key=True)
    batch: fields.ForeignKeyNullableRelation[PromoBatch] = fields.ForeignKeyField(
        "models.PromoBatch", related_name="codes", null=True, on_delete=fields.SET_NULL
    )

    # Человеко-читаемое имя промокода (не сам код), показывается в админке и списках
    name = fields.CharField(max_length=255, null=True, description="Имя промокода")

    code_hmac = fields.CharField(
        max_length=128,
        unique=True,
        description="Введите исходный промокод; HMAC будет сгенерирован автоматически (или вставьте готовый 64-символьный hex)"
    )

    # Гибкие эффекты, например: {"extend_days": 30, "discount_percent": 20, "add_hwid": 1, "one_time": true}
    effects: Dict[str, Any] = fields.JSONField(default=dict)

    max_activations = fields.IntField(default=1, description="Максимум активаций для этого промокода (всего)")
    per_user_limit = fields.IntField(default=1, description="Сколько раз один и тот же пользователь может активировать этот код")

    expires_at = fields.DateField(null=True, description="Дата истечения действия кода (включительно)")
    disabled = fields.BooleanField(default=False, description="Принудительное отключение")

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "promo_codes"

    def __str__(self) -> str:  # pragma: no cover
        return f"PromoCode {self.id} (batch={self.batch_id})"

    @staticmethod
    def is_expired(expires_at: Optional[date]) -> bool:
        if not expires_at:
            return False
        # Считаем истекшим, если сегодня позже даты истечения
        return date.today() > expires_at


class PromoUsage(models.Model):
    """Факт использования промокода конкретным пользователем с произвольным контекстом."""

    id = fields.IntField(primary_key=True)
    promo_code: fields.ForeignKeyRelation[PromoCode] = fields.ForeignKeyField(
        "models.PromoCode", related_name="usages", on_delete=fields.CASCADE
    )
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users", related_name="promo_usages", on_delete=fields.CASCADE
    )

    used_at = fields.DatetimeField(auto_now_add=True)
    context: Dict[str, Any] = fields.JSONField(default=dict, description="Доп. контекст применения (например payment_id)")

    class Meta:
        table = "promo_usages"
        indexes = ("promo_code", "user",)


# ------------------ Custom Schemas ------------------
class PromoCodeCreateSchema(FastAdminBaseModel):
    """Схема для создания промокода через админ-панель"""
    batch_id: Optional[int] = Field(None, description="ID партии промокодов")
    name: Optional[str] = Field(None, description="Имя промокода для отображения")
    raw_code: str = Field(..., description="Исходный промокод (будет захеширован)")
    effects: Dict[str, Any] = Field(default_factory=dict, description="Эффекты промокода в JSON")
    max_activations: int = Field(1, description="Максимум активаций")
    per_user_limit: int = Field(1, description="Лимит на пользователя")
    expires_at: Optional[date] = Field(None, description="Дата истечения")
    disabled: bool = Field(False, description="Отключен")
    code_hmac: Optional[str] = Field(None, description="HMAC хеш (генерируется автоматически)")
    
    @field_validator("raw_code")
    @classmethod
    def validate_raw_code(cls, v: str) -> str:
        if not v or len(v.strip()) < 3:
            raise ValueError("Промокод должен содержать минимум 3 символа")
        return v.strip()
    
    @model_validator(mode="after")
    def generate_code_hmac(self):
        """Автоматически генерируем HMAC из raw_code."""
        from bloobcat.settings import promo_settings

        if not promo_settings.hmac_secret:
            raise ValueError("PROMO_HMAC_SECRET не настроен")

        secret = promo_settings.hmac_secret.get_secret_value().encode()
        self.code_hmac = hmac.new(secret, self.raw_code.encode(), hashlib.sha256).hexdigest()
        return self


class PromoCodeUpdateSchema(FastAdminBaseModel):
    """Схема для обновления промокода через админ панель"""
    batch_id: Optional[int] = Field(None, description="ID партии промокодов")
    name: Optional[str] = Field(None, description="Имя промокода для отображения")
    effects: Dict[str, Any] = Field(default_factory=dict, description="Эффекты промокода в JSON")
    max_activations: int = Field(1, description="Максимум активаций")
    per_user_limit: int = Field(1, description="Лимит на пользователя")
    expires_at: Optional[date] = Field(None, description="Дата истечения")
    disabled: bool = Field(False, description="Отключен")


# Управление промокодами и партиями делается только через Directus
# (collections promo_batches / promo_codes / promo_usages).
# HMAC-преобразование raw_code теперь должно делаться в Directus flow
# или через скрипт scripts/seed_promo_codes.py (если оператор импортирует
# коды массово). FastAdmin регистрации удалены вместе с уходом FastAdmin.
