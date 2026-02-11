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
}
