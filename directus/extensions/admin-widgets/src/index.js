import crypto from "crypto";

function isAdminRequest(req) {
  const accountability = req?.accountability;
  return Boolean(accountability && accountability.admin === true);
}

const toUtcDate = (value) => {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d;
};

const startOfDayUtc = (d) =>
  new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 0, 0, 0));

const addSeconds = (d, seconds) => new Date(d.getTime() + seconds * 1000);

const ensurePeriod = (period) => {
  const allowed = new Set(["day", "week", "month", "year"]);
  return allowed.has(period) ? period : "day";
};

const toInt = (value, fallback, min, max) => {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
};

const toFiniteNumber = (value) => {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
};

const getMinDate = async (database, table, column) => {
  const row = await database(table).min({ min_date: column }).first();
  return row?.min_date ? new Date(row.min_date) : null;
};

const normalizeRange = async (database, table, column, min, max, clampToDbMin = true) => {
  const now = new Date();
  const minDate = min || new Date(now.getTime() - 360 * 24 * 60 * 60 * 1000);
  const maxDate = max || now;
  const dbMin = await getMinDate(database, table, column);
  let actualStart = minDate;
  if (clampToDbMin && dbMin) {
    actualStart = new Date(Math.max(minDate.getTime(), dbMin.getTime()));
    actualStart = startOfDayUtc(actualStart);
  } else {
    actualStart = startOfDayUtc(actualStart);
  }
  return { actualStart, maxDate };
};

const PROMO_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PROMO_CODE_GENERATED_PART_LEN = 8;
const PROMO_CODE_DEFAULT_LIMIT = 25;
const PROMO_CODE_CREATE_MAX_COUNT = 150;
const PROMO_CODE_MANUAL_REGEX = /^[A-Z0-9_-]{4,64}$/;

const normalizePromoDate = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const asDate = new Date(value);
  if (Number.isNaN(asDate.getTime())) return null;
  return asDate.toISOString().slice(0, 10);
};

const normalizePlainObject = (value, maxJsonLength = 10000) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  try {
    const serialized = JSON.stringify(value);
    if (!serialized || serialized.length > maxJsonLength) return {};
    return JSON.parse(serialized);
  } catch (_err) {
    return {};
  }
};

const sanitizePromoPrefix = (value) => {
  const raw = String(value ?? "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9-]/g, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return raw.slice(0, 12);
};

const sanitizePromoName = (value) => {
  const raw = String(value ?? "").trim().replace(/\s+/g, " ");
  return raw.slice(0, 255);
};

const normalizeManualPromoCode = (value) => String(value ?? "").trim().toUpperCase();

const parseManualPromoCodes = (value, maxCount = PROMO_CODE_CREATE_MAX_COUNT) => {
  const chunks = Array.isArray(value)
    ? value.flatMap((item) => String(item ?? "").split(/[\n,;]+/g))
    : String(value ?? "").split(/[\n,;]+/g);

  const codes = [];
  const invalid = [];
  const seen = new Set();
  const duplicateSet = new Set();

  for (const chunk of chunks) {
    const code = normalizeManualPromoCode(chunk);
    if (!code) continue;

    if (!PROMO_CODE_MANUAL_REGEX.test(code)) {
      invalid.push(code);
      continue;
    }

    if (seen.has(code)) {
      duplicateSet.add(code);
      continue;
    }

    seen.add(code);
    codes.push(code);
  }

  return {
    codes,
    invalid,
    duplicates: Array.from(duplicateSet),
    tooMany: codes.length > maxCount,
  };
};

const randomPromoChunk = (len) => {
  const bytes = crypto.randomBytes(len);
  let out = "";
  for (let i = 0; i < len; i += 1) {
    out += PROMO_CODE_ALPHABET[bytes[i] % PROMO_CODE_ALPHABET.length];
  }
  return out;
};

const buildPromoCode = (prefix) => {
  const payload = randomPromoChunk(PROMO_CODE_GENERATED_PART_LEN);
  return prefix ? `${prefix}-${payload}` : payload;
};

export default function registerEndpoint(router, { database }) {
  router.use((req, res, next) => {
    if (!isAdminRequest(req)) {
      return res.status(403).json({ error: "Admin access required" });
    }
    return next();
  });

  const tableExistsCache = new Map();
  const columnExistsCache = new Map();

  const hasTable = async (tableName) => {
    if (tableExistsCache.has(tableName)) {
      return tableExistsCache.get(tableName);
    }
    try {
      const row = await database("information_schema.tables")
        .select("table_name")
        .where({ table_schema: "public", table_name: tableName })
        .first();
      const exists = !!row;
      tableExistsCache.set(tableName, exists);
      return exists;
    } catch (_err) {
      tableExistsCache.set(tableName, false);
      return false;
    }
  };

  const readCount = async (tableName, whereBuilder) => {
    if (!(await hasTable(tableName))) return 0;
    let qb = database(tableName).count({ count: "*" });
    if (typeof whereBuilder === "function") {
      qb = whereBuilder(qb) || qb;
    }
    const row = await qb.first();
    const value = Number(row?.count ?? 0);
    return Number.isFinite(value) ? value : 0;
  };

  const hasColumn = async (tableName, columnName) => {
    const cacheKey = `${tableName}:${columnName}`;
    if (columnExistsCache.has(cacheKey)) {
      return columnExistsCache.get(cacheKey);
    }
    try {
      const row = await database("information_schema.columns")
        .select("column_name")
        .where({
          table_schema: "public",
          table_name: tableName,
          column_name: columnName,
        })
        .first();
      const exists = !!row;
      columnExistsCache.set(cacheKey, exists);
      return exists;
    } catch (_err) {
      columnExistsCache.set(cacheKey, false);
      return false;
    }
  };

  router.get("/total-users", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "users", "registration_date", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        (
          SELECT COUNT(*)
          FROM users u
          WHERE u.registration_date < ds.report_timestamp + ('1 ' || ?)::interval
        ) AS count
      FROM date_series ds
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  router.get("/active-users", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "users", "registration_date", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        COUNT(u.id) AS count
      FROM date_series ds
      LEFT JOIN users u ON u.registration_date::date <= ds.report_timestamp::date
        AND u.expired_at >= ds.report_timestamp::date + interval '1 day'
        AND u.is_registered = TRUE
      GROUP BY ds.report_timestamp
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  router.get("/inactive-users", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "users", "registration_date", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        COUNT(u.id) AS count
      FROM date_series ds
      LEFT JOIN users u ON u.registration_date <= ds.report_timestamp
        AND u.expired_at <= ds.report_timestamp::date
        AND u.is_registered = TRUE
      GROUP BY ds.report_timestamp
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  router.get("/registered-users", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "users", "registration_date", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        COUNT(u.id) AS count
      FROM date_series ds
      LEFT JOIN users u ON u.registration_date >= ds.report_timestamp
        AND u.registration_date < ds.report_timestamp + ('1 ' || ?)::interval
      GROUP BY ds.report_timestamp
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  router.get("/connections", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "connections", "at", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        COUNT(c.id) AS count
      FROM date_series ds
      LEFT JOIN connections c ON c."at" >= ds.report_timestamp
        AND c."at" < ds.report_timestamp + ('1 ' || ?)::interval
      GROUP BY ds.report_timestamp
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  // Processed payments: count + sum(amount) per period.
  // Used for dashboard "big picture" and payment anomaly alerts.
  router.get("/payments", async (req, res) => {
    const period = ensurePeriod(req.query.period_x_field || "day");
    const min = toUtcDate(req.query.min_x_field);
    const max = toUtcDate(req.query.max_x_field);
    const clampToDbMin = req.query.no_clamp_db_min !== "1";
    const { actualStart, maxDate } = await normalizeRange(database, "processed_payments", "processed_at", min, max, clampToDbMin);
    const queryEnd = addSeconds(maxDate, 1);

    const raw = await database.raw(
      `
      WITH date_series AS (
        SELECT generate_series(
          ?::timestamptz,
          ?::timestamptz,
          ('1 ' || ?)::interval
        ) AS report_timestamp
      )
      SELECT
        to_char(ds.report_timestamp::date, 'DD/MM/YYYY') AS date,
        COUNT(p.id) AS count,
        COALESCE(SUM(p.amount), 0) AS total_amount
      FROM date_series ds
      LEFT JOIN processed_payments p ON p.processed_at >= ds.report_timestamp
        AND p.processed_at < ds.report_timestamp + ('1 ' || ?)::interval
      GROUP BY ds.report_timestamp
      ORDER BY ds.report_timestamp;
      `,
      [actualStart, queryEnd, period, period]
    );
    const results = raw?.rows ?? raw;
    res.json({
      results,
      min_x_field: actualStart.toISOString(),
      max_x_field: maxDate.toISOString(),
      period_x_field: period,
    });
  });

  router.get("/content-ops/summary", async (req, res) => {
    try {
      const expiringDays = toInt(req.query.expiring_days, 7, 1, 90);
      const blockedDays = toInt(req.query.blocked_days, 3, 1, 30);
      const hasUsersBlockedAt = (await hasTable("users")) && (await hasColumn("users", "blocked_at"));

      const usersTotal = await readCount("users");
      const usersBlocked = await readCount("users", (qb) => qb.where("is_blocked", true));
      const usersBlockedRecent = hasUsersBlockedAt
        ? await readCount("users", (qb) =>
            qb
              .where("is_blocked", true)
              .andWhere("blocked_at", ">=", database.raw("NOW() - (? * INTERVAL '1 day')", [blockedDays]))
          )
        : 0;
      const usersExpiringSoon = await readCount("users", (qb) =>
        qb
          .whereNotNull("expired_at")
          .andWhere("expired_at", ">=", database.raw("CURRENT_DATE"))
          .andWhere("expired_at", "<=", database.raw("CURRENT_DATE + (? * INTERVAL '1 day')", [expiringDays]))
      );

      const paymentsTotal = await readCount("processed_payments");
      const paymentsFailed = await readCount("processed_payments", (qb) => qb.whereNot("status", "succeeded"));
      const paymentsSum = await (async () => {
        if (!(await hasTable("processed_payments"))) return 0;
        const row = await database("processed_payments").sum({ total: "amount" }).first();
        return Number(row?.total ?? 0) || 0;
      })();

      const promoActive = await readCount("promo_codes", (qb) =>
        qb
          .where("disabled", false)
          .andWhere((inner) => {
            inner.whereNull("expires_at").orWhere("expires_at", ">=", database.raw("CURRENT_DATE"));
          })
      );
      const promoUsagesRecent = await readCount("promo_usages", (qb) =>
        qb.where("used_at", ">=", database.raw("NOW() - INTERVAL '7 day'"))
      );

      const familyMembers = await readCount("family_members");
      const familyInvitesActive = await readCount("family_invites", (qb) =>
        qb
          .whereNull("revoked_at")
          .andWhere((inner) => {
            inner.whereNull("expires_at").orWhere("expires_at", ">=", database.raw("NOW()"));
          })
      );

      const errorsNew = await readCount("error_reports", (qb) => qb.where("triage_status", "new"));
      const errorsInProgress = await readCount("error_reports", (qb) => qb.where("triage_status", "in_progress"));

      const partnerPending = await readCount("partner_withdrawals", (qb) =>
        qb.whereIn("status", ["created", "processing"])
      );

      res.json({
        expiring_days: expiringDays,
        blocked_days: blockedDays,
        counters: {
          users: {
            total: usersTotal,
            blocked: usersBlocked,
            blocked_recent: usersBlockedRecent,
            expiring_soon: usersExpiringSoon,
          },
          payments: {
            total: paymentsTotal,
            failed: paymentsFailed,
            total_amount: paymentsSum,
          },
          promo: {
            active_codes: promoActive,
            usages_7d: promoUsagesRecent,
          },
          family: {
            members: familyMembers,
            active_invites: familyInvitesActive,
          },
          errors: {
            new: errorsNew,
            in_progress: errorsInProgress,
          },
          partners: {
            pending_withdrawals: partnerPending,
          },
        },
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build content-ops summary", details: String(err?.message || err) });
    }
  });

  router.get("/content-ops/queues", async (req, res) => {
    try {
      const limit = toInt(req.query.limit, 8, 1, 30);
      const expiringDays = toInt(req.query.expiring_days, 7, 1, 90);
      const blockedDays = toInt(req.query.blocked_days, 3, 1, 30);
      const hasUsersBlockedAt = (await hasTable("users")) && (await hasColumn("users", "blocked_at"));

      const usersExpiring = await (async () => {
        if (!(await hasTable("users"))) return [];
        return await database("users")
          .select("id", "username", "full_name", "expired_at", "is_blocked", "balance")
          .whereNotNull("expired_at")
          .andWhere("expired_at", "<=", database.raw("CURRENT_DATE + (? * INTERVAL '1 day')", [expiringDays]))
          .orderBy("expired_at", "asc")
          .limit(limit);
      })();

      const usersBlockedRecent = await (async () => {
        if (!(await hasTable("users"))) return [];
        let qb = database("users")
          .select("id", "username", "full_name", "balance")
          .where("is_blocked", true)
          .limit(limit);
        if (hasUsersBlockedAt) {
          qb = qb
            .select("blocked_at")
            .andWhere("blocked_at", ">=", database.raw("NOW() - (? * INTERVAL '1 day')", [blockedDays]))
            .orderBy("blocked_at", "desc");
        } else {
          qb = qb.select(database.raw("NULL::timestamptz as blocked_at")).orderBy("id", "desc");
        }
        return await qb;
      })();

      const usersTopBalance = await (async () => {
        if (!(await hasTable("users"))) return [];
        return await database("users")
          .select("id", "username", "full_name", "balance", "expired_at")
          .orderBy("balance", "desc")
          .limit(limit);
      })();

      const paymentsRecent = await (async () => {
        if (!(await hasTable("processed_payments"))) return [];
        return await database("processed_payments")
          .select("id", "payment_id", "user_id", "amount", "status", "processed_at")
          .orderBy("processed_at", "desc")
          .limit(limit);
      })();

      const promoUsagesRecent = await (async () => {
        if (!(await hasTable("promo_usages"))) return [];
        return await database("promo_usages")
          .select("id", "promo_code_id", "user_id", "used_at", "context")
          .orderBy("used_at", "desc")
          .limit(limit);
      })();

      const errorsNew = await (async () => {
        if (!(await hasTable("error_reports"))) return [];
        return await database("error_reports")
          .select("id", "created_at", "triage_due_at", "triage_severity", "triage_status", "type", "code", "route", "user_id", "message")
          .where("triage_status", "new")
          .orderBy("created_at", "desc")
          .limit(limit);
      })();

      const partnerWithdrawalsPending = await (async () => {
        if (!(await hasTable("partner_withdrawals"))) return [];
        return await database("partner_withdrawals")
          .select("id", "owner_id", "amount_rub", "method", "status", "created_at")
          .whereIn("status", ["created", "processing"])
          .orderBy("created_at", "desc")
          .limit(limit);
      })();

      res.json({
        limit,
        expiring_days: expiringDays,
        blocked_days: blockedDays,
        queues: {
          users_expiring: usersExpiring,
          users_blocked_recent: usersBlockedRecent,
          users_top_balance: usersTopBalance,
          payments_recent: paymentsRecent,
          promo_usages_recent: promoUsagesRecent,
          errors_new: errorsNew,
          partner_withdrawals_pending: partnerWithdrawalsPending,
        },
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build content-ops queues", details: String(err?.message || err) });
    }
  });

  router.get("/content-ops/search", async (req, res) => {
    try {
      const queryRaw = String(req.query.q ?? "").trim();
      const limit = toInt(req.query.limit, 8, 1, 20);
      if (!queryRaw) {
        res.json({
          query: "",
          limit,
          results: {
            users: [],
            payments: [],
            promo_codes: [],
          },
        });
        return;
      }

      const queryLowerLike = `%${queryRaw.toLowerCase()}%`;
      const isInt = /^[0-9]+$/.test(queryRaw);
      const asInt = isInt ? Number.parseInt(queryRaw, 10) : null;

      const users = await (async () => {
        if (!(await hasTable("users"))) return [];
        const qb = database("users")
          .select("id", "username", "full_name", "balance", "expired_at", "registration_date")
          .limit(limit)
          .orderBy("registration_date", "desc")
          .where((inner) => {
            if (isInt) inner.orWhere("id", asInt);
            inner.orWhereRaw("LOWER(COALESCE(username, '')) LIKE ?", [queryLowerLike]);
            inner.orWhereRaw("LOWER(COALESCE(full_name, '')) LIKE ?", [queryLowerLike]);
          });
        return await qb;
      })();

      const payments = await (async () => {
        if (!(await hasTable("processed_payments"))) return [];
        const qb = database("processed_payments")
          .select("id", "payment_id", "user_id", "amount", "status", "processed_at")
          .limit(limit)
          .orderBy("processed_at", "desc")
          .where((inner) => {
            if (isInt) inner.orWhere("id", asInt);
            inner.orWhereRaw("LOWER(COALESCE(CAST(payment_id AS text), '')) LIKE ?", [queryLowerLike]);
            if (isInt) inner.orWhereRaw("CAST(user_id AS text) = ?", [queryRaw]);
          });
        return await qb;
      })();

      const promoCodes = await (async () => {
        if (!(await hasTable("promo_codes"))) return [];
        const qb = database("promo_codes")
          .select("id", "name", "batch_id", "disabled", "expires_at", "created_at")
          .limit(limit)
          .orderBy("created_at", "desc")
          .where((inner) => {
            if (isInt) inner.orWhere("id", asInt);
            inner.orWhereRaw("LOWER(COALESCE(name, '')) LIKE ?", [queryLowerLike]);
          });
        return await qb;
      })();

      res.json({
        query: queryRaw,
        limit,
        results: {
          users,
          payments,
          promo_codes: promoCodes,
        },
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build content-ops search", details: String(err?.message || err) });
    }
  });

  router.get("/promo-studio/bootstrap", async (_req, res) => {
    try {
      const hasPromoCodes = await hasTable("promo_codes");
      const hasPromoBatches = await hasTable("promo_batches");
      const hasPromoUsages = await hasTable("promo_usages");
      const hasPayments = await hasTable("processed_payments");

      if (!hasPromoCodes) {
        res.json({
          setup_ready: false,
          has_payments_table: hasPayments,
          campaigns: [],
          counters: {
            campaigns_total: 0,
            codes_total: 0,
            active_codes: 0,
            activations_total: 0,
          },
        });
        return;
      }

      const campaigns = await (async () => {
        if (!hasPromoBatches) return [];
        return await database.raw(
          `
          SELECT
            pb.id,
            pb.title,
            pb.notes,
            pb.created_at,
            COUNT(pc.id)::int AS codes_total,
            COUNT(*) FILTER (
              WHERE pc.disabled = FALSE
                AND (pc.expires_at IS NULL OR pc.expires_at >= CURRENT_DATE)
            )::int AS active_codes,
            COUNT(pu.id)::int AS activations_total,
            MAX(pu.used_at) AS last_activation_at
          FROM promo_batches pb
          LEFT JOIN promo_codes pc ON pc.batch_id = pb.id
          LEFT JOIN promo_usages pu ON pu.promo_code_id = pc.id
          GROUP BY pb.id, pb.title, pb.notes, pb.created_at
          ORDER BY pb.created_at DESC, pb.id DESC
          LIMIT 300;
          `
        );
      })();

      const codesTotal = await readCount("promo_codes");
      const activeCodes = await readCount("promo_codes", (qb) =>
        qb
          .where("disabled", false)
          .andWhere((inner) => {
            inner.whereNull("expires_at").orWhere("expires_at", ">=", database.raw("CURRENT_DATE"));
          })
      );
      const activationsTotal = hasPromoUsages ? await readCount("promo_usages") : 0;

      res.json({
        setup_ready: true,
        has_payments_table: hasPayments,
        campaigns: campaigns?.rows ?? campaigns ?? [],
        counters: {
          campaigns_total: hasPromoBatches ? toFiniteNumber((campaigns?.rows ?? campaigns ?? []).length) : 0,
          codes_total: codesTotal,
          active_codes: activeCodes,
          activations_total: activationsTotal,
        },
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build promo-studio bootstrap", details: String(err?.message || err) });
    }
  });

  router.post("/promo-studio/create", async (req, res) => {
    try {
      const hasPromoCodes = await hasTable("promo_codes");
      const hasPromoBatches = await hasTable("promo_batches");
      if (!hasPromoCodes) {
        res.status(400).json({ error: "promo_codes table not found" });
        return;
      }

      const secret = String(process.env.PROMO_HMAC_SECRET || "").trim();
      if (!secret) {
        res.status(500).json({ error: "PROMO_HMAC_SECRET is not configured" });
        return;
      }

      const payload = req.body && typeof req.body === "object" ? req.body : {};
      const codeModeRaw = String(payload.code_mode ?? "generate").trim().toLowerCase();
      const codeMode = codeModeRaw === "manual" ? "manual" : "generate";
      const manualParsed = codeMode === "manual" ? parseManualPromoCodes(payload.manual_codes) : null;

      if (codeMode === "manual") {
        if (manualParsed.invalid.length) {
          const invalidPreview = manualParsed.invalid.slice(0, 8).join(", ");
          const moreInvalid = manualParsed.invalid.length > 8 ? ` (+${manualParsed.invalid.length - 8})` : "";
          res.status(400).json({
            error: "manual_codes contains invalid values",
            details: `Разрешены A-Z, 0-9, '_' и '-'; длина 4-64. Примеры некорректных: ${invalidPreview}${moreInvalid}`,
          });
          return;
        }

        if (!manualParsed || !manualParsed.codes.length) {
          res.status(400).json({ error: "manual_codes is empty", details: "Укажите хотя бы один промокод" });
          return;
        }

        if (manualParsed.tooMany) {
          res.status(400).json({
            error: "manual_codes limit exceeded",
            details: `За один запуск можно создать максимум ${PROMO_CODE_CREATE_MAX_COUNT} кодов`,
          });
          return;
        }
      }

      const count =
        codeMode === "manual"
          ? manualParsed.codes.length
          : toInt(payload.count, 1, 1, PROMO_CODE_CREATE_MAX_COUNT);
      const maxActivations = toInt(payload.max_activations, 1, 1, 5_000_000);
      const perUserLimit = toInt(payload.per_user_limit, 1, 1, 10_000);
      const disabled = payload.disabled === true;
      const expiresAt = normalizePromoDate(payload.expires_at);
      const effects = normalizePlainObject(payload.effects, 20_000);
      const codePrefix = sanitizePromoPrefix(payload.code_prefix || "VECTRA");
      const requestedName = sanitizePromoName(payload.name);

      let batchId = null;
      let batchTitle = "";

      const batchIdRaw = Number.parseInt(String(payload.campaign_id ?? ""), 10);
      if (Number.isFinite(batchIdRaw) && batchIdRaw > 0) {
        if (!hasPromoBatches) {
          res.status(400).json({ error: "promo_batches table not found" });
          return;
        }
        const existingBatch = await database("promo_batches")
          .select("id", "title")
          .where("id", batchIdRaw)
          .first();
        if (!existingBatch) {
          res.status(404).json({ error: "Campaign not found" });
          return;
        }
        batchId = existingBatch.id;
        batchTitle = String(existingBatch.title || "");
      } else {
        const campaignTitle = sanitizePromoName(payload.campaign_title);
        const campaignNotesRaw = String(payload.campaign_notes ?? "").trim();
        const campaignNotes = campaignNotesRaw ? campaignNotesRaw.slice(0, 4000) : null;
        if (campaignTitle) {
          if (!hasPromoBatches) {
            res.status(400).json({ error: "promo_batches table not found" });
            return;
          }
          const insertedBatch = await database("promo_batches")
            .insert({
              title: campaignTitle,
              notes: campaignNotes,
              created_at: new Date(),
            })
            .returning(["id", "title"]);
          const batchRow = Array.isArray(insertedBatch) ? insertedBatch[0] : insertedBatch;
          batchId = batchRow?.id ?? null;
          batchTitle = String(batchRow?.title || campaignTitle);
        }
      }

      const baseName =
        requestedName || sanitizePromoName(batchTitle) || (count === 1 ? "Promo" : "Promo batch");

      const created = [];
      const manualCodes = codeMode === "manual" ? manualParsed.codes : [];

      await database.transaction(async (trx) => {
        for (let idx = 0; idx < count; idx += 1) {
          const manualCode = codeMode === "manual" ? manualCodes[idx] : null;
          let inserted = null;
          const attemptsLimit = codeMode === "manual" ? 1 : 8;
          for (let attempt = 0; attempt < attemptsLimit; attempt += 1) {
            const plainCode = manualCode || buildPromoCode(codePrefix);
            const codeHmac = crypto
              .createHmac("sha256", secret)
              .update(plainCode)
              .digest("hex");
            const rowName = count === 1 ? baseName : `${baseName} #${idx + 1}`;

            const insertPayload = {
              batch_id: batchId,
              name: rowName,
              code_hmac: codeHmac,
              effects,
              max_activations: maxActivations,
              per_user_limit: perUserLimit,
              expires_at: expiresAt,
              disabled,
              created_at: new Date(),
            };

            try {
              const insertedRows = await trx("promo_codes")
                .insert(insertPayload)
                .returning([
                  "id",
                  "name",
                  "batch_id",
                  "max_activations",
                  "per_user_limit",
                  "expires_at",
                  "disabled",
                  "created_at",
                ]);
              const row = Array.isArray(insertedRows) ? insertedRows[0] : insertedRows;
              inserted = {
                id: row?.id ?? null,
                name: row?.name ?? rowName,
                batch_id: row?.batch_id ?? batchId,
                max_activations: row?.max_activations ?? maxActivations,
                per_user_limit: row?.per_user_limit ?? perUserLimit,
                expires_at: row?.expires_at ?? expiresAt,
                disabled: row?.disabled ?? disabled,
                created_at: row?.created_at ?? new Date().toISOString(),
                plain_code: plainCode,
              };
              break;
            } catch (dbErr) {
              const code = String(dbErr?.code || "");
              const message = String(dbErr?.message || "");
              const uniqueConflict =
                code === "23505" ||
                message.toLowerCase().includes("unique") ||
                message.toLowerCase().includes("duplicate");
              if (!uniqueConflict) throw dbErr;
              if (codeMode === "manual") {
                throw new Error(`Промокод "${manualCode}" уже существует`);
              }
            }
          }
          if (!inserted) {
            if (codeMode === "manual") {
              throw new Error(`Не удалось создать промокод "${manualCode}"`);
            }
            throw new Error("Could not generate a unique promo code after several attempts");
          }
          created.push(inserted);
        }
      });

      res.json({
        success: true,
        mode: codeMode,
        created_count: created.length,
        skipped_duplicates: codeMode === "manual" ? manualParsed.duplicates.length : 0,
        campaign: batchId
          ? {
              id: batchId,
              title: batchTitle || null,
            }
          : null,
        created,
      });
    } catch (err) {
      const message = String(err?.message || err);
      if (message.includes("Промокод \"")) {
        res.status(409).json({ error: "manual promo code conflict", details: message });
        return;
      }
      res.status(500).json({ error: "Failed to create promo codes", details: message });
    }
  });

  router.get("/promo-studio/analytics", async (req, res) => {
    try {
      const hasPromoCodes = await hasTable("promo_codes");
      const hasPromoUsages = await hasTable("promo_usages");
      const hasPromoBatches = await hasTable("promo_batches");
      const hasPayments = await hasTable("processed_payments");
      const days = toInt(req.query.days, 30, 1, 365);
      const limit = toInt(req.query.limit, PROMO_CODE_DEFAULT_LIMIT, 5, 200);

      const campaignIdRaw = Number.parseInt(String(req.query.campaign_id ?? ""), 10);
      const hasCampaignFilter = Number.isFinite(campaignIdRaw) && campaignIdRaw > 0;
      const campaignId = hasCampaignFilter ? campaignIdRaw : null;

      if (!hasPromoCodes || !hasPromoUsages) {
        res.json({
          window_days: days,
          limit,
          campaign_id: campaignId,
          has_payments_table: hasPayments,
          summary: {
            codes_total: 0,
            active_codes: 0,
            campaigns_total: 0,
            activations_total: 0,
            unique_users_total: 0,
            attributed_revenue: 0,
            attributed_payments: 0,
            avg_revenue_per_activation: 0,
            avg_revenue_per_user: 0,
          },
          campaigns: [],
          codes: [],
          timeline: [],
        });
        return;
      }

      const scopedFilterSql = hasCampaignFilter ? " AND pc.batch_id = ?" : "";
      const scopedParams = hasCampaignFilter ? [days, campaignId] : [days];
      const codeScopeWhereSql = hasCampaignFilter ? "WHERE pc.batch_id = ?" : "";
      const codeScopeWhereParams = hasCampaignFilter ? [campaignId] : [];

      const summaryRaw = hasPayments
        ? await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            first_touch AS (
              SELECT DISTINCT ON (su.user_id)
                su.user_id,
                su.promo_code_id,
                su.batch_id,
                su.used_at,
                su.id
              FROM scoped_usages su
              ORDER BY su.user_id ASC, su.used_at ASC, su.id ASC
            ),
            revenue AS (
              SELECT
                COALESCE(SUM(pp.amount), 0) AS revenue_total,
                COUNT(pp.id)::int AS payments_total
              FROM first_touch ft
              LEFT JOIN processed_payments pp
                ON pp.user_id = ft.user_id
               AND pp.status = 'succeeded'
               AND pp.processed_at >= ft.used_at
            )
            SELECT
              (SELECT COUNT(*)::int FROM scoped_usages) AS activations_total,
              (SELECT COUNT(DISTINCT user_id)::int FROM scoped_usages) AS unique_users_total,
              (SELECT COALESCE(revenue_total, 0) FROM revenue) AS attributed_revenue,
              (SELECT COALESCE(payments_total, 0) FROM revenue) AS attributed_payments;
            `,
            scopedParams
          )
        : await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            )
            SELECT
              (SELECT COUNT(*)::int FROM scoped_usages) AS activations_total,
              (SELECT COUNT(DISTINCT user_id)::int FROM scoped_usages) AS unique_users_total,
              0::numeric AS attributed_revenue,
              0::int AS attributed_payments;
            `,
            scopedParams
          );

      const summaryRow = (summaryRaw?.rows ?? summaryRaw ?? [])[0] || {};

      const codesTotal = await readCount("promo_codes", (qb) => {
        if (hasCampaignFilter) {
          return qb.where("batch_id", campaignId);
        }
        return qb;
      });

      const activeCodes = await readCount("promo_codes", (qb) => {
        const scoped = hasCampaignFilter ? qb.where("batch_id", campaignId) : qb;
        return scoped
          .andWhere("disabled", false)
          .andWhere((inner) => {
            inner.whereNull("expires_at").orWhere("expires_at", ">=", database.raw("CURRENT_DATE"));
          });
      });

      const campaignsTotal = await (async () => {
        if (hasCampaignFilter) return campaignId ? 1 : 0;
        if (!hasPromoBatches) return 0;
        return await readCount("promo_batches");
      })();

      const campaignsRaw = hasPayments
        ? await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            campaign_activations AS (
              SELECT
                su.batch_id,
                COUNT(*)::int AS activations,
                COUNT(DISTINCT su.user_id)::int AS unique_users
              FROM scoped_usages su
              GROUP BY su.batch_id
            ),
            first_touch AS (
              SELECT DISTINCT ON (su.user_id)
                su.user_id,
                su.batch_id,
                su.used_at,
                su.id
              FROM scoped_usages su
              ORDER BY su.user_id ASC, su.used_at ASC, su.id ASC
            ),
            campaign_revenue AS (
              SELECT
                ft.batch_id,
                COALESCE(SUM(pp.amount), 0) AS revenue,
                COUNT(pp.id)::int AS payments
              FROM first_touch ft
              LEFT JOIN processed_payments pp
                ON pp.user_id = ft.user_id
               AND pp.status = 'succeeded'
               AND pp.processed_at >= ft.used_at
              GROUP BY ft.batch_id
            ),
            campaign_codes AS (
              SELECT
                pc.batch_id,
                COUNT(*)::int AS codes_total,
                COUNT(*) FILTER (
                  WHERE pc.disabled = FALSE
                    AND (pc.expires_at IS NULL OR pc.expires_at >= CURRENT_DATE)
                )::int AS active_codes
              FROM promo_codes pc
              GROUP BY pc.batch_id
            )
            SELECT
              ca.batch_id AS campaign_id,
              COALESCE(pb.title, 'Без кампании') AS campaign_title,
              COALESCE(cc.codes_total, 0)::int AS codes_total,
              COALESCE(cc.active_codes, 0)::int AS active_codes,
              ca.activations,
              ca.unique_users,
              COALESCE(cr.revenue, 0) AS attributed_revenue,
              COALESCE(cr.payments, 0)::int AS attributed_payments
            FROM campaign_activations ca
            LEFT JOIN promo_batches pb ON pb.id = ca.batch_id
            LEFT JOIN campaign_codes cc ON cc.batch_id IS NOT DISTINCT FROM ca.batch_id
            LEFT JOIN campaign_revenue cr ON cr.batch_id IS NOT DISTINCT FROM ca.batch_id
            ORDER BY ca.activations DESC, COALESCE(cr.revenue, 0) DESC, campaign_title ASC
            LIMIT ?;
            `,
            [...scopedParams, limit]
          )
        : await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            campaign_activations AS (
              SELECT
                su.batch_id,
                COUNT(*)::int AS activations,
                COUNT(DISTINCT su.user_id)::int AS unique_users
              FROM scoped_usages su
              GROUP BY su.batch_id
            ),
            campaign_codes AS (
              SELECT
                pc.batch_id,
                COUNT(*)::int AS codes_total,
                COUNT(*) FILTER (
                  WHERE pc.disabled = FALSE
                    AND (pc.expires_at IS NULL OR pc.expires_at >= CURRENT_DATE)
                )::int AS active_codes
              FROM promo_codes pc
              GROUP BY pc.batch_id
            )
            SELECT
              ca.batch_id AS campaign_id,
              COALESCE(pb.title, 'Без кампании') AS campaign_title,
              COALESCE(cc.codes_total, 0)::int AS codes_total,
              COALESCE(cc.active_codes, 0)::int AS active_codes,
              ca.activations,
              ca.unique_users,
              0::numeric AS attributed_revenue,
              0::int AS attributed_payments
            FROM campaign_activations ca
            LEFT JOIN promo_batches pb ON pb.id = ca.batch_id
            LEFT JOIN campaign_codes cc ON cc.batch_id IS NOT DISTINCT FROM ca.batch_id
            ORDER BY ca.activations DESC, campaign_title ASC
            LIMIT ?;
            `,
            [...scopedParams, limit]
          );

      const codesRaw = hasPayments
        ? await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            code_activations AS (
              SELECT
                su.promo_code_id,
                COUNT(*)::int AS activations,
                COUNT(DISTINCT su.user_id)::int AS unique_users,
                MAX(su.used_at) AS last_used_at
              FROM scoped_usages su
              GROUP BY su.promo_code_id
            ),
            first_touch AS (
              SELECT DISTINCT ON (su.user_id)
                su.user_id,
                su.promo_code_id,
                su.used_at,
                su.id
              FROM scoped_usages su
              ORDER BY su.user_id ASC, su.used_at ASC, su.id ASC
            ),
            code_revenue AS (
              SELECT
                ft.promo_code_id,
                COALESCE(SUM(pp.amount), 0) AS revenue,
                COUNT(pp.id)::int AS payments
              FROM first_touch ft
              LEFT JOIN processed_payments pp
                ON pp.user_id = ft.user_id
               AND pp.status = 'succeeded'
               AND pp.processed_at >= ft.used_at
              GROUP BY ft.promo_code_id
            ),
            code_usage_total AS (
              SELECT
                pu.promo_code_id,
                COUNT(*)::int AS used_total
              FROM promo_usages pu
              GROUP BY pu.promo_code_id
            )
            SELECT
              pc.id AS promo_code_id,
              pc.name,
              pc.batch_id AS campaign_id,
              COALESCE(pb.title, 'Без кампании') AS campaign_title,
              COALESCE(ca.activations, 0)::int AS activations,
              COALESCE(ca.unique_users, 0)::int AS unique_users,
              COALESCE(cr.revenue, 0) AS attributed_revenue,
              COALESCE(cr.payments, 0)::int AS attributed_payments,
              COALESCE(ut.used_total, 0)::int AS used_total,
              pc.max_activations,
              pc.per_user_limit,
              pc.expires_at,
              pc.disabled,
              pc.created_at,
              ca.last_used_at,
              CASE
                WHEN pc.disabled THEN 'disabled'
                WHEN pc.expires_at IS NOT NULL AND pc.expires_at < CURRENT_DATE THEN 'expired'
                ELSE 'active'
              END AS status,
              GREATEST(pc.max_activations - COALESCE(ut.used_total, 0), 0)::int AS remaining_activations
            FROM promo_codes pc
            LEFT JOIN promo_batches pb ON pb.id = pc.batch_id
            LEFT JOIN code_activations ca ON ca.promo_code_id = pc.id
            LEFT JOIN code_revenue cr ON cr.promo_code_id = pc.id
            LEFT JOIN code_usage_total ut ON ut.promo_code_id = pc.id
            ${codeScopeWhereSql}
            ORDER BY COALESCE(ca.activations, 0) DESC, COALESCE(cr.revenue, 0) DESC, pc.id DESC
            LIMIT ?;
            `,
            [...scopedParams, ...codeScopeWhereParams, limit]
          )
        : await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            code_activations AS (
              SELECT
                su.promo_code_id,
                COUNT(*)::int AS activations,
                COUNT(DISTINCT su.user_id)::int AS unique_users,
                MAX(su.used_at) AS last_used_at
              FROM scoped_usages su
              GROUP BY su.promo_code_id
            ),
            code_usage_total AS (
              SELECT
                pu.promo_code_id,
                COUNT(*)::int AS used_total
              FROM promo_usages pu
              GROUP BY pu.promo_code_id
            )
            SELECT
              pc.id AS promo_code_id,
              pc.name,
              pc.batch_id AS campaign_id,
              COALESCE(pb.title, 'Без кампании') AS campaign_title,
              COALESCE(ca.activations, 0)::int AS activations,
              COALESCE(ca.unique_users, 0)::int AS unique_users,
              0::numeric AS attributed_revenue,
              0::int AS attributed_payments,
              COALESCE(ut.used_total, 0)::int AS used_total,
              pc.max_activations,
              pc.per_user_limit,
              pc.expires_at,
              pc.disabled,
              pc.created_at,
              ca.last_used_at,
              CASE
                WHEN pc.disabled THEN 'disabled'
                WHEN pc.expires_at IS NOT NULL AND pc.expires_at < CURRENT_DATE THEN 'expired'
                ELSE 'active'
              END AS status,
              GREATEST(pc.max_activations - COALESCE(ut.used_total, 0), 0)::int AS remaining_activations
            FROM promo_codes pc
            LEFT JOIN promo_batches pb ON pb.id = pc.batch_id
            LEFT JOIN code_activations ca ON ca.promo_code_id = pc.id
            LEFT JOIN code_usage_total ut ON ut.promo_code_id = pc.id
            ${codeScopeWhereSql}
            ORDER BY COALESCE(ca.activations, 0) DESC, pc.id DESC
            LIMIT ?;
            `,
            [...scopedParams, ...codeScopeWhereParams, limit]
          );

      const timelineRaw = hasPayments
        ? await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            ),
            activation_daily AS (
              SELECT
                date_trunc('day', su.used_at)::date AS day,
                COUNT(*)::int AS activations,
                COUNT(DISTINCT su.user_id)::int AS unique_users
              FROM scoped_usages su
              GROUP BY day
            ),
            first_touch AS (
              SELECT DISTINCT ON (su.user_id)
                su.user_id,
                su.used_at,
                su.id
              FROM scoped_usages su
              ORDER BY su.user_id ASC, su.used_at ASC, su.id ASC
            ),
            revenue_daily AS (
              SELECT
                date_trunc('day', pp.processed_at)::date AS day,
                COALESCE(SUM(pp.amount), 0) AS revenue,
                COUNT(pp.id)::int AS payments
              FROM first_touch ft
              LEFT JOIN processed_payments pp
                ON pp.user_id = ft.user_id
               AND pp.status = 'succeeded'
               AND pp.processed_at >= ft.used_at
               AND pp.processed_at >= NOW() - (? * INTERVAL '1 day')
              GROUP BY day
            )
            SELECT
              to_char(COALESCE(ad.day, rd.day), 'YYYY-MM-DD') AS day,
              COALESCE(ad.activations, 0)::int AS activations,
              COALESCE(ad.unique_users, 0)::int AS unique_users,
              COALESCE(rd.revenue, 0) AS attributed_revenue,
              COALESCE(rd.payments, 0)::int AS attributed_payments
            FROM activation_daily ad
            FULL OUTER JOIN revenue_daily rd ON rd.day = ad.day
            ORDER BY COALESCE(ad.day, rd.day) ASC;
            `,
            [...scopedParams, days]
          )
        : await database.raw(
            `
            WITH scoped_usages AS (
              SELECT
                pu.id,
                pu.user_id,
                pu.promo_code_id,
                pu.used_at,
                pc.batch_id
              FROM promo_usages pu
              JOIN promo_codes pc ON pc.id = pu.promo_code_id
              WHERE pu.used_at >= NOW() - (? * INTERVAL '1 day')
              ${scopedFilterSql}
            )
            SELECT
              to_char(date_trunc('day', su.used_at)::date, 'YYYY-MM-DD') AS day,
              COUNT(*)::int AS activations,
              COUNT(DISTINCT su.user_id)::int AS unique_users,
              0::numeric AS attributed_revenue,
              0::int AS attributed_payments
            FROM scoped_usages su
            GROUP BY date_trunc('day', su.used_at)::date
            ORDER BY date_trunc('day', su.used_at)::date ASC;
            `,
            scopedParams
          );

      const activationsTotal = toFiniteNumber(summaryRow.activations_total);
      const uniqueUsersTotal = toFiniteNumber(summaryRow.unique_users_total);
      const attributedRevenue = toFiniteNumber(summaryRow.attributed_revenue);
      const attributedPayments = toFiniteNumber(summaryRow.attributed_payments);

      res.json({
        window_days: days,
        limit,
        campaign_id: campaignId,
        has_payments_table: hasPayments,
        summary: {
          codes_total: codesTotal,
          active_codes: activeCodes,
          campaigns_total: campaignsTotal,
          activations_total: activationsTotal,
          unique_users_total: uniqueUsersTotal,
          attributed_revenue: attributedRevenue,
          attributed_payments: attributedPayments,
          avg_revenue_per_activation:
            activationsTotal > 0 ? attributedRevenue / activationsTotal : 0,
          avg_revenue_per_user:
            uniqueUsersTotal > 0 ? attributedRevenue / uniqueUsersTotal : 0,
        },
        campaigns: campaignsRaw?.rows ?? campaignsRaw ?? [],
        codes: codesRaw?.rows ?? codesRaw ?? [],
        timeline: timelineRaw?.rows ?? timelineRaw ?? [],
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build promo-studio analytics", details: String(err?.message || err) });
    }
  });

  // Single comprehensive user card payload for the tvpn-user-card interface.
  // Aggregates identity, login methods, subscription, finance, devices, activity,
  // referrals and risk indicators for one user id. Postgres-only (uses NOW()/interval).
  router.get("/user-card/:user_id", async (req, res) => {
    const userIdRaw = String(req.params.user_id || "").trim();
    if (!/^-?\d+$/.test(userIdRaw)) {
      return res.status(400).json({ error: "Invalid user id" });
    }
    const userId = userIdRaw;
    const WEB_USER_ID_FLOOR = 8_000_000_000_000_000n;

    if (!(await hasTable("users"))) {
      return res.status(404).json({ error: "users table not found" });
    }

    try {
      const userRow = await database("users").where({ id: userId }).first();
      if (!userRow) {
        return res.status(404).json({ error: "User not found" });
      }

      const hasIdentities = await hasTable("auth_identities");
      const hasPasswordCred = await hasTable("auth_password_credentials");
      const hasPayments = await hasTable("processed_payments");
      const hasActiveTariffs = await hasTable("active_tariffs");
      const hasUserDevices = await hasTable("user_devices");
      const hasHwidLocal = await hasTable("hwid_devices_local");
      const hasConnections = await hasTable("connections");
      const hasAuditEvents = await hasTable("auth_audit_events");
      const hasUserDevicesLastOnline = hasUserDevices && (await hasColumn("user_devices", "last_online_at"));

      const [
        authIdentities,
        passwordCred,
        paymentsAgg,
        paymentsAllAgg,
        recentPayments,
        activeTariff,
        devicesAgg,
        connectionsAgg,
        referrer,
        recentAudit,
      ] = await Promise.all([
        hasIdentities
          ? database("auth_identities")
              .where({ user_id: userId })
              .orderBy("linked_at", "asc")
          : Promise.resolve([]),
        hasPasswordCred
          ? database("auth_password_credentials").where({ user_id: userId }).first()
          : Promise.resolve(null),
        hasPayments
          ? database("processed_payments")
              .where({ user_id: userId, status: "succeeded" })
              .select(
                database.raw(
                  `COUNT(*)::int AS count,
                   COALESCE(SUM(amount),0)::numeric AS total_amount,
                   COALESCE(SUM(amount_external),0)::numeric AS total_external,
                   COALESCE(SUM(amount_from_balance),0)::numeric AS total_from_balance,
                   COALESCE(SUM(CASE WHEN processed_at >= NOW() - INTERVAL '30 days' THEN amount ELSE 0 END),0)::numeric AS amount_30d,
                   COALESCE(SUM(CASE WHEN processed_at >= NOW() - INTERVAL '7 days' THEN amount ELSE 0 END),0)::numeric AS amount_7d,
                   MAX(processed_at) AS last_succeeded_at`
                )
              )
              .first()
          : Promise.resolve({}),
        hasPayments
          ? database("processed_payments")
              .where({ user_id: userId })
              .select(
                database.raw(
                  `COUNT(*)::int AS total_count,
                   COUNT(*) FILTER (WHERE status = 'refunded')::int AS refunded_count,
                   COUNT(*) FILTER (WHERE status = 'canceled')::int AS canceled_count,
                   COUNT(*) FILTER (WHERE status = 'pending')::int AS pending_count,
                   COUNT(DISTINCT provider)::int AS providers_used`
                )
              )
              .first()
          : Promise.resolve({}),
        hasPayments
          ? database("processed_payments")
              .where({ user_id: userId })
              .orderBy("processed_at", "desc")
              .limit(8)
              .select(
                "id",
                "payment_id",
                "provider",
                "status",
                "amount",
                "amount_external",
                "amount_from_balance",
                "processed_at"
              )
          : Promise.resolve([]),
        userRow.active_tariff_id && hasActiveTariffs
          ? database("active_tariffs").where({ id: userRow.active_tariff_id }).first()
          : Promise.resolve(null),
        // Devices aggregate — union of two device-inventory sources so the card
        // shows accurate counts for both schemes:
        //   1) user_devices — populated only when device-per-user is enabled.
        //   2) hwid_devices_local — local registry of HWIDs seen on Remnawave
        //      (covers legacy users who never opt into device-per-user).
        // Dedupe by hwid so the same physical device isn't double-counted.
        hasUserDevices || hasHwidLocal
          ? database.raw(
              `WITH d AS (
                  ${hasUserDevices
                    ? `SELECT hwid,
                              ${hasUserDevicesLastOnline ? "last_online_at" : "NULL::timestamptz AS last_online_at"}
                         FROM user_devices
                        WHERE user_id = ? AND hwid IS NOT NULL`
                    : "SELECT NULL::text AS hwid, NULL::timestamptz AS last_online_at WHERE FALSE"}
                  ${hasUserDevices && hasHwidLocal ? "UNION" : ""}
                  ${hasHwidLocal
                    ? `SELECT hwid, last_seen_at AS last_online_at
                         FROM hwid_devices_local
                        WHERE telegram_user_id = ?`
                    : ""}
                )
                SELECT COUNT(DISTINCT hwid)::int AS total,
                       COUNT(DISTINCT hwid) FILTER (
                         WHERE last_online_at >= NOW() - INTERVAL '7 days'
                       )::int AS active_7d,
                       MAX(last_online_at) AS last_online_at
                  FROM d`,
              hasUserDevices && hasHwidLocal
                ? [userId, userId]
                : [userId]
            ).then((raw) => (raw?.rows?.[0] ?? raw?.[0] ?? { total: 0, active_7d: 0, last_online_at: null }))
          : Promise.resolve({ total: 0, active_7d: 0, last_online_at: null }),
        hasConnections
          ? database("connections")
              .where({ user_id: userId })
              .select(
                database.raw(
                  `COUNT(*)::int AS days_total,
                   COUNT(*) FILTER (WHERE "at" >= (NOW() - INTERVAL '30 days')::date)::int AS days_30d,
                   COUNT(*) FILTER (WHERE "at" >= (NOW() - INTERVAL '7 days')::date)::int AS days_7d,
                   MIN("at") AS first_day,
                   MAX("at") AS last_day`
                )
              )
              .first()
          : Promise.resolve({}),
        userRow.referred_by
          ? database("users")
              .where({ id: userRow.referred_by })
              .select("id", "full_name", "username", "is_partner")
              .first()
          : Promise.resolve(null),
        hasAuditEvents
          ? database("auth_audit_events")
              .where({ user_id: userId })
              .orderBy("created_at", "desc")
              .limit(8)
              .select("id", "provider", "action", "result", "reason", "created_at")
          : Promise.resolve([]),
      ]);

      const num = (v) => {
        if (v === null || v === undefined) return 0;
        const n = Number(v);
        return Number.isFinite(n) ? n : 0;
      };
      const iso = (v) => (v ? new Date(v).toISOString() : null);

      // Mirror of Python is_campaign_utm() in bloobcat/funcs/referral_attribution.py:
      // a non-empty utm that is not the generic "partner" marker is a campaign tag.
      const isCampaignUtm = (raw) => {
        const n = String(raw ?? "").trim();
        return n !== "" && n !== "partner";
      };

      // Attribution chain — walk referred_by upward, depth-capped to keep the
      // recursive CTE bounded even on adversarial chains.
      const ATTRIBUTION_CHAIN_MAX_DEPTH = 10;
      let attributionChainRows = [];
      if (userRow.referred_by) {
        try {
          const raw = await database.raw(
            `WITH RECURSIVE chain AS (
                SELECT id, referred_by, utm, full_name, username, is_partner, 0 AS depth
                FROM users WHERE id = ?
                UNION ALL
                SELECT u.id, u.referred_by, u.utm, u.full_name, u.username, u.is_partner, c.depth + 1
                FROM users u
                INNER JOIN chain c ON u.id = c.referred_by
                WHERE c.depth < ?
              )
              SELECT id, referred_by, utm, full_name, username, is_partner, depth
              FROM chain WHERE depth > 0 ORDER BY depth ASC`,
            [userId, ATTRIBUTION_CHAIN_MAX_DEPTH]
          );
          attributionChainRows = raw?.rows ?? raw ?? [];
        } catch (_e) {
          attributionChainRows = [];
        }
      }

      // Downstream count — total descendants attributed to this user via referred_by.
      // Pairs with PR feat/acquisition-source-attribution: campaign roots see the full
      // funnel including multi-hop invitees that inherited their utm tag.
      let downstreamCount = 0;
      try {
        const raw = await database.raw(
          `WITH RECURSIVE descendants AS (
              SELECT id FROM users WHERE referred_by = ?
              UNION ALL
              SELECT u.id FROM users u INNER JOIN descendants d ON u.referred_by = d.id
            )
            SELECT COUNT(*)::int AS count FROM descendants`,
          [userId]
        );
        const rows = raw?.rows ?? raw ?? [];
        downstreamCount = Number(rows[0]?.count ?? 0) || 0;
      } catch (_e) {
        downstreamCount = 0;
      }

      const ownUtmRaw = userRow.utm == null ? null : String(userRow.utm).trim();
      const ownUtm = ownUtmRaw && ownUtmRaw.length > 0 ? ownUtmRaw : null;
      const ownIsCampaign = isCampaignUtm(ownUtm);
      const chainItems = (attributionChainRows || []).map((row) => ({
        id: String(row.id),
        depth: Number(row.depth ?? 0),
        utm: row.utm == null || row.utm === "" ? null : String(row.utm),
        utm_is_campaign: isCampaignUtm(row.utm),
        full_name: row.full_name || null,
        username: row.username || null,
        is_partner: !!row.is_partner,
      }));
      const inheritedAncestor = ownIsCampaign
        ? chainItems.find((item) => item.utm === ownUtm) || null
        : null;
      const campaignRoot = chainItems.find((item) => item.utm_is_campaign) || null;
      let attributionSource;
      if (ownIsCampaign) {
        attributionSource = inheritedAncestor ? "inherited" : "direct";
      } else if (ownUtm === "partner") {
        attributionSource = "partner-default";
      } else if (!ownUtm && userRow.referred_by) {
        attributionSource = "referral-no-utm";
      } else if (!ownUtm) {
        attributionSource = "organic";
      } else {
        attributionSource = "direct";
      }

      const userIdBig = (() => {
        try {
          return BigInt(userIdRaw);
        } catch (_e) {
          return null;
        }
      })();
      const isWebUser = userIdBig !== null && userIdBig >= WEB_USER_ID_FLOOR;

      // Identity providers — derive from id heuristic + auth_identities + password creds.
      // For non-web users the bot user_id IS the telegram subject, so an OAuth
      // auth_identities row with provider='telegram' and matching subject would
      // otherwise produce a visually duplicated card. Merge it into the synthetic
      // primary entry instead of appending a second row.
      const providers = [];
      const telegramIdentityMatch = authIdentities.find(
        (ident) => ident && ident.provider === "telegram" && String(ident.provider_subject) === userIdRaw
      );
      if (!isWebUser) {
        providers.push({
          provider: "telegram",
          external_id: userIdRaw,
          display_name: userRow.full_name || telegramIdentityMatch?.display_name || null,
          username: userRow.username || null,
          email: null,
          email_verified: null,
          linked_at:
            iso(userRow.created_at) ||
            iso(userRow.registration_date) ||
            iso(telegramIdentityMatch?.linked_at),
          last_login_at: iso(telegramIdentityMatch?.last_login_at),
          source: "primary",
          avatar_url: telegramIdentityMatch?.avatar_url || null,
        });
      }
      if (passwordCred) {
        providers.push({
          provider: "password",
          external_id: passwordCred.email_normalized || null,
          display_name: null,
          username: null,
          email: passwordCred.email_normalized || null,
          email_verified: !!passwordCred.email_verified,
          linked_at: iso(passwordCred.created_at),
          last_login_at: iso(passwordCred.updated_at),
          source: "credential",
        });
      }
      for (const ident of authIdentities) {
        if (ident === telegramIdentityMatch && !isWebUser) {
          continue;
        }
        providers.push({
          provider: ident.provider,
          external_id: ident.provider_subject,
          display_name: ident.display_name || null,
          username: null,
          email: ident.email || null,
          email_verified: !!ident.email_verified,
          linked_at: iso(ident.linked_at),
          last_login_at: iso(ident.last_login_at),
          source: "oauth",
          avatar_url: ident.avatar_url || null,
        });
      }

      // Subscription / LTE
      const lteTotal = userRow.lte_gb_total ?? activeTariff?.lte_gb_total ?? 0;
      const lteUsed = num(activeTariff?.lte_gb_used);
      const lteRemaining = Math.max(0, num(lteTotal) - lteUsed);
      const ltePct =
        num(lteTotal) > 0 ? Math.min(100, Math.max(0, (lteUsed / num(lteTotal)) * 100)) : 0;

      const today = new Date();
      today.setUTCHours(0, 0, 0, 0);
      let daysLeft = null;
      if (userRow.expired_at) {
        const exp = new Date(userRow.expired_at);
        exp.setUTCHours(0, 0, 0, 0);
        daysLeft = Math.round((exp.getTime() - today.getTime()) / 86400000);
      }
      const isExpired = daysLeft !== null && daysLeft < 0;
      const isActiveSub = !!userRow.expired_at && !isExpired;

      // Finance
      const totalAmount = num(paymentsAgg?.total_amount);
      const totalExternal = num(paymentsAgg?.total_external);
      const totalFromBalance = num(paymentsAgg?.total_from_balance);
      const amount30d = num(paymentsAgg?.amount_30d);
      const amount7d = num(paymentsAgg?.amount_7d);
      const succeededCount = num(paymentsAgg?.count);
      const refundedCount = num(paymentsAllAgg?.refunded_count);
      const canceledCount = num(paymentsAllAgg?.canceled_count);
      const pendingCount = num(paymentsAllAgg?.pending_count);
      const totalAttempts = num(paymentsAllAgg?.total_count);
      const refundRatio = succeededCount > 0 ? refundedCount / succeededCount : 0;
      const lastPayment =
        Array.isArray(recentPayments) && recentPayments.length > 0 ? recentPayments[0] : null;

      // Risk indicators (soft heuristics, not policy)
      const indicators = [];
      if (userRow.is_blocked) {
        indicators.push({
          level: "warn",
          code: "blocked",
          label: "Заблокирован ботом",
          detail: userRow.blocked_at ? `с ${iso(userRow.blocked_at)}` : null,
        });
      }
      if (userRow.used_trial && !succeededCount) {
        indicators.push({
          level: "info",
          code: "trial-only",
          label: "Использовал триал, без оплат",
          detail: userRow.trial_started_at ? `триал с ${iso(userRow.trial_started_at)}` : null,
        });
      }
      if (refundedCount >= 1 && refundRatio >= 0.34) {
        indicators.push({
          level: "warn",
          code: "high-refund-ratio",
          label: `Высокая доля возвратов: ${(refundRatio * 100).toFixed(0)}%`,
          detail: `${refundedCount} возвратов из ${succeededCount} оплат`,
        });
      }
      if (num(userRow.failed_message_count) >= 5) {
        indicators.push({
          level: "warn",
          code: "delivery-failures",
          label: `${userRow.failed_message_count} подряд неуспешных доставок`,
          detail: userRow.last_failed_message_at ? `последняя ${iso(userRow.last_failed_message_at)}` : null,
        });
      }
      if (num(devicesAgg?.total) > num(userRow.hwid_limit ?? activeTariff?.hwid_limit ?? 0) && num(userRow.hwid_limit ?? activeTariff?.hwid_limit ?? 0) > 0) {
        indicators.push({
          level: "warn",
          code: "devices-over-limit",
          label: `Устройств больше лимита (${devicesAgg.total} > ${userRow.hwid_limit ?? activeTariff?.hwid_limit})`,
          detail: null,
        });
      }
      if (providers.length === 0) {
        indicators.push({
          level: "info",
          code: "no-login-methods",
          label: "Нет связанных методов входа",
          detail: "Ни telegram, ни password, ни OAuth identity",
        });
      } else if (providers.length >= 3) {
        indicators.push({
          level: "info",
          code: "many-login-methods",
          label: `Привязано методов входа: ${providers.length}`,
          detail: providers.map((p) => p.provider).join(", "),
        });
      }
      if (num(userRow.referrals) >= 25) {
        indicators.push({
          level: "info",
          code: "high-referrals",
          label: `Много рефералов: ${userRow.referrals}`,
          detail: null,
        });
      }
      if (num(connectionsAgg?.days_total) === 0 && isActiveSub) {
        indicators.push({
          level: "info",
          code: "active-no-connections",
          label: "Активная подписка, но 0 коннектов",
          detail: null,
        });
      }

      res.json({
        user: {
          id: String(userRow.id),
          username: userRow.username || null,
          full_name: userRow.full_name || null,
          email: userRow.email || null,
          language_code: userRow.language_code || null,
          is_admin: !!userRow.is_admin,
          is_partner: !!userRow.is_partner,
          is_registered: !!userRow.is_registered,
          is_subscribed: !!userRow.is_subscribed,
          is_blocked: !!userRow.is_blocked,
          is_trial: !!userRow.is_trial,
          used_trial: !!userRow.used_trial,
          key_activated: !!userRow.key_activated,
          remnawave_uuid: userRow.remnawave_uuid || null,
          balance: num(userRow.balance),
          custom_referral_percent: num(userRow.custom_referral_percent),
          partner_link_mode: userRow.partner_link_mode || null,
          utm: userRow.utm || null,
          registration_date: iso(userRow.registration_date),
          created_at: iso(userRow.created_at),
          connected_at: iso(userRow.connected_at),
          blocked_at: iso(userRow.blocked_at),
          trial_started_at: iso(userRow.trial_started_at),
          last_hwid_reset: iso(userRow.last_hwid_reset),
          last_failed_message_at: iso(userRow.last_failed_message_at),
          failed_message_count: num(userRow.failed_message_count),
          prize_wheel_attempts: num(userRow.prize_wheel_attempts),
          device_per_user_enabled: userRow.device_per_user_enabled,
          is_web_user: isWebUser,
        },
        providers,
        subscription: {
          expired_at: userRow.expired_at ? new Date(userRow.expired_at).toISOString() : null,
          days_left: daysLeft,
          is_expired: isExpired,
          is_active: isActiveSub,
          active_tariff: activeTariff
            ? {
                id: activeTariff.id,
                name: activeTariff.name || null,
                months: num(activeTariff.months),
                price: num(activeTariff.price),
                hwid_limit: num(activeTariff.hwid_limit),
                lte_price_per_gb: num(activeTariff.lte_price_per_gb),
                lte_autopay_free: !!activeTariff.lte_autopay_free,
              }
            : null,
          hwid_limit_effective:
            userRow.hwid_limit !== null && userRow.hwid_limit !== undefined
              ? num(userRow.hwid_limit)
              : activeTariff?.hwid_limit !== null && activeTariff?.hwid_limit !== undefined
                ? num(activeTariff.hwid_limit)
                : null,
          hwid_limit_user: userRow.hwid_limit !== null && userRow.hwid_limit !== undefined ? num(userRow.hwid_limit) : null,
          lte_total_gb: num(lteTotal),
          lte_used_gb: lteUsed,
          lte_remaining_gb: lteRemaining,
          lte_used_percent: ltePct,
          lte_personal_override: userRow.lte_gb_total !== null && userRow.lte_gb_total !== undefined,
        },
        finance: {
          balance: num(userRow.balance),
          total_succeeded_count: succeededCount,
          total_succeeded_amount: totalAmount,
          total_external_amount: totalExternal,
          total_from_balance_amount: totalFromBalance,
          amount_30d: amount30d,
          amount_7d: amount7d,
          last_succeeded_at: iso(paymentsAgg?.last_succeeded_at),
          refunded_count: refundedCount,
          canceled_count: canceledCount,
          pending_count: pendingCount,
          total_attempts: totalAttempts,
          providers_used: num(paymentsAllAgg?.providers_used),
          refund_ratio: refundRatio,
          last_payment: lastPayment
            ? {
                id: lastPayment.id,
                payment_id: lastPayment.payment_id,
                provider: lastPayment.provider,
                status: lastPayment.status,
                amount: num(lastPayment.amount),
                amount_external: num(lastPayment.amount_external),
                amount_from_balance: num(lastPayment.amount_from_balance),
                processed_at: iso(lastPayment.processed_at),
              }
            : null,
          recent: (recentPayments || []).map((p) => ({
            id: p.id,
            payment_id: p.payment_id,
            provider: p.provider,
            status: p.status,
            amount: num(p.amount),
            amount_external: num(p.amount_external),
            amount_from_balance: num(p.amount_from_balance),
            processed_at: iso(p.processed_at),
          })),
        },
        devices: {
          total: num(devicesAgg?.total),
          active_7d: num(devicesAgg?.active_7d),
          last_online_at: iso(devicesAgg?.last_online_at),
        },
        connections: {
          days_total: num(connectionsAgg?.days_total),
          days_30d: num(connectionsAgg?.days_30d),
          days_7d: num(connectionsAgg?.days_7d),
          first_day: connectionsAgg?.first_day ? new Date(connectionsAgg.first_day).toISOString() : null,
          last_day: connectionsAgg?.last_day ? new Date(connectionsAgg.last_day).toISOString() : null,
        },
        referrals: {
          referred_by: userRow.referred_by ? String(userRow.referred_by) : null,
          referrer: referrer
            ? {
                id: String(referrer.id),
                full_name: referrer.full_name || null,
                username: referrer.username || null,
                is_partner: !!referrer.is_partner,
              }
            : null,
          referrals_count: num(userRow.referrals),
          referral_bonus_days_total: num(userRow.referral_bonus_days_total),
          custom_referral_percent: num(userRow.custom_referral_percent),
          is_partner: !!userRow.is_partner,
          partner_link_mode: userRow.partner_link_mode || null,
          downstream_count: downstreamCount,
          attribution: {
            source: attributionSource,
            own_utm: ownUtm,
            own_is_campaign: ownIsCampaign,
            chain: chainItems,
            chain_truncated: chainItems.length >= ATTRIBUTION_CHAIN_MAX_DEPTH,
            inherited_from: inheritedAncestor
              ? {
                  id: inheritedAncestor.id,
                  depth: inheritedAncestor.depth,
                  full_name: inheritedAncestor.full_name,
                  username: inheritedAncestor.username,
                  utm: inheritedAncestor.utm,
                }
              : null,
            campaign_root: campaignRoot
              ? {
                  id: campaignRoot.id,
                  depth: campaignRoot.depth,
                  full_name: campaignRoot.full_name,
                  username: campaignRoot.username,
                  utm: campaignRoot.utm,
                }
              : null,
          },
        },
        risk: {
          indicators,
          recent_audit: (recentAudit || []).map((row) => ({
            id: row.id,
            provider: row.provider || null,
            action: row.action,
            result: row.result,
            reason: row.reason || null,
            created_at: iso(row.created_at),
          })),
        },
        meta: {
          generated_at: new Date().toISOString(),
          tables_present: {
            auth_identities: hasIdentities,
            auth_password_credentials: hasPasswordCred,
            processed_payments: hasPayments,
            active_tariffs: hasActiveTariffs,
            user_devices: hasUserDevices,
            user_devices_last_online: hasUserDevicesLastOnline,
            hwid_devices_local: hasHwidLocal,
            connections: hasConnections,
            auth_audit_events: hasAuditEvents,
          },
        },
      });
    } catch (err) {
      res
        .status(500)
        .json({ error: "Failed to build user-card payload", details: String(err?.message || err) });
    }
  });

  // -------------------------------------------------------------------------
  // GET /admin-widgets/utm-stats
  //
  // Returns aggregated conversion metrics grouped by `users.utm`, so the
  // admin dashboard can see a single row per traffic source (qr_rt_launch_*,
  // partner, organic null, custom campaigns) with: total users, registered,
  // trial-used, key-activated, currently active subscriptions, paid count
  // and total revenue.
  //
  // Combined with PR feat/acquisition-source-attribution (which propagates
  // the campaign tag down referral chains), this query reflects the full
  // funnel — direct visitors AND every downstream invitee — for each
  // campaign.
  //
  // Query params (all optional):
  //   - since        ISO date; only include users with created_at >= since
  //   - utm_prefix   e.g. "qr_" to scope to a campaign family
  //   - limit        max number of rows (default 200, capped at 1000)
  // -------------------------------------------------------------------------
  router.get("/utm-stats", async (req, res) => {
    try {
      const since = toUtcDate(req.query.since);
      const until = toUtcDate(req.query.until);
      const utmPrefix = String(req.query.utm_prefix ?? "").trim();
      const limit = toInt(req.query.limit, 200, 1, 1000);

      const hasUsers = await hasTable("users");
      if (!hasUsers) {
        return res.json({
          sources: [],
          totals: { users_total: 0, users_with_utm: 0, users_no_utm: 0 },
          filters_applied: { since: since ? since.toISOString() : null, utm_prefix: utmPrefix || null, limit },
          generated_at: new Date().toISOString(),
        });
      }

      const hasPayments = await hasTable("processed_payments");
      const hasProcessedAt = hasPayments && (await hasColumn("processed_payments", "processed_at"));
      const hasPaymentStatus = hasPayments && (await hasColumn("processed_payments", "status"));

      // Build the aggregate query in plain SQL: COUNT FILTER lets us compute
      // multiple cohorts in a single pass.
      const filters = [];
      const bindings = [];
      if (since) {
        filters.push("u.created_at >= ?");
        bindings.push(since.toISOString());
      }
      if (until) {
        filters.push("u.created_at < ?");
        bindings.push(until.toISOString());
      }
      if (utmPrefix) {
        // prefix match; allow exact match too via the LIKE wildcard
        filters.push("u.utm LIKE ?");
        bindings.push(`${utmPrefix}%`);
      }
      const whereSql = filters.length ? `WHERE ${filters.join(" AND ")}` : "";

      // Payments aggregate per utm (only when the table exists).
      //
      // The `direct`/`indirect` split mirrors the attribution semantics from
      // `bloobcat/funcs/referral_attribution.py::pick_attribution_utm`:
      //   - INDIRECT = user inherited the tag from their referrer
      //     (`referred_by IS NOT NULL` AND `referrer.utm = user.utm`).
      //   - DIRECT   = anything else — either no referrer at all, or the
      //     referrer has a different/empty utm so this user is the first
      //     node in the chain to carry the tag.
      //
      // The earlier "referred_by IS NULL = direct" heuristic was wrong for
      // partner-attributed campaigns: every user arriving via a partner QR
      // already gets `referred_by = partner.id` even when their UTM came
      // straight from the campaign link. SELF JOIN with the referrer row
      // is the correct way to detect inheritance.
      let paymentsCte = "";
      let paymentsJoin = "";
      let paymentsSelect =
        "0::bigint AS users_paid, 0::bigint AS users_paid_direct, 0::bigint AS users_paid_indirect, " +
        "0::numeric AS revenue_rub, 0::numeric AS revenue_rub_direct, 0::numeric AS revenue_rub_indirect";
      if (hasPayments) {
        const statusFilter = hasPaymentStatus ? "AND p.status = 'succeeded'" : "";
        // PostgreSQL FILTER expressions reused for direct/indirect split.
        const indirectExpr = "(u2.referred_by IS NOT NULL AND r2.utm IS NOT NULL AND r2.utm = u2.utm)";
        const directExpr = "NOT " + indirectExpr;
        paymentsCte = `,
        payments_per_utm AS (
          SELECT
            u2.utm AS utm,
            COUNT(DISTINCT p.user_id) AS users_paid,
            COUNT(DISTINCT p.user_id) FILTER (WHERE ${directExpr}) AS users_paid_direct,
            COUNT(DISTINCT p.user_id) FILTER (WHERE ${indirectExpr}) AS users_paid_indirect,
            COALESCE(SUM(p.amount), 0) AS revenue_rub,
            COALESCE(SUM(p.amount) FILTER (WHERE ${directExpr}), 0) AS revenue_rub_direct,
            COALESCE(SUM(p.amount) FILTER (WHERE ${indirectExpr}), 0) AS revenue_rub_indirect
          FROM processed_payments p
          INNER JOIN users u2 ON u2.id = p.user_id
          LEFT JOIN users r2 ON r2.id = u2.referred_by
          WHERE 1=1
            ${statusFilter}
          GROUP BY u2.utm
        )`;
        paymentsJoin = "LEFT JOIN payments_per_utm ppu ON ppu.utm IS NOT DISTINCT FROM g.utm";
        paymentsSelect =
          "COALESCE(ppu.users_paid, 0) AS users_paid, " +
          "COALESCE(ppu.users_paid_direct, 0) AS users_paid_direct, " +
          "COALESCE(ppu.users_paid_indirect, 0) AS users_paid_indirect, " +
          "COALESCE(ppu.revenue_rub, 0) AS revenue_rub, " +
          "COALESCE(ppu.revenue_rub_direct, 0) AS revenue_rub_direct, " +
          "COALESCE(ppu.revenue_rub_indirect, 0) AS revenue_rub_indirect";
      }

      // Same direct/indirect logic for the user-side grouped CTE.
      const userIndirectExpr = "(u.referred_by IS NOT NULL AND r.utm IS NOT NULL AND r.utm = u.utm)";
      const userDirectExpr = "NOT " + userIndirectExpr;
      const sql = `
        WITH grouped AS (
          SELECT
            u.utm AS utm,
            COUNT(*) AS users_total,
            COUNT(*) FILTER (WHERE ${userDirectExpr}) AS users_direct,
            COUNT(*) FILTER (WHERE ${userIndirectExpr}) AS users_indirect,
            COUNT(*) FILTER (WHERE u.is_registered = true) AS users_registered,
            COUNT(*) FILTER (WHERE u.used_trial = true) AS users_used_trial,
            COUNT(*) FILTER (WHERE u.key_activated = true) AS users_key_activated,
            COUNT(*) FILTER (WHERE u.expired_at IS NOT NULL AND u.expired_at > CURRENT_DATE) AS users_active_subscription,
            COUNT(*) FILTER (WHERE u.expired_at IS NOT NULL AND u.expired_at > CURRENT_DATE AND ${userDirectExpr}) AS users_active_subscription_direct,
            COUNT(*) FILTER (WHERE u.expired_at IS NOT NULL AND u.expired_at > CURRENT_DATE AND ${userIndirectExpr}) AS users_active_subscription_indirect,
            MIN(u.created_at) AS first_seen,
            MAX(u.created_at) AS last_seen
          FROM users u
          LEFT JOIN users r ON r.id = u.referred_by
          ${whereSql}
          GROUP BY u.utm
        )${paymentsCte}
        SELECT
          g.utm,
          g.users_total,
          g.users_direct,
          g.users_indirect,
          g.users_registered,
          g.users_used_trial,
          g.users_key_activated,
          g.users_active_subscription,
          g.users_active_subscription_direct,
          g.users_active_subscription_indirect,
          g.first_seen,
          g.last_seen,
          ${paymentsSelect}
        FROM grouped g
        ${paymentsJoin}
        ORDER BY g.users_total DESC
        LIMIT ${limit};
      `;

      const raw = await database.raw(sql, bindings);
      const rows = raw?.rows ?? raw ?? [];

      const sources = rows.map((row) => {
        const utm = row.utm == null || row.utm === "" ? null : String(row.utm);
        return {
          utm,
          users_total: Number(row.users_total ?? 0),
          users_direct: Number(row.users_direct ?? 0),
          users_indirect: Number(row.users_indirect ?? 0),
          users_registered: Number(row.users_registered ?? 0),
          users_used_trial: Number(row.users_used_trial ?? 0),
          users_key_activated: Number(row.users_key_activated ?? 0),
          users_active_subscription: Number(row.users_active_subscription ?? 0),
          users_active_subscription_direct: Number(row.users_active_subscription_direct ?? 0),
          users_active_subscription_indirect: Number(row.users_active_subscription_indirect ?? 0),
          users_paid: Number(row.users_paid ?? 0),
          users_paid_direct: Number(row.users_paid_direct ?? 0),
          users_paid_indirect: Number(row.users_paid_indirect ?? 0),
          revenue_rub: Number(row.revenue_rub ?? 0),
          revenue_rub_direct: Number(row.revenue_rub_direct ?? 0),
          revenue_rub_indirect: Number(row.revenue_rub_indirect ?? 0),
          first_seen: row.first_seen ? new Date(row.first_seen).toISOString() : null,
          last_seen: row.last_seen ? new Date(row.last_seen).toISOString() : null,
        };
      });

      const totalsRowFilters = [...filters];
      const totalsBindings = [...bindings];
      const totalsWhere = totalsRowFilters.length ? `WHERE ${totalsRowFilters.join(" AND ")}` : "";
      const totalsRaw = await database.raw(
        `
        SELECT
          COUNT(*) AS users_total,
          COUNT(*) FILTER (WHERE u.utm IS NOT NULL AND u.utm <> '') AS users_with_utm,
          COUNT(*) FILTER (WHERE u.utm IS NULL OR u.utm = '') AS users_no_utm
        FROM users u
        ${totalsWhere}
        `,
        totalsBindings
      );
      const totalsRow = totalsRaw?.rows?.[0] ?? totalsRaw?.[0] ?? {};

      res.json({
        sources,
        totals: {
          users_total: Number(totalsRow.users_total ?? 0),
          users_with_utm: Number(totalsRow.users_with_utm ?? 0),
          users_no_utm: Number(totalsRow.users_no_utm ?? 0),
        },
        filters_applied: {
          since: since ? since.toISOString() : null,
          until: until ? until.toISOString() : null,
          utm_prefix: utmPrefix || null,
          limit,
        },
        generated_at: new Date().toISOString(),
      });
    } catch (err) {
      res
        .status(500)
        .json({ error: "Failed to build utm-stats payload", details: String(err?.message || err) });
    }
  });

  // -------------------------------------------------------------------------
  // GET /admin-widgets/utm-stats/timeseries
  //
  // Daily/weekly time-series for a single exact UTM tag. Returns buckets with
  // registration count, paid-user count and revenue. Drives the per-campaign
  // mini-chart in the expanded row of the UTM Stats dashboard.
  //
  // Query params:
  //   - utm        REQUIRED — exact tag (or "__no_utm__" for the null bucket)
  //   - since      ISO date (default = utm first_seen)
  //   - until      ISO date (default = now)
  //   - bucket     "day" | "week" (default "day")
  // -------------------------------------------------------------------------
  router.get("/utm-stats/timeseries", async (req, res) => {
    try {
      const utm = String(req.query.utm ?? "").trim();
      if (!utm) {
        return res.status(400).json({ error: "utm is required" });
      }
      const isNullBucket = utm === "__no_utm__";
      const since = toUtcDate(req.query.since);
      const until = toUtcDate(req.query.until);
      const bucketRaw = String(req.query.bucket ?? "day").toLowerCase();
      const bucket = bucketRaw === "week" ? "week" : "day";

      const hasUsers = await hasTable("users");
      if (!hasUsers) {
        return res.json({
          utm,
          bucket,
          buckets: [],
          filters_applied: {
            since: since ? since.toISOString() : null,
            until: until ? until.toISOString() : null,
          },
          generated_at: new Date().toISOString(),
        });
      }
      const hasPayments = await hasTable("processed_payments");
      const hasPaymentStatus = hasPayments && (await hasColumn("processed_payments", "status"));

      // Truncation expression depends on bucket. Postgres `date_trunc` rounds
      // down to the day or ISO-week boundary; we cast back to date for clean
      // bucket keys in the response.
      const truncExpr = `date_trunc('${bucket}', u.created_at)`;
      const filters = isNullBucket
        ? ["(u.utm IS NULL OR u.utm = '')"]
        : ["u.utm = ?"];
      const bindings = isNullBucket ? [] : [utm];
      if (since) {
        filters.push("u.created_at >= ?");
        bindings.push(since.toISOString());
      }
      if (until) {
        filters.push("u.created_at < ?");
        bindings.push(until.toISOString());
      }
      const whereSql = `WHERE ${filters.join(" AND ")}`;

      // Two CTEs: registrations (per-bucket COUNT) and payments (per-bucket
      // SUM/COUNT). LEFT JOIN so an empty payment bucket still renders.
      let paymentsCte = "";
      let paymentsJoin = "";
      let paymentsSelect = "0::bigint AS paid_count, 0::numeric AS revenue_rub";
      if (hasPayments) {
        const statusFilter = hasPaymentStatus ? "AND p.status = 'succeeded'" : "";
        const payFilters = isNullBucket
          ? ["(u2.utm IS NULL OR u2.utm = '')"]
          : ["u2.utm = ?"];
        const payBindings = isNullBucket ? [] : [utm];
        if (since) { payFilters.push("u2.created_at >= ?"); payBindings.push(since.toISOString()); }
        if (until) { payFilters.push("u2.created_at < ?"); payBindings.push(until.toISOString()); }
        // Payments are bucketed by the PAYMENT date, not user creation, so we
        // truncate on `p.created_at` (or `processed_at` if present). Falls
        // back to created_at on the payment.
        const hasProcessedAt = hasPayments && (await hasColumn("processed_payments", "processed_at"));
        const payDateCol = hasProcessedAt ? "p.processed_at" : "p.created_at";
        paymentsCte = `,
        payments_per_bucket AS (
          SELECT
            date_trunc('${bucket}', ${payDateCol}) AS bucket_ts,
            COUNT(DISTINCT p.user_id) AS paid_count,
            COALESCE(SUM(p.amount), 0) AS revenue_rub
          FROM processed_payments p
          INNER JOIN users u2 ON u2.id = p.user_id
          WHERE ${payFilters.join(" AND ")}
            ${statusFilter}
          GROUP BY date_trunc('${bucket}', ${payDateCol})
        )`;
        bindings.push(...payBindings);
        paymentsJoin = "FULL OUTER JOIN payments_per_bucket pb ON pb.bucket_ts = r.bucket_ts";
        paymentsSelect = "COALESCE(pb.paid_count, 0) AS paid_count, COALESCE(pb.revenue_rub, 0) AS revenue_rub";
      }

      const sql = `
        WITH regs AS (
          SELECT
            ${truncExpr} AS bucket_ts,
            COUNT(*) AS registrations
          FROM users u
          ${whereSql}
          GROUP BY ${truncExpr}
        )${paymentsCte}
        SELECT
          COALESCE(r.bucket_ts, pb.bucket_ts) AS bucket_ts,
          COALESCE(r.registrations, 0) AS registrations,
          ${paymentsSelect}
        FROM regs r
        ${paymentsJoin}
        ORDER BY bucket_ts ASC
      `;
      const raw = await database.raw(sql, bindings);
      const rows = raw?.rows ?? raw ?? [];

      const buckets = rows.map((row) => ({
        bucket_ts: row.bucket_ts ? new Date(row.bucket_ts).toISOString() : null,
        registrations: Number(row.registrations ?? 0),
        paid_count: Number(row.paid_count ?? 0),
        revenue_rub: Number(row.revenue_rub ?? 0),
      }));

      res.json({
        utm,
        bucket,
        buckets,
        filters_applied: {
          since: since ? since.toISOString() : null,
          until: until ? until.toISOString() : null,
        },
        generated_at: new Date().toISOString(),
      });
    } catch (err) {
      res
        .status(500)
        .json({ error: "Failed to build utm-stats timeseries payload", details: String(err?.message || err) });
    }
  });

  // -------------------------------------------------------------------------
  // GET /admin-widgets/utm-stats/funnel
  //
  // Returns the conversion funnel for a single exact UTM tag — five ordered
  // steps with absolute counts plus the step-to-step conversion ratio. Used
  // by the expanded-row funnel visualization.
  //
  // Steps: total → registered → used_trial → key_activated → active_subscription → paid
  //
  // `ratio_total` = step / total
  // `ratio_prev`  = step / previous_step (drops the funnel level by level)
  // -------------------------------------------------------------------------
  router.get("/utm-stats/funnel", async (req, res) => {
    try {
      const utm = String(req.query.utm ?? "").trim();
      if (!utm) {
        return res.status(400).json({ error: "utm is required" });
      }
      const isNullBucket = utm === "__no_utm__";
      const since = toUtcDate(req.query.since);
      const until = toUtcDate(req.query.until);

      const hasUsers = await hasTable("users");
      if (!hasUsers) {
        return res.json({
          utm,
          steps: [],
          generated_at: new Date().toISOString(),
        });
      }
      const hasPayments = await hasTable("processed_payments");
      const hasPaymentStatus = hasPayments && (await hasColumn("processed_payments", "status"));

      const filters = isNullBucket
        ? ["(u.utm IS NULL OR u.utm = '')"]
        : ["u.utm = ?"];
      const bindings = isNullBucket ? [] : [utm];
      if (since) { filters.push("u.created_at >= ?"); bindings.push(since.toISOString()); }
      if (until) { filters.push("u.created_at < ?"); bindings.push(until.toISOString()); }
      const whereSql = `WHERE ${filters.join(" AND ")}`;

      let paidSelect = "0::bigint AS paid";
      let paidJoin = "";
      if (hasPayments) {
        const statusFilter = hasPaymentStatus ? "AND p.status = 'succeeded'" : "";
        paidJoin = `LEFT JOIN LATERAL (
          SELECT COUNT(DISTINCT p.user_id) AS paid_inner
          FROM processed_payments p
          WHERE p.user_id IN (SELECT id FROM users u ${whereSql})
            ${statusFilter}
        ) paid_data ON true`;
        paidSelect = "COALESCE(paid_data.paid_inner, 0) AS paid";
      }

      const sql = `
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE u.is_registered = true) AS registered,
          COUNT(*) FILTER (WHERE u.used_trial = true) AS used_trial,
          COUNT(*) FILTER (WHERE u.key_activated = true) AS key_activated,
          COUNT(*) FILTER (WHERE u.expired_at IS NOT NULL AND u.expired_at > CURRENT_DATE) AS active_subscription,
          ${paidSelect}
        FROM users u
        ${paidJoin}
        ${whereSql}
      `;
      // hasPayments path uses bindings twice (subquery + outer where).
      const finalBindings = hasPayments ? [...bindings, ...bindings] : bindings;
      const raw = await database.raw(sql, finalBindings);
      const row = raw?.rows?.[0] ?? raw?.[0] ?? {};

      const stepDefs = [
        { key: "total", label: "Всего", count: Number(row.total ?? 0) },
        { key: "registered", label: "Зарегистрировались", count: Number(row.registered ?? 0) },
        { key: "used_trial", label: "Активировали триал", count: Number(row.used_trial ?? 0) },
        { key: "key_activated", label: "Подключили Happ", count: Number(row.key_activated ?? 0) },
        { key: "active_subscription", label: "Активная подписка", count: Number(row.active_subscription ?? 0) },
        { key: "paid", label: "Заплатили", count: Number(row.paid ?? 0) },
      ];
      const total = stepDefs[0].count || 0;
      let prev = total;
      const steps = stepDefs.map((step, i) => {
        const ratioTotal = total > 0 ? step.count / total : 0;
        const ratioPrev = i === 0 ? 1 : prev > 0 ? step.count / prev : 0;
        prev = step.count;
        return { ...step, ratio_total: ratioTotal, ratio_prev: ratioPrev };
      });

      res.json({
        utm,
        steps,
        filters_applied: {
          since: since ? since.toISOString() : null,
          until: until ? until.toISOString() : null,
        },
        generated_at: new Date().toISOString(),
      });
    } catch (err) {
      res
        .status(500)
        .json({ error: "Failed to build utm-stats funnel payload", details: String(err?.message || err) });
    }
  });

  // -------------------------------------------------------------------------
  // GET /admin-widgets/utm-stats/cohort
  //
  // Returns weekly registration cohorts for a single UTM tag, with conversion
  // funnel metrics computed cumulatively per cohort. Cohorts are bucketed by
  // ISO week of `users.created_at`, so each row represents users who first
  // showed up that week, and the columns show how many of THOSE users went
  // on to convert.
  //
  // Query params:
  //   - utm        REQUIRED — exact tag (or "__no_utm__" for null bucket)
  //   - since      ISO date (default = 90 days ago)
  //   - until      ISO date (default = now)
  //   - weeks      Optional cap on cohorts returned (default 16)
  // -------------------------------------------------------------------------
  router.get("/utm-stats/cohort", async (req, res) => {
    try {
      const utm = String(req.query.utm ?? "").trim();
      if (!utm) return res.status(400).json({ error: "utm is required" });
      const isNullBucket = utm === "__no_utm__";
      const since = toUtcDate(req.query.since);
      const until = toUtcDate(req.query.until);
      const weeksCap = toInt(req.query.weeks, 16, 1, 52);

      const hasUsers = await hasTable("users");
      if (!hasUsers) return res.json({ utm, cohorts: [], generated_at: new Date().toISOString() });
      const hasPayments = await hasTable("processed_payments");
      const hasPaymentStatus = hasPayments && (await hasColumn("processed_payments", "status"));

      const filters = isNullBucket
        ? ["(u.utm IS NULL OR u.utm = '')"]
        : ["u.utm = ?"];
      const bindings = isNullBucket ? [] : [utm];
      if (since) { filters.push("u.created_at >= ?"); bindings.push(since.toISOString()); }
      if (until) { filters.push("u.created_at < ?"); bindings.push(until.toISOString()); }
      const whereSql = `WHERE ${filters.join(" AND ")}`;

      const paidJoin = hasPayments
        ? `LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT p.user_id) AS paid_inner
            FROM processed_payments p
            WHERE p.user_id = u.id
              ${hasPaymentStatus ? "AND p.status = 'succeeded'" : ""}
          ) paid_data ON true`
        : "";
      const paidExpr = hasPayments ? "paid_data.paid_inner" : "0";

      const sql = `
        SELECT
          date_trunc('week', u.created_at) AS cohort_week,
          COUNT(*) AS cohort_size,
          COUNT(*) FILTER (WHERE u.is_registered = true) AS registered,
          COUNT(*) FILTER (WHERE u.used_trial = true) AS trial,
          COUNT(*) FILTER (WHERE u.key_activated = true) AS activated,
          COUNT(*) FILTER (WHERE u.expired_at IS NOT NULL AND u.expired_at > CURRENT_DATE) AS active_now,
          SUM(${paidExpr}) AS paid
        FROM users u
        ${paidJoin}
        ${whereSql}
        GROUP BY date_trunc('week', u.created_at)
        ORDER BY cohort_week DESC
        LIMIT ${weeksCap}
      `;
      const raw = await database.raw(sql, bindings);
      const rows = raw?.rows ?? raw ?? [];

      const cohorts = rows.map((row) => {
        const size = Number(row.cohort_size || 0);
        const fmt = (k) => Number(row[k] || 0);
        const ratio = (k) => (size > 0 ? fmt(k) / size : 0);
        return {
          cohort_week: row.cohort_week ? new Date(row.cohort_week).toISOString() : null,
          cohort_size: size,
          registered: fmt("registered"),
          trial: fmt("trial"),
          activated: fmt("activated"),
          active_now: fmt("active_now"),
          paid: fmt("paid"),
          ratio_registered: ratio("registered"),
          ratio_trial: ratio("trial"),
          ratio_activated: ratio("activated"),
          ratio_active_now: ratio("active_now"),
          ratio_paid: ratio("paid"),
        };
      });

      res.json({
        utm,
        cohorts,
        filters_applied: {
          since: since ? since.toISOString() : null,
          until: until ? until.toISOString() : null,
          weeks: weeksCap,
        },
        generated_at: new Date().toISOString(),
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to build utm-stats cohort payload", details: String(err?.message || err) });
    }
  });

  // =========================================================================
  // utm_campaigns CRUD
  //
  // Persistence for campaign metadata, scoped to the dashboard. Backed by the
  // `utm_campaigns` table created by tvpn-utm-campaigns-bootstrap. All routes
  // gracefully no-op (returning empty/404) if the table doesn't exist yet, so
  // the frontend can render without persistence in fresh environments.
  // =========================================================================

  const CAMPAIGN_FIELDS = [
    "id","utm","label","description","status","partner_user_id",
    "promo_code","notes","tags","created_at","updated_at","created_by",
  ];
  const UTM_RE = /^[A-Za-z0-9_]+$/;

  function normalizeCampaignRow(row) {
    if (!row) return null;
    let tags = null;
    if (row.tags !== null && row.tags !== undefined) {
      if (typeof row.tags === "string") {
        try { tags = JSON.parse(row.tags); } catch { tags = null; }
      } else {
        tags = row.tags;
      }
    }
    return {
      id: row.id,
      utm: row.utm,
      label: row.label ?? null,
      description: row.description ?? null,
      status: row.status ?? "active",
      partner_user_id: row.partner_user_id ?? null,
      promo_code: row.promo_code ?? null,
      notes: row.notes ?? null,
      tags,
      created_at: row.created_at ? new Date(row.created_at).toISOString() : null,
      updated_at: row.updated_at ? new Date(row.updated_at).toISOString() : null,
      created_by: row.created_by ?? null,
    };
  }

  function validateCampaignPayload(body, { requireUtm } = {}) {
    if (!body || typeof body !== "object") return "Empty body";
    if (requireUtm) {
      const utm = String(body.utm ?? "").trim();
      if (!utm) return "utm is required";
      if (utm.length > 64) return "utm must be <= 64 chars";
      if (!UTM_RE.test(utm)) return "utm must match [A-Za-z0-9_]+";
      if (utm === "partner") return "utm cannot be the reserved literal 'partner'";
    }
    if (body.label !== undefined && body.label !== null && String(body.label).length > 200) {
      return "label must be <= 200 chars";
    }
    if (body.status !== undefined && body.status !== null && !["active","archived"].includes(String(body.status))) {
      return "status must be 'active' or 'archived'";
    }
    return null;
  }

  // GET /admin-widgets/utm-campaigns?status=active|archived&search=...
  router.get("/utm-campaigns", async (req, res) => {
    try {
      const hasCampaigns = await hasTable("utm_campaigns");
      if (!hasCampaigns) {
        return res.json({ campaigns: [], generated_at: new Date().toISOString() });
      }
      const status = String(req.query.status ?? "active").trim();
      const search = String(req.query.search ?? "").trim();
      let q = database("utm_campaigns").select(CAMPAIGN_FIELDS);
      if (status === "active" || status === "archived") {
        q = q.where("status", status);
      } else if (status === "all") {
        // no filter
      }
      if (search) {
        q = q.where(function () {
          this.where("utm", "ilike", `%${search}%`).orWhere("label", "ilike", `%${search}%`);
        });
      }
      const rows = await q.orderBy("updated_at", "desc").limit(500);
      res.json({
        campaigns: rows.map(normalizeCampaignRow),
        generated_at: new Date().toISOString(),
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to list utm-campaigns", details: String(err?.message || err) });
    }
  });

  // GET /admin-widgets/utm-campaigns/by-utm/:utm
  router.get("/utm-campaigns/by-utm/:utm", async (req, res) => {
    try {
      const hasCampaigns = await hasTable("utm_campaigns");
      if (!hasCampaigns) return res.json({ campaign: null });
      const utm = String(req.params.utm ?? "").trim();
      if (!utm) return res.status(400).json({ error: "utm is required" });
      const row = await database("utm_campaigns").where({ utm }).first();
      res.json({ campaign: normalizeCampaignRow(row) });
    } catch (err) {
      res.status(500).json({ error: "Failed to fetch utm-campaign", details: String(err?.message || err) });
    }
  });

  // POST /admin-widgets/utm-campaigns
  // Body: { utm, label?, description?, status?, partner_user_id?, promo_code?, notes?, tags? }
  // Upsert semantics on `utm` — same tag creates once, then PATCH for updates.
  router.post("/utm-campaigns", async (req, res) => {
    try {
      const hasCampaigns = await hasTable("utm_campaigns");
      if (!hasCampaigns) {
        return res.status(503).json({ error: "utm_campaigns collection not yet bootstrapped" });
      }
      const validation = validateCampaignPayload(req.body, { requireUtm: true });
      if (validation) return res.status(400).json({ error: validation });

      const utm = String(req.body.utm).trim();
      const existing = await database("utm_campaigns").where({ utm }).first();
      if (existing) {
        return res.status(409).json({ error: "Campaign already exists for this utm", campaign: normalizeCampaignRow(existing) });
      }
      const now = new Date().toISOString();
      const createdBy = req.accountability?.user || null;
      const insertRow = {
        utm,
        label: req.body.label ?? null,
        description: req.body.description ?? null,
        status: req.body.status === "archived" ? "archived" : "active",
        partner_user_id: req.body.partner_user_id ?? null,
        promo_code: req.body.promo_code ?? null,
        notes: req.body.notes ?? null,
        tags: req.body.tags ? JSON.stringify(req.body.tags) : null,
        created_at: now,
        updated_at: now,
        created_by: createdBy,
      };
      const [inserted] = await database("utm_campaigns").insert(insertRow).returning(CAMPAIGN_FIELDS);
      res.json({ campaign: normalizeCampaignRow(inserted) });
    } catch (err) {
      res.status(500).json({ error: "Failed to create utm-campaign", details: String(err?.message || err) });
    }
  });

  // PATCH /admin-widgets/utm-campaigns/:id
  // Body: any subset of label, description, status, partner_user_id, promo_code, notes, tags
  router.patch("/utm-campaigns/:id", async (req, res) => {
    try {
      const hasCampaigns = await hasTable("utm_campaigns");
      if (!hasCampaigns) return res.status(503).json({ error: "utm_campaigns collection not yet bootstrapped" });
      const id = toInt(req.params.id, 0, 1, 1_000_000_000);
      if (!id) return res.status(400).json({ error: "id is required" });
      const validation = validateCampaignPayload(req.body, { requireUtm: false });
      if (validation) return res.status(400).json({ error: validation });

      const updateRow = { updated_at: new Date().toISOString() };
      const settable = ["label", "description", "status", "partner_user_id", "promo_code", "notes"];
      for (const key of settable) {
        if (req.body[key] !== undefined) updateRow[key] = req.body[key];
      }
      if (req.body.tags !== undefined) {
        updateRow.tags = req.body.tags === null ? null : JSON.stringify(req.body.tags);
      }
      const existing = await database("utm_campaigns").where({ id }).first();
      if (!existing) return res.status(404).json({ error: "campaign not found" });
      await database("utm_campaigns").where({ id }).update(updateRow);
      const fresh = await database("utm_campaigns").where({ id }).first();
      res.json({ campaign: normalizeCampaignRow(fresh) });
    } catch (err) {
      res.status(500).json({ error: "Failed to update utm-campaign", details: String(err?.message || err) });
    }
  });

  // DELETE /admin-widgets/utm-campaigns/:id — soft archive
  router.delete("/utm-campaigns/:id", async (req, res) => {
    try {
      const hasCampaigns = await hasTable("utm_campaigns");
      if (!hasCampaigns) return res.status(503).json({ error: "utm_campaigns collection not yet bootstrapped" });
      const id = toInt(req.params.id, 0, 1, 1_000_000_000);
      if (!id) return res.status(400).json({ error: "id is required" });
      const existing = await database("utm_campaigns").where({ id }).first();
      if (!existing) return res.status(404).json({ error: "campaign not found" });
      await database("utm_campaigns").where({ id }).update({
        status: "archived",
        updated_at: new Date().toISOString(),
      });
      const fresh = await database("utm_campaigns").where({ id }).first();
      res.json({ campaign: normalizeCampaignRow(fresh) });
    } catch (err) {
      res.status(500).json({ error: "Failed to archive utm-campaign", details: String(err?.message || err) });
    }
  });
}
