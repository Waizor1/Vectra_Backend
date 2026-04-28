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

function getAdminIntegrationConfig() {
  const baseUrl = String(process.env.ADMIN_INTEGRATION_URL || "").trim().replace(/\/+$/, "");
  const token = String(process.env.ADMIN_INTEGRATION_TOKEN || "").trim();
  return { baseUrl, token };
}

async function callBackend(method, path, body) {
  const { baseUrl, token } = getAdminIntegrationConfig();
  if (!baseUrl || !token) {
    const error = new Error("ADMIN_INTEGRATION_URL / ADMIN_INTEGRATION_TOKEN are not configured");
    error.status = 503;
    throw error;
  }

  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Integration-Token": token,
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const rawText = await res.text().catch(() => "");
  const contentType = String(res.headers.get("content-type") || "").toLowerCase();
  const payload = contentType.includes("application/json") && rawText
    ? JSON.parse(rawText)
    : rawText;

  if (!res.ok) {
    const detail =
      payload?.detail ||
      payload?.error ||
      payload?.message ||
      rawText ||
      `Admin integration request failed with ${res.status}`;
    const error = new Error(String(detail));
    error.status = res.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

function buildHwidActor(req) {
  const accountability = req?.accountability || {};
  const actor = {
    directus_user_id: accountability?.user != null ? String(accountability.user) : undefined,
    directus_role_id: accountability?.role != null ? String(accountability.role) : undefined,
    is_admin: accountability?.admin === true,
  };

  if (typeof accountability?.email === "string" && accountability.email.trim()) {
    actor.email = accountability.email.trim();
  }

  const firstName = typeof accountability?.first_name === "string" ? accountability.first_name.trim() : "";
  const lastName = typeof accountability?.last_name === "string" ? accountability.last_name.trim() : "";
  const fullName = [firstName, lastName].filter(Boolean).join(" ").trim();
  if (fullName) {
    actor.name = fullName;
  } else if (typeof accountability?.name === "string" && accountability.name.trim()) {
    actor.name = accountability.name.trim();
  }

  return actor;
}

function normalizeErrorResponse(error, fallbackMessage) {
  const status = Number(error?.status);
  const safeStatus = Number.isInteger(status) && status >= 400 ? status : 500;
  const message = error instanceof Error ? error.message : fallbackMessage;
  return {
    status: safeStatus,
    body: {
      ok: false,
      error: message || fallbackMessage,
    },
  };
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

  router.post("/hwid/preview", async (req, res) => {
    if (!isAdminRequest(req)) return res.status(403).json({ error: "Admin access required" });
    try {
      const hwid = String(req?.body?.hwid || "").trim();
      if (!hwid) {
        return res.status(400).json({ ok: false, error: "HWID is required" });
      }
      const response = await callBackend("POST", "/admin/integration/hwid/preview", { hwid });
      return res.json(response);
    } catch (error) {
      console.error("[server-ops] hwid preview failed", {
        hwid: String(req?.body?.hwid || ""),
        error,
      });
      const normalized = normalizeErrorResponse(error, "Failed to preview HWID purge");
      return res.status(normalized.status).json(normalized.body);
    }
  });

  router.post("/hwid/purge", async (req, res) => {
    if (!isAdminRequest(req)) return res.status(403).json({ error: "Admin access required" });
    try {
      const hwid = String(req?.body?.hwid || "").trim();
      if (!hwid) {
        return res.status(400).json({ ok: false, error: "HWID is required" });
      }

      const reasonRaw = req?.body?.reason;
      const reason = typeof reasonRaw === "string" ? reasonRaw.trim() : "";
      const response = await callBackend("POST", "/admin/integration/hwid/purge", {
        hwid,
        reason: reason || null,
        actor: buildHwidActor(req),
      });
      return res.json(response);
    } catch (error) {
      console.error("[server-ops] hwid purge failed", {
        hwid: String(req?.body?.hwid || ""),
        error,
      });
      const normalized = normalizeErrorResponse(error, "Failed to purge HWID");
      return res.status(normalized.status).json(normalized.body);
    }
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
