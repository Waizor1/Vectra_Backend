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
}
