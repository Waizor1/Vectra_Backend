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
                "bloobcat.db.discounts",
                "bloobcat.db.hwid_local",
                "bloobcat.db.family_devices",
                "bloobcat.db.family_members",
                "bloobcat.db.family_invites",
                "bloobcat.db.family_audit_logs",
                "bloobcat.db.partner_qr",
                "bloobcat.db.partner_withdrawals",
                "bloobcat.db.partner_earnings",
                "bloobcat.db.error_reports",
                "bloobcat.db.referral_rewards",
            ],
            "default_connection": "default",
        },
    },
}
