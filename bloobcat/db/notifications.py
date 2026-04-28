from typing import TYPE_CHECKING
from tortoise import fields, models

if TYPE_CHECKING:  # For type hints only, avoids runtime circular import
    from bloobcat.db.users import Users  # noqa: F401


class NotificationMarks(models.Model):
    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users", related_name="notification_marks", on_delete=fields.CASCADE
    )
    type = fields.CharField(
        max_length=64,
        description="Notification category, e.g., trial_no_sub, referral_prompt",
    )
    key = fields.CharField(
        max_length=64, null=True, description="Sub-key, e.g., 2h, 24h, 7d, 14d, 30d"
    )
    meta = fields.CharField(max_length=255, null=True, description="Optional metadata")
    sent_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "notification_marks"
        indexes = ("user_id", "type", "key")
        unique_together = (("user", "type", "key", "meta"),)
