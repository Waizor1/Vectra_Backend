function isAdminRequest(req) {
  const accountability = req?.accountability;
  return Boolean(accountability && accountability.admin === true);
}

function rowsFromRaw(raw) {
  if (!raw) return [];
  if (Array.isArray(raw.rows)) return raw.rows;
  if (Array.isArray(raw)) return raw;
  return [];
}

async function cmdFkUsersOverview(database) {
  const raw = await database.raw(
    `
    SELECT
      tc.table_name,
      kcu.column_name,
      tc.constraint_name,
      rc.delete_rule
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.constraint_schema = kcu.constraint_schema
    JOIN information_schema.constraint_column_usage AS ccu
      ON tc.constraint_name = ccu.constraint_name
     AND tc.constraint_schema = ccu.constraint_schema
    JOIN information_schema.referential_constraints AS rc
      ON tc.constraint_name = rc.constraint_name
     AND tc.constraint_schema = rc.constraint_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND ccu.table_name = 'users'
    ORDER BY tc.table_name, tc.constraint_name;
    `
  );
  return rowsFromRaw(raw);
}

async function cmdFkActiveTariffs(database) {
  const raw = await database.raw(
    `
    SELECT
      tc.table_schema,
      tc.constraint_name,
      rc.delete_rule,
      pg_get_constraintdef(con.oid) AS definition
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
    JOIN pg_namespace ns
      ON ns.nspname = tc.table_schema
    JOIN pg_class rel
      ON rel.relname = tc.table_name
     AND rel.relnamespace = ns.oid
    JOIN pg_constraint con
      ON con.conname = tc.constraint_name
     AND con.connamespace = ns.oid
     AND con.conrelid = rel.oid
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_name = 'active_tariffs'
      AND kcu.column_name = 'user_id'
      AND ccu.table_name = 'users'
      AND ccu.column_name = 'id'
    ORDER BY tc.table_schema, tc.constraint_name;
    `
  );
  return rowsFromRaw(raw);
}

async function cmdFixFkActiveTariffs(database) {
  await database.raw(
    `
    DO $$
    DECLARE
      target_schema text;
      r RECORD;
    BEGIN
      SELECT n.nspname
        INTO target_schema
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relname = 'active_tariffs'
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
    END $$;
    `
  );

  return await cmdFkActiveTariffs(database);
}

async function cmdFkNotificationMarks(database) {
  const raw = await database.raw(
    `
    SELECT
      tc.table_schema,
      tc.constraint_name,
      rc.delete_rule,
      pg_get_constraintdef(con.oid) AS definition
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
    JOIN pg_namespace ns
      ON ns.nspname = tc.table_schema
    JOIN pg_class rel
      ON rel.relname = tc.table_name
     AND rel.relnamespace = ns.oid
    JOIN pg_constraint con
      ON con.conname = tc.constraint_name
     AND con.connamespace = ns.oid
     AND con.conrelid = rel.oid
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_name = 'notification_marks'
      AND kcu.column_name = 'user_id'
      AND ccu.table_name = 'users'
      AND ccu.column_name = 'id'
    ORDER BY tc.table_schema, tc.constraint_name;
    `
  );
  return rowsFromRaw(raw);
}

async function cmdFixFkNotificationMarks(database) {
  await database.raw(
    `
    DO $$
    DECLARE
      target_schema text;
      r RECORD;
    BEGIN
      SELECT n.nspname
        INTO target_schema
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relname = 'notification_marks'
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
        'DELETE FROM %I.%I nm WHERE nm.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I u WHERE u.%I = nm.%I)',
        target_schema,
        'notification_marks',
        'user_id',
        target_schema,
        'users',
        'id',
        'user_id'
      );

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
    END $$;
    `
  );

  return await cmdFkNotificationMarks(database);
}

async function cmdFkUsersReferredBy(database) {
  const raw = await database.raw(
    `
    SELECT
      tc.table_schema,
      tc.constraint_name,
      rc.delete_rule,
      pg_get_constraintdef(con.oid) AS definition
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
    JOIN pg_namespace ns
      ON ns.nspname = tc.table_schema
    JOIN pg_class rel
      ON rel.relname = tc.table_name
     AND rel.relnamespace = ns.oid
    JOIN pg_constraint con
      ON con.conname = tc.constraint_name
     AND con.connamespace = ns.oid
     AND con.conrelid = rel.oid
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_name = 'users'
      AND kcu.column_name = 'referred_by'
      AND ccu.table_name = 'users'
      AND ccu.column_name = 'id'
    ORDER BY tc.table_schema, tc.constraint_name;
    `
  );
  return rowsFromRaw(raw);
}

async function cmdFixFkUsersReferredBy(database) {
  await database.raw(
    `
    DO $$
    DECLARE
      target_schema text;
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
        'UPDATE %I.%I u SET %I = NULL WHERE u.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %I.%I ref WHERE ref.%I = u.%I)',
        target_schema,
        'users',
        'referred_by',
        'referred_by',
        target_schema,
        'users',
        'id',
        'referred_by'
      );

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
    `
  );

  return await cmdFkUsersReferredBy(database);
}

async function cmdFamilyQuickHealth(database) {
  const [members, invites, devices, audit] = await Promise.all([
    database("family_members").count("* as count").first(),
    database("family_invites").count("* as count").first(),
    database("family_devices").count("* as count").first(),
    database("family_audit_logs").count("* as count").first(),
  ]);

  return [
    { metric: "family_members", count: Number(members?.count ?? 0) },
    { metric: "family_invites", count: Number(invites?.count ?? 0) },
    { metric: "family_devices", count: Number(devices?.count ?? 0) },
    { metric: "family_audit_logs", count: Number(audit?.count ?? 0) },
  ];
}

const COMMANDS = {
  fk_users_overview: {
    label: "Проверить все FK -> users",
    run: cmdFkUsersOverview,
  },
  fk_active_tariffs: {
    label: "Проверить fk_active_tariffs_user",
    run: cmdFkActiveTariffs,
  },
  fix_fk_active_tariffs: {
    label: "Исправить fk_active_tariffs_user (CASCADE)",
    run: cmdFixFkActiveTariffs,
  },
  fk_notification_marks: {
    label: "Проверить fk_notification_marks_user",
    run: cmdFkNotificationMarks,
  },
  fix_fk_notification_marks: {
    label: "Исправить fk_notification_marks_user (CASCADE)",
    run: cmdFixFkNotificationMarks,
  },
  fk_users_referred_by: {
    label: "Проверить fk_users_referred_by",
    run: cmdFkUsersReferredBy,
  },
  fix_fk_users_referred_by: {
    label: "Исправить fk_users_referred_by (SET NULL)",
    run: cmdFixFkUsersReferredBy,
  },
  family_quick_health: {
    label: "Family quick health (counts)",
    run: cmdFamilyQuickHealth,
  },
};

export default function registerEndpoint(router, { database }) {
  router.get("/commands", async (req, res) => {
    if (!isAdminRequest(req)) return res.status(403).json({ error: "Admin access required" });
    const list = Object.entries(COMMANDS).map(([id, item]) => ({ id, label: item.label }));
    res.json({ commands: list });
  });

  router.post("/run", async (req, res) => {
    if (!isAdminRequest(req)) return res.status(403).json({ error: "Admin access required" });
    try {
      const commandId = String(req?.body?.commandId || "");
      const command = COMMANDS[commandId];
      if (!command) return res.status(400).json({ error: "Unknown commandId" });
      const output = await command.run(database);
      return res.json({
        ok: true,
        commandId,
        label: command.label,
        output,
        executedAt: new Date().toISOString(),
      });
    } catch (error) {
      console.error("[server-ops] command run failed", {
        commandId: String(req?.body?.commandId || ""),
        error,
      });
      return res.status(500).json({ ok: false, error: "Failed to run server operation" });
    }
  });
}
