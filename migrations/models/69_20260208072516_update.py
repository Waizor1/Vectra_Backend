from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "family_devices" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "client_id" VARCHAR(64),
    "title" VARCHAR(100) NOT NULL,
    "subtitle" VARCHAR(200) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_family_devi_user_id_4bd7d5" UNIQUE ("user_id", "client_id")
);
        CREATE TABLE IF NOT EXISTS "family_members" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "allocated_devices" INT NOT NULL  DEFAULT 1,
    "status" VARCHAR(24) NOT NULL  DEFAULT 'active',
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "member_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "owner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_family_memb_owner_i_aba198" UNIQUE ("owner_id", "member_id")
);
        CREATE TABLE IF NOT EXISTS "family_invites" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "allocated_devices" INT NOT NULL  DEFAULT 1,
    "token_hash" VARCHAR(128) NOT NULL UNIQUE,
    "expires_at" TIMESTAMPTZ,
    "max_uses" INT NOT NULL  DEFAULT 1,
    "used_count" INT NOT NULL  DEFAULT 0,
    "used_at" TIMESTAMPTZ,
    "revoked_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "owner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_family_invi_token_h_d75187" ON "family_invites" ("token_hash");
        CREATE TABLE IF NOT EXISTS "family_audit_logs" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "action" VARCHAR(64) NOT NULL,
    "target_id" VARCHAR(128),
    "details" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "actor_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "owner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "family_devices";
        DROP TABLE IF EXISTS "family_members";
        DROP TABLE IF EXISTS "family_invites";
        DROP TABLE IF EXISTS "family_audit_logs";"""
