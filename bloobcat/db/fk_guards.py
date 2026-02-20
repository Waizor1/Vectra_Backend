from tortoise import Tortoise

from bloobcat.logger import get_logger

logger = get_logger("fk_guards")


async def _resolve_active_tariffs_schema(conn) -> str | None:
    # Prefer public when multiple schemas are present.
    rows = await conn.execute_query_dict(
        """
        SELECT n.nspname AS table_schema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'active_tariffs'
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
        LIMIT 1;
        """
    )
    if not rows:
        return None
    return rows[0].get("table_schema")


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
              tc.table_schema,
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
              AND tc.table_schema = kcu.table_schema
              AND ccu.table_name = 'users'
              AND ccu.column_name = 'id';
            """
        )
        target_schema = await _resolve_active_tariffs_schema(conn)
        if not target_schema:
            logger.warning("Не найдена таблица active_tariffs: FK self-heal пропущен")
            return

        target_rows = [row for row in rows if row.get("table_schema") == target_schema]
        normalized_rules = {str((row.get("delete_rule") or "")).upper() for row in rows}
        target_rules = {str((row.get("delete_rule") or "")).upper() for row in target_rows}
        has_cascade = "CASCADE" in target_rules
        has_non_cascade = any(rule and rule != "CASCADE" for rule in target_rules)
        has_one_constraint = len(target_rows) == 1

        if target_rows and has_cascade and not has_non_cascade and has_one_constraint:
            logger.info(
                "FK check ok: {}.active_tariffs.user_id -> {}.users.id is CASCADE (constraint={})",
                target_schema,
                target_schema,
                ", ".join(
                    str(row.get("constraint_name"))
                    for row in target_rows
                    if row.get("constraint_name")
                ),
            )
            return

        logger.warning(
            "Обнаружен drift FK {}.active_tariffs.user_id -> {}.users.id (rules={}, constraints={}): применяю self-heal",
            target_schema,
            target_schema,
            sorted(target_rules) if target_rules else ["MISSING"],
            ", ".join(
                str(row.get("constraint_name")) for row in target_rows if row.get("constraint_name")
            ) or "NONE",
        )
        await conn.execute_script(
            f"""
            DO $$
            DECLARE r RECORD;
            BEGIN
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
                  AND tc.table_schema = '{target_schema}'
                  AND tc.table_name = 'active_tariffs'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
              LOOP
                EXECUTE format(
                  'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                  '{target_schema}',
                  'active_tariffs',
                  r.constraint_name
                );
              END LOOP;
            END $$;

            ALTER TABLE "{target_schema}"."active_tariffs"
              ADD CONSTRAINT "fk_active_tariffs_user"
              FOREIGN KEY ("user_id") REFERENCES "{target_schema}"."users" ("id") ON DELETE CASCADE;
            """
        )
        logger.info(
            "FK self-heal applied: {}.fk_active_tariffs_user -> ON DELETE CASCADE",
            target_schema,
        )
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
