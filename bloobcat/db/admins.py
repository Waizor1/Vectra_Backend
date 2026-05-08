import bcrypt
from tortoise import fields
from tortoise.models import Model

from bloobcat.settings import admin_settings


class Admin(Model):
    """Legacy admin row used to bootstrap the Directus migration.

    The actual admin UI is Directus; this table is kept so
    `scripts/migrate_admins_to_directus.py` can read existing rows and
    so `Admin.init()` keeps a single seed admin record consistent with
    settings. New admin management happens entirely in Directus.
    """

    username = fields.CharField(max_length=255, unique=True)
    hash_password = fields.CharField(max_length=255)
    is_superuser = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)

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
