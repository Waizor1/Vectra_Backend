from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_severity" VARCHAR(24) NOT NULL DEFAULT 'medium';
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_due_at" TIMESTAMPTZ;
        UPDATE "error_reports"
        SET "triage_due_at" = COALESCE("triage_due_at", "created_at" + INTERVAL '24 hours')
        WHERE "triage_due_at" IS NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_due_at";
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_severity";
    """
