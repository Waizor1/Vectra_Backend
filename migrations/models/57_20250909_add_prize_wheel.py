from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "prize_wheel_history" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "prize_type" VARCHAR(64) NOT NULL,
            "prize_name" VARCHAR(255) NOT NULL,
            "prize_value" VARCHAR(255) NOT NULL,
            "is_claimed" BOOL NOT NULL DEFAULT FALSE,
            "claimed_at" TIMESTAMPTZ,
            "admin_notified" BOOL NOT NULL DEFAULT FALSE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_prize_history_user" ON "prize_wheel_history" ("user_id");

        CREATE TABLE IF NOT EXISTS "prize_wheel_config" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "prize_type" VARCHAR(64) NOT NULL UNIQUE,
            "prize_name" VARCHAR(255) NOT NULL,
            "prize_value" VARCHAR(255) NOT NULL,
            "probability" DOUBLE PRECISION NOT NULL,
            "is_active" BOOL NOT NULL DEFAULT TRUE,
            "requires_admin" BOOL NOT NULL DEFAULT FALSE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Users: добавляем поле попыток, если его нет
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='prize_wheel_attempts'
            ) THEN
                ALTER TABLE "users" ADD COLUMN "prize_wheel_attempts" INT NOT NULL DEFAULT 0;
            END IF;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "prize_wheel_history";
        DROP TABLE IF EXISTS "prize_wheel_config";
        -- Поле убирать не будем, чтобы не терять данные попыток
    """


