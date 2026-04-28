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


async def _resolve_notification_marks_schema(conn) -> str | None:
    # Prefer public when multiple schemas are present.
    rows = await conn.execute_query_dict(
        """
        SELECT n.nspname AS table_schema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'notification_marks'
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
        LIMIT 1;
        """
    )
    if not rows:
        return None
    return rows[0].get("table_schema")


async def _resolve_promo_usages_schema(conn) -> str | None:
    # Prefer public when multiple schemas are present.
    rows = await conn.execute_query_dict(
        """
        SELECT n.nspname AS table_schema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'promo_usages'
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
        LIMIT 1;
        """
    )
    if not rows:
        return None
    return rows[0].get("table_schema")


async def _resolve_users_schema(conn) -> str | None:
    # Prefer public when multiple schemas are present.
    rows = await conn.execute_query_dict(
        """
        SELECT n.nspname AS table_schema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'users'
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END, n.nspname
        LIMIT 1;
        """
    )
    if not rows:
        return None
    return rows[0].get("table_schema")


async def ensure_active_tariffs_fk_cascade() -> bool:
    """
    Safety net against schema drift:
    ensure active_tariffs.user_id -> users.id is ON DELETE CASCADE.
    Returns True if guard ran (check or heal), False if early return (no connection, table not found).
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal: {}", e)
        return False

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
            return False

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
            return True

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
        return True
    except Exception as e:
        logger.error("Ошибка FK self-heal для active_tariffs: {}", e, exc_info=True)
        return False


async def ensure_notification_marks_fk_cascade() -> bool:
    """
    Safety net against schema drift:
    ensure notification_marks.user_id -> users.id is ON DELETE CASCADE.
    Schema-aware and constraint-agnostic (like active_tariffs).
    Returns True if guard ran (check or heal), False if early return (no connection, table not found).
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal (notification_marks): {}", e)
        return False

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
              AND tc.table_name = 'notification_marks'
              AND kcu.column_name = 'user_id'
              AND tc.table_schema = kcu.table_schema
              AND ccu.table_name = 'users'
              AND ccu.column_name = 'id';
            """
        )
        target_schema = await _resolve_notification_marks_schema(conn)
        if not target_schema:
            logger.warning("Не найдена таблица notification_marks: FK self-heal пропущен")
            return False

        target_rows = [row for row in rows if row.get("table_schema") == target_schema]
        target_rules = {str((row.get("delete_rule") or "")).upper() for row in target_rows}
        has_cascade = "CASCADE" in target_rules
        has_non_cascade = any(rule and rule != "CASCADE" for rule in target_rules)
        has_one_constraint = len(target_rows) == 1

        if target_rows and has_cascade and not has_non_cascade and has_one_constraint:
            logger.info(
                "FK check ok: {}.notification_marks.user_id -> {}.users.id is CASCADE (constraint={})",
                target_schema,
                target_schema,
                ", ".join(
                    str(row.get("constraint_name"))
                    for row in target_rows
                    if row.get("constraint_name")
                ),
            )
            return True

        logger.warning(
            "Обнаружен drift FK {}.notification_marks.user_id -> {}.users.id (rules={}, constraints={}): применяю self-heal",
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
                  AND tc.table_name = 'notification_marks'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
              LOOP
                EXECUTE format(
                  'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                  '{target_schema}',
                  'notification_marks',
                  r.constraint_name
                );
              END LOOP;
            END $$;

            ALTER TABLE "{target_schema}"."notification_marks"
              ADD CONSTRAINT "fk_notification_marks_user"
              FOREIGN KEY ("user_id") REFERENCES "{target_schema}"."users" ("id") ON DELETE CASCADE;
            """
        )
        logger.info(
            "FK self-heal applied: {}.fk_notification_marks_user -> ON DELETE CASCADE",
            target_schema,
        )
        return True
    except Exception as e:
        logger.error("Ошибка FK self-heal для notification_marks: {}", e, exc_info=True)
        return False


async def ensure_promo_usages_fk_cascade() -> bool:
    """
    Safety net against schema drift:
    ensure promo_usages.user_id -> users.id is ON DELETE CASCADE.
    Schema-aware and constraint-agnostic (like active_tariffs).
    Returns True if guard ran (check or heal), False if early return (no connection, table not found).
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal (promo_usages): {}", e)
        return False

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
              AND tc.table_name = 'promo_usages'
              AND kcu.column_name = 'user_id'
              AND tc.table_schema = kcu.table_schema
              AND ccu.table_name = 'users'
              AND ccu.column_name = 'id';
            """
        )
        target_schema = await _resolve_promo_usages_schema(conn)
        if not target_schema:
            logger.warning("Не найдена таблица promo_usages: FK self-heal пропущен")
            return False

        target_rows = [row for row in rows if row.get("table_schema") == target_schema]
        target_rules = {str((row.get("delete_rule") or "")).upper() for row in target_rows}
        has_cascade = "CASCADE" in target_rules
        has_non_cascade = any(rule and rule != "CASCADE" for rule in target_rules)
        has_one_constraint = len(target_rows) == 1

        if target_rows and has_cascade and not has_non_cascade and has_one_constraint:
            logger.info(
                "FK check ok: {}.promo_usages.user_id -> {}.users.id is CASCADE (constraint={})",
                target_schema,
                target_schema,
                ", ".join(
                    str(row.get("constraint_name"))
                    for row in target_rows
                    if row.get("constraint_name")
                ),
            )
            return True

        logger.warning(
            "Обнаружен drift FK {}.promo_usages.user_id -> {}.users.id (rules={}, constraints={}): применяю self-heal",
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
                  AND tc.table_name = 'promo_usages'
                  AND kcu.column_name = 'user_id'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
              LOOP
                EXECUTE format(
                  'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                  '{target_schema}',
                  'promo_usages',
                  r.constraint_name
                );
              END LOOP;
            END $$;

            ALTER TABLE "{target_schema}"."promo_usages"
              ADD CONSTRAINT "fk_promo_usages_user"
              FOREIGN KEY ("user_id") REFERENCES "{target_schema}"."users" ("id") ON DELETE CASCADE;
            """
        )
        logger.info(
            "FK self-heal applied: {}.fk_promo_usages_user -> ON DELETE CASCADE",
            target_schema,
        )
        return True
    except Exception as e:
        logger.error("Ошибка FK self-heal для promo_usages: {}", e, exc_info=True)
        return False


async def ensure_users_referred_by_fk_set_null() -> bool:
    """
    Safety net against schema drift:
    ensure users.referred_by -> users.id is ON DELETE SET NULL.
    Schema-aware and constraint-agnostic.
    Returns True if guard ran (check or heal), False if early return (no connection, table not found).
    """
    try:
        conn = Tortoise.get_connection("default")
    except Exception as e:
        logger.warning("Не удалось получить DB connection для FK self-heal (users.referred_by): {}", e)
        return False

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
              AND tc.table_name = 'users'
              AND kcu.column_name = 'referred_by'
              AND tc.table_schema = kcu.table_schema
              AND ccu.table_name = 'users'
              AND ccu.column_name = 'id';
            """
        )
        target_schema = await _resolve_users_schema(conn)
        if not target_schema:
            logger.warning("Не найдена таблица users: FK self-heal пропущен")
            return False

        target_rows = [row for row in rows if row.get("table_schema") == target_schema]
        target_rules = {str((row.get("delete_rule") or "")).upper() for row in target_rows}
        has_set_null = "SET NULL" in target_rules
        has_non_set_null = any(rule and rule != "SET NULL" for rule in target_rules)
        has_one_constraint = len(target_rows) == 1

        if target_rows and has_set_null and not has_non_set_null and has_one_constraint:
            logger.info(
                "FK check ok: {}.users.referred_by -> {}.users.id is SET NULL (constraint={})",
                target_schema,
                target_schema,
                ", ".join(
                    str(row.get("constraint_name"))
                    for row in target_rows
                    if row.get("constraint_name")
                ),
            )
            return True

        logger.warning(
            "Обнаружен drift FK {}.users.referred_by -> {}.users.id (rules={}, constraints={}): применяю self-heal",
            target_schema,
            target_schema,
            sorted(target_rules) if target_rules else ["MISSING"],
            ", ".join(
                str(row.get("constraint_name")) for row in target_rows if row.get("constraint_name")
            ) or "NONE",
        )
        await conn.execute_script(
            f"""
            UPDATE "{target_schema}"."users" child
            SET "referred_by" = NULL
            WHERE child."referred_by" IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM "{target_schema}"."users" parent
                WHERE parent."id" = child."referred_by"
              );

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
                  AND tc.table_name = 'users'
                  AND kcu.column_name = 'referred_by'
                  AND ccu.table_name = 'users'
                  AND ccu.column_name = 'id'
              LOOP
                EXECUTE format(
                  'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
                  '{target_schema}',
                  'users',
                  r.constraint_name
                );
              END LOOP;
            END $$;

            ALTER TABLE "{target_schema}"."users"
              ADD CONSTRAINT "users_referred_by_foreign"
              FOREIGN KEY ("referred_by") REFERENCES "{target_schema}"."users" ("id") ON DELETE SET NULL;
            """
        )
        logger.info(
            "FK self-heal applied: {}.users_referred_by_foreign -> ON DELETE SET NULL",
            target_schema,
        )
        return True
    except Exception as e:
        logger.error("Ошибка FK self-heal для users.referred_by: {}", e, exc_info=True)
        return False
