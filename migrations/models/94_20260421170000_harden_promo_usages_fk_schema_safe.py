from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            target_schema text;
            r RECORD;
        BEGIN
            SELECT n.nspname
              INTO target_schema
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'promo_usages'
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
            LIMIT 1;

            IF target_schema IS NULL THEN
                RETURN;
            END IF;

            FOR r IN
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.constraint_schema = kcu.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.constraint_schema = ccu.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = target_schema
                  AND tc.table_name = 'promo_usages'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'promo_usages',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'DELETE FROM %I.%I pu WHERE pu.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I u WHERE u.%I = pu.%I)',
                target_schema,
                'promo_usages',
                'user_id',
                target_schema,
                'users',
                'id',
                'user_id'
            );

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE CASCADE',
                target_schema,
                'promo_usages',
                'fk_promo_usages_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            target_schema text;
            r RECORD;
        BEGIN
            SELECT n.nspname
              INTO target_schema
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'promo_usages'
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
            LIMIT 1;

            IF target_schema IS NULL THEN
                RETURN;
            END IF;

            FOR r IN
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.constraint_schema = kcu.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.constraint_schema = ccu.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = target_schema
                  AND tc.table_name = 'promo_usages'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'promo_usages',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'DELETE FROM %I.%I pu WHERE pu.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I u WHERE u.%I = pu.%I)',
                target_schema,
                'promo_usages',
                'user_id',
                target_schema,
                'users',
                'id',
                'user_id'
            );

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE NO ACTION',
                target_schema,
                'promo_usages',
                'fk_promo_usages_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );
        END $$;
    """
