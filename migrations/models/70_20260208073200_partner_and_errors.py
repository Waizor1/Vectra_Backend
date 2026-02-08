from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "partner_qr_codes" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "title" VARCHAR(120) NOT NULL,
    "slug" VARCHAR(64),
    "link" VARCHAR(255),
    "is_active" BOOL NOT NULL  DEFAULT True,
    "views_count" INT NOT NULL  DEFAULT 0,
    "activations_count" INT NOT NULL  DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "owner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "partner_withdrawals" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "amount_rub" INT NOT NULL,
    "method" VARCHAR(16) NOT NULL,
    "details" VARCHAR(255),
    "status" VARCHAR(24) NOT NULL  DEFAULT 'created',
    "error" VARCHAR(255),
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "owner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "error_reports" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "user_id" BIGINT,
    "event_id" VARCHAR(64) NOT NULL,
    "code" VARCHAR(128) NOT NULL,
    "type" VARCHAR(64) NOT NULL,
    "message" VARCHAR(1024),
    "name" VARCHAR(256),
    "stack" TEXT,
    "route" VARCHAR(512),
    "href" VARCHAR(1024),
    "user_agent" VARCHAR(512),
    "extra" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "reported_at" TIMESTAMPTZ
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "error_reports";
        DROP TABLE IF EXISTS "partner_withdrawals";
        DROP TABLE IF EXISTS "partner_qr_codes";"""
