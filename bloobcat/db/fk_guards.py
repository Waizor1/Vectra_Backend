from tortoise import Tortoise

from bloobcat.logger import get_logger

logger = get_logger("fk_guards")


async def ensure_active_tariffs_fk_cascade() -> None:
    """
    Safety net against schema drift:
    ensure active_tariffs.user_id -> users.id is ON DELETE CASCADE.
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal: {}", e)
        return

    try:
        rows = await conn.execute_query_dict(
            """
            SELECT
              tc.constraint_name,
              rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.constraint_schema = ccu.constraint_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.constraint_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = 'active_tariffs'
              AND kcu.column_name = 'user_id'
              AND ccu.table_name = 'users'
              AND ccu.column_name = 'id';
            """
        )
        normalized_rules = {str((row.get("delete_rule") or "")).upper() for row in rows}
        has_cascade = "CASCADE" in normalized_rules
        has_non_cascade = any(rule and rule != "CASCADE" for rule in normalized_rules)

        if rows and has_cascade and not has_non_cascade:
            logger.info(
                "FK check ok: active_tariffs.user_id -> users.id is CASCADE (constraints={})",
                ", ".join(str(row.get("constraint_name")) for row in rows if row.get("constraint_name")),
            )
            return

        logger.warning(
            "Обнаружен drift FK active_tariffs.user_id -> users.id (rules={}): применяю self-heal",
            sorted(normalized_rules) if normalized_rules else ["MISSING"],
        )
        await conn.execute_script(
            """
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
        )
        logger.info("FK self-heal applied: fk_active_tariffs_user -> ON DELETE CASCADE")
    except Exception as e:
        logger.error("Ошибка FK self-heal для active_tariffs: {}", e, exc_info=True)


async def ensure_notification_marks_fk_cascade() -> None:
    """
    Safety net against schema drift:
    ensure notification_marks.user_id -> users.id is ON DELETE CASCADE.
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal (notification_marks): {}", e)
        return

    try:
        rows = await conn.execute_query_dict(
            """
            SELECT rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.constraint_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = 'notification_marks'
              AND tc.constraint_name = 'notification_marks_user_id_fkey'
            LIMIT 1;
            """
        )
        delete_rule = (rows[0].get("delete_rule") if rows else None) or ""
        if str(delete_rule).upper() == "CASCADE":
            logger.info("FK check ok: notification_marks_user_id_fkey is CASCADE")
            return

        logger.warning(
            "Обнаружен drift FK notification_marks_user_id_fkey (delete_rule={}): применяю self-heal",
            delete_rule or "MISSING",
        )
        await conn.execute_script(
            """
            ALTER TABLE "notification_marks"
              DROP CONSTRAINT IF EXISTS "notification_marks_user_id_fkey";

            ALTER TABLE "notification_marks"
              ADD CONSTRAINT "notification_marks_user_id_fkey"
              FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE;
            """
        )
        logger.info("FK self-heal applied: notification_marks_user_id_fkey -> ON DELETE CASCADE")
    except Exception as e:
        logger.error("Ошибка FK self-heal для notification_marks: {}", e, exc_info=True)
