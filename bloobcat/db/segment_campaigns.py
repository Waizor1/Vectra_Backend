from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tortoise import fields, models


SEGMENT_CHOICES = (
    "no_purchase_yet",
    "trial_active",
    "lapsed",
    "loyal_renewer",
    "everyone",
)

ACCENT_CHOICES = ("gold", "cyan", "violet", "blue", "green")


class SegmentCampaign(models.Model):
    """Сегментная маркетинговая акция со скидкой и таймером.

    Кампания применяется к пользователям, попадающим в `segment`,
    в окне `[starts_at; ends_at]`. Скидка действует только на тарифы
    из `applies_to_months` (если пусто — на все). При нескольких
    подходящих кампаниях выбирается с наибольшим `priority`.
    """

    id = fields.IntField(primary_key=True)
    slug = fields.CharField(
        max_length=80,
        unique=True,
        description="Машинный идентификатор кампании (для аналитики)",
    )
    title = fields.CharField(max_length=120, description="Заголовок акции на витрине")
    subtitle = fields.CharField(
        max_length=180,
        null=True,
        description="Подзаголовок/слоган. Можно оставить пустым.",
    )
    description = fields.TextField(
        null=True,
        description="Длинное описание (модалка/тултип). Можно оставить пустым.",
    )
    segment = fields.CharField(
        max_length=32,
        description=(
            "Целевой сегмент: no_purchase_yet | trial_active | lapsed |"
            " loyal_renewer | everyone"
        ),
    )
    discount_percent = fields.IntField(
        description="Размер скидки в процентах (1..90)",
    )
    applies_to_months: List[int] = fields.JSONField(
        default=list,
        description="Список длительностей в месяцах, к которым применима скидка ([] = ко всем)",
    )
    accent = fields.CharField(
        max_length=16,
        default="gold",
        description="Акцентная палитра карточки: gold | cyan | violet | blue | green",
    )
    cta_label = fields.CharField(
        max_length=80,
        null=True,
        description="Подпись кнопки CTA (если пусто — фронт подставит дефолт)",
    )
    cta_target = fields.CharField(
        max_length=24,
        default="builder",
        description="Цель CTA: builder | tariff_12m | tariff_6m | tariff_3m | tariff_1m | family",
    )
    starts_at = fields.DatetimeField(
        description="Когда кампания становится активной",
    )
    ends_at = fields.DatetimeField(
        description="Когда кампания завершается (дедлайн таймера)",
    )
    priority = fields.IntField(
        default=0,
        description="Приоритет: при нескольких совпадениях выбирается с большим",
    )
    is_active = fields.BooleanField(
        default=True,
        description="Принудительный выключатель кампании",
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "segment_campaigns"
        indexes = (("segment", "is_active"),)

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"{self.slug} ({self.segment}, -{self.discount_percent}%)"

    def is_live_at(self, moment: Optional[datetime] = None) -> bool:
        if not self.is_active:
            return False
        now = moment or datetime.now(timezone.utc)
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False
        return True

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "subtitle": self.subtitle,
            "description": self.description,
            "segment": self.segment,
            "discountPercent": int(self.discount_percent),
            "appliesToMonths": list(self.applies_to_months or []),
            "accent": self.accent or "gold",
            "ctaLabel": self.cta_label,
            "ctaTarget": self.cta_target or "builder",
            "startsAtMs": int(self.starts_at.timestamp() * 1000)
            if self.starts_at
            else None,
            "endsAtMs": int(self.ends_at.timestamp() * 1000)
            if self.ends_at
            else None,
            "priority": int(self.priority or 0),
        }


# NOTE: управление кампаниями делается только через Directus
# (collection `segment_campaigns`). FastAdmin не используется.
