from bloobcat.settings import script_settings

TORTOISE_ORM = {
    "connections": {"default": script_settings.db.get_secret_value()},
    "apps": {
        "models": {
            "models": [
                "bloobcat.db.users",
                "bloobcat.db.admins",
                "bloobcat.db.tariff",
                "bloobcat.db.connections",
                "aerich.models",
                "bloobcat.db.payments",
            ],
            "default_connection": "default",
        },
    },
}
