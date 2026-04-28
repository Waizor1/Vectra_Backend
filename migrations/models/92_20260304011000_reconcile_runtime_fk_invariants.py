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
            FROM pg_namespace n
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'users'
                  AND c.relkind IN ('r', 'p')
              )
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'active_tariffs'
                  AND c.relkind IN ('r', 'p')
              )
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'notification_marks'
                  AND c.relkind IN ('r', 'p')
              )
            ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
            LIMIT 1;

            IF target_schema IS NULL THEN
                RETURN;
            END IF;

            -- users.referred_by must stay nullable and safe for SET NULL FK rebind.
            EXECUTE format(
                'UPDATE %I.%I child
                   SET %I = NULL
                 WHERE child.%I IS NOT NULL
                   AND NOT EXISTS (
                     SELECT 1
                     FROM %I.%I parent
                     WHERE parent.%I = child.%I
                   )',
                target_schema,
                'users',
                'referred_by',
                'referred_by',
                target_schema,
                'users',
                'id',
                'referred_by'
            );

            -- active_tariffs.user_id -> users.id must be ON DELETE CASCADE.
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
                  AND tc.table_name = 'active_tariffs'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'active_tariffs',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE CASCADE',
                target_schema,
                'active_tariffs',
                'fk_active_tariffs_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );

            -- notification_marks has historical drifts and possible orphan rows.
            EXECUTE format(
                'DELETE FROM %I.%I nm
                 WHERE nm.%I IS NOT NULL
                   AND NOT EXISTS (
                     SELECT 1
                     FROM %I.%I u
                     WHERE u.%I = nm.%I
                   )',
                target_schema,
                'notification_marks',
                'user_id',
                target_schema,
                'users',
                'id',
                'user_id'
            );

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
                  AND tc.table_name = 'notification_marks'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'notification_marks',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE CASCADE',
                target_schema,
                'notification_marks',
                'fk_notification_marks_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );

            -- users.referred_by -> users.id must be ON DELETE SET NULL.
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
                  AND tc.table_name = 'users'
                  AND kcu.column_name = 'referred_by'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'users',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE SET NULL',
                target_schema,
                'users',
                'users_referred_by_foreign',
                'referred_by',
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
            FROM pg_namespace n
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'users'
                  AND c.relkind IN ('r', 'p')
              )
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'active_tariffs'
                  AND c.relkind IN ('r', 'p')
              )
              AND EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.relnamespace = n.oid
                  AND c.relname = 'notification_marks'
                  AND c.relkind IN ('r', 'p')
              )
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
                  AND tc.table_name = 'active_tariffs'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'active_tariffs',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE NO ACTION',
                target_schema,
                'active_tariffs',
                'fk_active_tariffs_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );

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
                  AND tc.table_name = 'notification_marks'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'notification_marks',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE NO ACTION',
                target_schema,
                'notification_marks',
                'fk_notification_marks_user',
                'user_id',
                target_schema,
                'users',
                'id'
            );

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
                  AND tc.table_name = 'users'
                  AND kcu.column_name = 'referred_by'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'users',
                    r.constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE NO ACTION',
                target_schema,
                'users',
                'users_referred_by_foreign',
                'referred_by',
                target_schema,
                'users',
                'id'
            );
        END $$;
    """
