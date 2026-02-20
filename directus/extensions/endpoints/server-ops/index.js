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
    JOIN pg_constraint con
      ON con.conname = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_name = 'active_tariffs'
      AND kcu.column_name = 'user_id'
      AND ccu.table_name = 'users'
      AND ccu.column_name = 'id'
    ORDER BY tc.constraint_name;
    `
  );
  return rowsFromRaw(raw);
}

async function cmdFixFkActiveTariffs(database) {
  await database.raw(
    `
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
    `
  );

  return await cmdFkActiveTariffs(database);
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
      const message = error instanceof Error ? error.message : "Unknown server-ops error";
      return res.status(500).json({ ok: false, error: message });
    }
  });
}
