"""Web Push subscription model.

Stores PushSubscription records produced by the browser PushManager.
A single user can have multiple devices/browsers subscribed; `endpoint`
is the unique key for de-duplication. When delivery fails with a
404/410 we mark the row inactive (`is_active=False`) so retry logic
can clean it up later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:
    from bloobcat.db.users import Users  # noqa: F401


class PushSubscription(models.Model):
    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users", related_name="push_subscriptions", on_delete=fields.CASCADE
    )
    endpoint = fields.CharField(max_length=2048, unique=True)
    p256dh = fields.CharField(max_length=256)
    auth = fields.CharField(max_length=128)
    user_agent = fields.CharField(max_length=512, null=True)
    locale = fields.CharField(max_length=16, null=True)
    is_active = fields.BooleanField(default=True)
    failure_count = fields.IntField(default=0)
    last_success_at = fields.DatetimeField(null=True)
    last_failure_at = fields.DatetimeField(null=True)
    last_error = fields.CharField(max_length=512, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "push_subscriptions"
        indexes = (("user_id", "is_active"),)

    def __str__(self) -> str:
        return f"PushSubscription({self.id}, user={self.user_id}, active={self.is_active})"
