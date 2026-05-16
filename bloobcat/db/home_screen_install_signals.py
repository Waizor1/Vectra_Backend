"""Append-only ledger of PWA install signals observed at claim time.

Every `POST /referrals/home-screen-claim` call writes one row here —
including the duplicate `already_claimed` calls — so we can analyze
post-fact: how many of our home-screen rewards came from a strong
W3C `appinstalled` signal vs. a weak `boot` heuristic vs. the manual
"Я уже добавил иконку" bypass, and whether the device had a real
push subscription at claim time.

This is shadow-mode by design: the ledger row is written before the
reward is granted, but a "suspicious" verdict (manual + no push sub)
does NOT block the grant. The goal of v1 is observability — once we
have a week of real verdict distributions we can decide whether to
add hard gates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:
    from bloobcat.db.users import Users  # noqa: F401


class HomeScreenInstallSignal(models.Model):
    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation["Users"] = fields.ForeignKeyField(
        "models.Users",
        related_name="home_screen_install_signals",
        on_delete=fields.CASCADE,
    )
    trigger = fields.CharField(
        max_length=32,
        description="appinstalled | first_standalone | pending_flag | boot | manual | unknown",
    )
    platform_hint = fields.CharField(max_length=32, null=True)
    reward_kind = fields.CharField(max_length=16, description="balance | discount")
    had_active_push_sub = fields.BooleanField(default=False)
    verdict = fields.CharField(
        max_length=32,
        description="strong | weak | manual_with_push | manual_no_push | unknown",
    )
    already_claimed = fields.BooleanField(
        default=False,
        description="True if this signal arrived after the user already claimed",
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "home_screen_install_signals"
        indexes = (
            ("user_id",),
            ("created_at",),
            ("verdict",),
        )

    def __str__(self) -> str:
        return (
            f"HomeScreenInstallSignal({self.id}, user={self.user_id}, "
            f"trigger={self.trigger}, verdict={self.verdict})"
        )
