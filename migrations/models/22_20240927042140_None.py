from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "users" (
    "id" BIGSERIAL NOT NULL PRIMARY KEY,
    "username" VARCHAR(100),
    "full_name" VARCHAR(1000) NOT NULL,
    "expired_at" DATE NOT NULL  DEFAULT '2024-09-30',
    "is_registered" BOOL NOT NULL  DEFAULT False,
    "connect_url" VARCHAR(100),
    "balance" INT NOT NULL  DEFAULT 0,
    "referred_by" INT NOT NULL  DEFAULT 0,
    "is_admin" BOOL NOT NULL  DEFAULT False,
    "custom_referral_percent" INT NOT NULL  DEFAULT 0,
    "registration_date" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "referrals" INT NOT NULL  DEFAULT 0,
    "is_subscribed" BOOL NOT NULL  DEFAULT False,
    "is_sended_notification_connect" BOOL NOT NULL  DEFAULT False,
    "utm" VARCHAR(100)
);
CREATE TABLE IF NOT EXISTS "admin" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "username" VARCHAR(255) NOT NULL UNIQUE,
    "hash_password" VARCHAR(255) NOT NULL,
    "is_superuser" BOOL NOT NULL  DEFAULT False,
    "is_active" BOOL NOT NULL  DEFAULT False
);
CREATE TABLE IF NOT EXISTS "tariffs" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "name" VARCHAR(100) NOT NULL,
    "months" INT NOT NULL,
    "price" INT NOT NULL
);
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
