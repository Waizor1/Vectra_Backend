from uuid import UUID

import bcrypt
from fastadmin import TortoiseModelAdmin, register
from tortoise import fields
from tortoise.models import Model

from bloobcat.settings import admin_settings


class Admin(Model):
    username = fields.CharField(max_length=255, unique=True)
    hash_password = fields.CharField(max_length=255)
    is_superuser = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=False)

    @classmethod
    async def init(cls):
        salt = bcrypt.gensalt()
        password = bcrypt.hashpw(
            admin_settings.password.get_secret_value().encode(), salt
        )
        await cls.get_or_create(
            username=admin_settings.login.get_secret_value(),
            defaults={
                "hash_password": password.decode(),
                "is_superuser": True,
            },
        )

    def __str__(self):
        return self.username


@register(Admin)
class UserAdmin(TortoiseModelAdmin):
    exclude = ("hash_password",)
    list_display = ("id", "username", "is_superuser", "is_active")
    list_display_links = ("id", "username")
    list_filter = ("id", "username", "is_superuser", "is_active")
    search_fields = ("username",)

    async def authenticate(
        self, username: str, password: str
    ) -> UUID | int | None:
        user = await Admin.filter(username=username, is_superuser=True).first()
        if not user:
            return None
        if not bcrypt.checkpw(password.encode(), user.hash_password.encode()):
            return None
        return user.id
