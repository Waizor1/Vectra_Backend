from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f'
                  AND nsp.nspname = current_schema()
                  AND rel.relname = 'active_tariffs'
                  AND att.attname = 'user_id'
            LOOP
                EXECUTE format('ALTER TABLE "active_tariffs" DROP CONSTRAINT IF EXISTS %I', r.conname);
            END LOOP;
        END $$;

        ALTER TABLE "active_tariffs"
            ADD CONSTRAINT "fk_active_tariffs_user"
            FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f'
                  AND nsp.nspname = current_schema()
                  AND rel.relname = 'active_tariffs'
                  AND att.attname = 'user_id'
            LOOP
                EXECUTE format('ALTER TABLE "active_tariffs" DROP CONSTRAINT IF EXISTS %I', r.conname);
            END LOOP;
        END $$;

        ALTER TABLE "active_tariffs"
            ADD CONSTRAINT "fk_active_tariffs_user"
            FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
    """
