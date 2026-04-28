import crypto from "crypto";

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
      const count = toInt(payload.count, 1, 1, 150);
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

      await database.transaction(async (trx) => {
        for (let idx = 0; idx < count; idx += 1) {
          let inserted = null;
          for (let attempt = 0; attempt < 8; attempt += 1) {
            const plainCode = buildPromoCode(codePrefix);
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
            }
          }
          if (!inserted) {
            throw new Error("Could not generate a unique promo code after several attempts");
          }
          created.push(inserted);
        }
      });

      res.json({
        success: true,
        created_count: created.length,
        campaign: batchId
          ? {
              id: batchId,
              title: batchTitle || null,
            }
          : null,
        created,
      });
    } catch (err) {
      res.status(500).json({ error: "Failed to create promo codes", details: String(err?.message || err) });
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
}
