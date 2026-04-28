from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM \"notification_marks\" a
        USING \"notification_marks\" b
        WHERE a.id < b.id
          AND a.user_id = b.user_id
          AND a.type = b.type
          AND COALESCE(a.key, '') = COALESCE(b.key, '')
          AND COALESCE(a.meta, '') = COALESCE(b.meta, '');

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'notification_marks'
                  AND c.contype = 'u'
                  AND c.conname = 'ux_notification_marks_user_type_key_meta'
            ) THEN
                ALTER TABLE \"notification_marks\"
                ADD CONSTRAINT \"ux_notification_marks_user_type_key_meta\"
                UNIQUE (\"user_id\", \"type\", \"key\", \"meta\");
            END IF;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE \"notification_marks\"
        DROP CONSTRAINT IF EXISTS \"ux_notification_marks_user_type_key_meta\";
    """
