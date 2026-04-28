from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            target_schema text;
            users_relid oid;
            referred_by_attnum smallint;
            id_attnum smallint;
            matching_fk_count integer := 0;
            conflicting_fk_count integer := 0;
            has_canonical_correct boolean := false;
            canonical_exists boolean := false;
            r RECORD;
        BEGIN
            SELECT n.nspname
              INTO target_schema
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'users'
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
            LIMIT 1;

            IF target_schema IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                'UPDATE %I.%I child SET %I = NULL WHERE child.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I parent WHERE parent.%I = child.%I)',
                target_schema,
                'users',
                'referred_by',
                'referred_by',
                target_schema,
                'users',
                'id',
                'referred_by'
            );

            SELECT c.oid
              INTO users_relid
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = target_schema
              AND c.relname = 'users'
              AND c.relkind IN ('r', 'p')
            LIMIT 1;

            IF users_relid IS NULL THEN
                RETURN;
            END IF;

            SELECT a.attnum
              INTO referred_by_attnum
            FROM pg_attribute a
            WHERE a.attrelid = users_relid
              AND a.attname = 'referred_by'
              AND NOT a.attisdropped
            LIMIT 1;

            SELECT a.attnum
              INTO id_attnum
            FROM pg_attribute a
            WHERE a.attrelid = users_relid
              AND a.attname = 'id'
              AND NOT a.attisdropped
            LIMIT 1;

            IF referred_by_attnum IS NULL OR id_attnum IS NULL THEN
                RETURN;
            END IF;

            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE c.conname <> 'users_referred_by_foreign' OR c.confdeltype <> 'n'),
                   BOOL_OR(c.conname = 'users_referred_by_foreign' AND c.confdeltype = 'n')
              INTO matching_fk_count, conflicting_fk_count, has_canonical_correct
            FROM pg_constraint c
            WHERE c.contype = 'f'
              AND c.conrelid = users_relid
              AND c.conkey = ARRAY[referred_by_attnum]::smallint[]
              AND c.confrelid = users_relid
              AND c.confkey = ARRAY[id_attnum]::smallint[];

            SELECT EXISTS(
                SELECT 1
                FROM pg_constraint c
                WHERE c.conrelid = users_relid
                  AND c.conname = 'users_referred_by_foreign'
            )
              INTO canonical_exists;

            IF NOT (matching_fk_count = 1 AND conflicting_fk_count = 0 AND has_canonical_correct) THEN
                FOR r IN
                    SELECT c.conname
                    FROM pg_constraint c
                    WHERE c.contype = 'f'
                      AND c.conrelid = users_relid
                      AND c.conkey = ARRAY[referred_by_attnum]::smallint[]
                      AND c.confrelid = users_relid
                      AND c.confkey = ARRAY[id_attnum]::smallint[]
                LOOP
                    EXECUTE format(
                        'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                        target_schema,
                        'users',
                        r.conname
                    );
                END LOOP;

                IF canonical_exists AND NOT has_canonical_correct THEN
                    EXECUTE format(
                        'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                        target_schema,
                        'users',
                        'users_referred_by_foreign'
                    );
                END IF;

                IF NOT EXISTS(
                    SELECT 1
                    FROM pg_constraint c
                    WHERE c.conrelid = users_relid
                      AND c.conname = 'users_referred_by_foreign'
                ) THEN
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
                END IF;
            END IF;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            target_schema text;
            users_relid oid;
            referred_by_attnum smallint;
            id_attnum smallint;
            r RECORD;
        BEGIN
            SELECT n.nspname
              INTO target_schema
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'users'
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
            LIMIT 1;

            IF target_schema IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                'UPDATE %I.%I child SET %I = NULL WHERE child.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I parent WHERE parent.%I = child.%I)',
                target_schema,
                'users',
                'referred_by',
                'referred_by',
                target_schema,
                'users',
                'id',
                'referred_by'
            );

            SELECT c.oid
              INTO users_relid
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = target_schema
              AND c.relname = 'users'
              AND c.relkind IN ('r', 'p')
            LIMIT 1;

            IF users_relid IS NULL THEN
                RETURN;
            END IF;

            SELECT a.attnum
              INTO referred_by_attnum
            FROM pg_attribute a
            WHERE a.attrelid = users_relid
              AND a.attname = 'referred_by'
              AND NOT a.attisdropped
            LIMIT 1;

            SELECT a.attnum
              INTO id_attnum
            FROM pg_attribute a
            WHERE a.attrelid = users_relid
              AND a.attname = 'id'
              AND NOT a.attisdropped
            LIMIT 1;

            IF referred_by_attnum IS NULL OR id_attnum IS NULL THEN
                RETURN;
            END IF;

            FOR r IN
                SELECT c.conname
                FROM pg_constraint c
                WHERE c.contype = 'f'
                  AND c.conrelid = users_relid
                  AND c.conkey = ARRAY[referred_by_attnum]::smallint[]
                  AND c.confrelid = users_relid
                  AND c.confkey = ARRAY[id_attnum]::smallint[]
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                    target_schema,
                    'users',
                    r.conname
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
