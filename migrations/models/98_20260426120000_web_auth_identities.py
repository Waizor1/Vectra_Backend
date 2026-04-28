from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "auth_token_version" INT NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS "auth_identities" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "provider" VARCHAR(32) NOT NULL,
            "provider_subject" VARCHAR(255) NOT NULL,
            "email" VARCHAR(255),
            "email_verified" BOOL NOT NULL DEFAULT FALSE,
            "display_name" VARCHAR(255),
            "avatar_url" TEXT,
            "linked_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "last_login_at" TIMESTAMPTZ,
            CONSTRAINT "uid_auth_identity_provider_subject" UNIQUE ("provider", "provider_subject")
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_identities_user_provider"
            ON "auth_identities" ("user_id", "provider");
        CREATE INDEX IF NOT EXISTS "idx_auth_identities_provider_subject"
            ON "auth_identities" ("provider", "provider_subject");

        CREATE TABLE IF NOT EXISTS "auth_password_credentials" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "email_normalized" VARCHAR(255) NOT NULL UNIQUE,
            "password_hash" VARCHAR(255) NOT NULL,
            "email_verified" BOOL NOT NULL DEFAULT FALSE,
            "verification_token_hash" VARCHAR(128) UNIQUE,
            "verification_expires_at" TIMESTAMPTZ,
            "reset_token_hash" VARCHAR(128) UNIQUE,
            "reset_expires_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_password_user"
            ON "auth_password_credentials" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_auth_password_email"
            ON "auth_password_credentials" ("email_normalized");
        CREATE INDEX IF NOT EXISTS "idx_auth_password_verify_token"
            ON "auth_password_credentials" ("verification_token_hash");
        CREATE INDEX IF NOT EXISTS "idx_auth_password_reset_token"
            ON "auth_password_credentials" ("reset_token_hash");

        CREATE TABLE IF NOT EXISTS "auth_oauth_states" (
            "id" SERIAL PRIMARY KEY,
            "state_hash" VARCHAR(128) NOT NULL UNIQUE,
            "provider" VARCHAR(32) NOT NULL,
            "mode" VARCHAR(16) NOT NULL,
            "nonce" VARCHAR(128) NOT NULL,
            "pkce_verifier" VARCHAR(255) NOT NULL,
            "linking_user_id" BIGINT,
            "return_to" VARCHAR(512),
            "expires_at" TIMESTAMPTZ NOT NULL,
            "consumed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_oauth_provider_mode"
            ON "auth_oauth_states" ("provider", "mode");
        CREATE INDEX IF NOT EXISTS "idx_auth_oauth_expires"
            ON "auth_oauth_states" ("expires_at");
        CREATE INDEX IF NOT EXISTS "idx_auth_oauth_linking_user"
            ON "auth_oauth_states" ("linking_user_id");

        CREATE TABLE IF NOT EXISTS "auth_login_tickets" (
            "id" SERIAL PRIMARY KEY,
            "ticket_hash" VARCHAR(128) NOT NULL UNIQUE,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "expires_at" TIMESTAMPTZ NOT NULL,
            "consumed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_login_tickets_user"
            ON "auth_login_tickets" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_auth_login_tickets_expires"
            ON "auth_login_tickets" ("expires_at");

        CREATE TABLE IF NOT EXISTS "auth_link_requests" (
            "id" SERIAL PRIMARY KEY,
            "token_hash" VARCHAR(128) NOT NULL UNIQUE,
            "source_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "target_provider" VARCHAR(32) NOT NULL,
            "expires_at" TIMESTAMPTZ NOT NULL,
            "consumed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_link_requests_source"
            ON "auth_link_requests" ("source_user_id");
        CREATE INDEX IF NOT EXISTS "idx_auth_link_requests_provider"
            ON "auth_link_requests" ("target_provider");
        CREATE INDEX IF NOT EXISTS "idx_auth_link_requests_expires"
            ON "auth_link_requests" ("expires_at");

        CREATE TABLE IF NOT EXISTS "auth_audit_events" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT,
            "provider" VARCHAR(32),
            "action" VARCHAR(64) NOT NULL,
            "result" VARCHAR(32) NOT NULL,
            "reason" VARCHAR(128),
            "ip_hash" VARCHAR(128),
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "idx_auth_audit_user"
            ON "auth_audit_events" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_auth_audit_provider_action"
            ON "auth_audit_events" ("provider", "action");
        CREATE INDEX IF NOT EXISTS "idx_auth_audit_created"
            ON "auth_audit_events" ("created_at");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "auth_audit_events";
        DROP TABLE IF EXISTS "auth_link_requests";
        DROP TABLE IF EXISTS "auth_login_tickets";
        DROP TABLE IF EXISTS "auth_oauth_states";
        DROP TABLE IF EXISTS "auth_password_credentials";
        DROP TABLE IF EXISTS "auth_identities";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "auth_token_version";
    """
