from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY job_type, user_id
                    ORDER BY
                        updated_at DESC,
                        id DESC
                ) AS rn
            FROM remnawave_retry_jobs
            WHERE status IN ('pending', 'processing')
        )
        UPDATE remnawave_retry_jobs j
        SET
            status = 'dead_letter',
            last_error = LEFT(
                CONCAT_WS(' | ', NULLIF(j.last_error, ''), 'deduplicated by active retry uniqueness migration'),
                1024
            )
        FROM ranked r
        WHERE j.id = r.id
          AND r.rn > 1;

        DROP INDEX IF EXISTS "ux_remnawave_retry_jobs_active_user";

        CREATE UNIQUE INDEX IF NOT EXISTS "ux_remnawave_retry_jobs_active_user"
            ON "remnawave_retry_jobs" ("job_type", "user_id")
            WHERE "status" IN ('pending', 'processing');
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ux_remnawave_retry_jobs_active_user";

        CREATE UNIQUE INDEX IF NOT EXISTS "ux_remnawave_retry_jobs_active_user"
            ON "remnawave_retry_jobs" ("job_type", "user_id")
            WHERE "status" = 'pending';
    """
