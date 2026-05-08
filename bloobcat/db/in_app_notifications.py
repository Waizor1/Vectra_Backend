"""In-app notification models for dynamic Mini App banners/toasts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:
    from bloobcat.db.users import Users  # noqa: F401


class InAppNotification(models.Model):
    """Dynamic in-app notification (banner/toast) with scheduling and limits."""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=255)
    body = fields.TextField()
    start_at = fields.DatetimeField(description="Show from (inclusive)")
    end_at = fields.DatetimeField(description="Show until (inclusive)")
    max_per_user = fields.IntField(
        null=True,
        description="Max views per user (null = unlimited)",
    )
    max_per_session = fields.IntField(
        null=True,
        description="Max views per session per user (null = unlimited)",
    )
    auto_hide_seconds = fields.IntField(
        null=True,
        description="Auto-hide after N seconds (null = manual close)",
    )
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "in_app_notifications"

    def __str__(self) -> str:
        return f"InAppNotification({self.id}): {self.title}"

    async def save(self, *args, **kwargs) -> None:
        """Validate before save: end_at >= start_at; max_* and auto_hide >= 1 or null."""
        if self.start_at is not None and self.end_at is not None:
            if self.end_at < self.start_at:
                raise ValueError("end_at must be >= start_at")
        if self.max_per_user is not None and self.max_per_user < 1:
            raise ValueError("max_per_user must be >= 1 or null")
        if self.max_per_session is not None and self.max_per_session < 1:
            raise ValueError("max_per_session must be >= 1 or null")
        if self.auto_hide_seconds is not None and self.auto_hide_seconds < 1:
            raise ValueError("auto_hide_seconds must be >= 1 or null")
        await super().save(*args, **kwargs)


class NotificationView(models.Model):
    """Record of a notification being shown to a user in a session."""

    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users", related_name="in_app_notification_views", on_delete=fields.CASCADE
    )
    notification: fields.ForeignKeyRelation[InAppNotification] = fields.ForeignKeyField(
        "models.InAppNotification", related_name="views", on_delete=fields.CASCADE
    )
    session_id = fields.CharField(max_length=128)
    viewed_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "notification_views"
        indexes = (("user_id", "notification_id"), ("user_id", "notification_id", "session_id"))
