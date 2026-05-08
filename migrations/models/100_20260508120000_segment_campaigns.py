from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "segment_campaigns" (
            "id" SERIAL PRIMARY KEY,
            "slug" VARCHAR(80) NOT NULL UNIQUE,
            "title" VARCHAR(120) NOT NULL,
            "subtitle" VARCHAR(180),
            "description" TEXT,
            "segment" VARCHAR(32) NOT NULL,
            "discount_percent" INTEGER NOT NULL,
            "applies_to_months" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "accent" VARCHAR(16) NOT NULL DEFAULT 'gold',
            "cta_label" VARCHAR(80),
            "cta_target" VARCHAR(24) NOT NULL DEFAULT 'builder',
            "starts_at" TIMESTAMPTZ NOT NULL,
            "ends_at" TIMESTAMPTZ NOT NULL,
            "priority" INTEGER NOT NULL DEFAULT 0,
            "is_active" BOOLEAN NOT NULL DEFAULT TRUE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS "idx_segment_campaigns_segment_active"
            ON "segment_campaigns" ("segment", "is_active");

        CREATE INDEX IF NOT EXISTS "idx_segment_campaigns_window"
            ON "segment_campaigns" ("starts_at", "ends_at")
            WHERE "is_active" = TRUE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_segment_campaigns_window";
        DROP INDEX IF EXISTS "idx_segment_campaigns_segment_active";
        DROP TABLE IF EXISTS "segment_campaigns";
    """
