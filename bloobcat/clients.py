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
                "bloobcat.db.active_tariff",
                "bloobcat.db.notifications",
                "bloobcat.db.promotions",
                "bloobcat.db.prize_wheel",
            ],
            "default_connection": "default",
        },
    },
}
