from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_status" VARCHAR(24) NOT NULL DEFAULT 'new';
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_owner" VARCHAR(128);
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_note" TEXT;
        ALTER TABLE "error_reports" ADD COLUMN IF NOT EXISTS "triage_updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_updated_at";
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_note";
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_owner";
        ALTER TABLE "error_reports" DROP COLUMN IF EXISTS "triage_status";
    """
