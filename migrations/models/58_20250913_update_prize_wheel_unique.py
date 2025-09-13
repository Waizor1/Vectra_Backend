from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Убираем уникальность только по prize_type
        ALTER TABLE "prize_wheel_config"
        DROP CONSTRAINT IF EXISTS "prize_wheel_config_prize_type_key";

        -- Добавляем уникальность по паре (prize_type, prize_value), если ещё не добавлена
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints tc
                WHERE tc.table_name = 'prize_wheel_config'
                  AND tc.constraint_type = 'UNIQUE'
                  AND tc.constraint_name = 'uq_prize_wheel_type_value'
            ) THEN
                ALTER TABLE "prize_wheel_config"
                ADD CONSTRAINT "uq_prize_wheel_type_value" UNIQUE ("prize_type", "prize_value");
            END IF;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Откатываемся: убираем составную уникальность и возвращаем уникальность по prize_type
        ALTER TABLE "prize_wheel_config"
        DROP CONSTRAINT IF EXISTS "uq_prize_wheel_type_value";

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints tc
                WHERE tc.table_name = 'prize_wheel_config'
                  AND tc.constraint_type = 'UNIQUE'
                  AND tc.constraint_name = 'prize_wheel_config_prize_type_key'
            ) THEN
                ALTER TABLE "prize_wheel_config"
                ADD CONSTRAINT "prize_wheel_config_prize_type_key" UNIQUE ("prize_type");
            END IF;
        END $$;
    """


