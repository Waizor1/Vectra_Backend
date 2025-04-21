from cyberdog.settings import script_settings

TORTOISE_ORM = {
    "connections": {"default": script_settings.db.get_secret_value()},
    "apps": {
        "models": {
            "models": [
                "cyberdog.db.users",
                "cyberdog.db.admins",
                "cyberdog.db.tariff",
                "cyberdog.db.connections",
                "aerich.models",
                "cyberdog.db.tv",
                "cyberdog.db.payments",
            ],
            "default_connection": "default",
        },
    },
}
