from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "remnawave_retry_jobs" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "job_type" VARCHAR(64) NOT NULL,
            "user_id" BIGINT NOT NULL,
            "remnawave_uuid" VARCHAR(64) NOT NULL,
            "attempts" INT NOT NULL DEFAULT 0,
            "next_retry_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "status" VARCHAR(24) NOT NULL DEFAULT 'pending',
            "last_error" VARCHAR(1024),
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS "idx_remnawave_retry_jobs_due"
            ON "remnawave_retry_jobs" ("status", "next_retry_at");
        CREATE INDEX IF NOT EXISTS "idx_remnawave_retry_jobs_user"
            ON "remnawave_retry_jobs" ("job_type", "user_id");
        CREATE UNIQUE INDEX IF NOT EXISTS "ux_remnawave_retry_jobs_active_user"
            ON "remnawave_retry_jobs" ("job_type", "user_id")
            WHERE "status" = 'pending';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "remnawave_retry_jobs";
    """
