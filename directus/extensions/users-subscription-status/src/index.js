/**
 * users-subscription-status
 *
 * Adds a virtual (alias) field `users.subscription_status` that exposes a
 * single, admin-friendly status label for each user — computed at read time
 * from `is_blocked`, `expired_at`, `is_trial`, `active_tariff_id` and the
 * joined `active_tariffs.is_promo_synthetic`.
 *
 * Why: Platega has no recurring billing, so `users.is_subscribed` is a
 * lagging boolean. Admin operators need a clear at-a-glance status that
 * distinguishes paid-active / paid-expired / promo (RUTRACKER & co) /
 * built-in trial / blocked, without clicking into each user.
 *
 * Returned status values:
 *   "blocked"      — `is_blocked = true` (highest priority)
 *   "paid_active"  — active tariff (not promo-synthetic) AND `expired_at >= today`
 *   "paid_expired" — has tariff (not promo-synthetic) AND `expired_at < today`
 *   "promo"        — active tariff with `is_promo_synthetic = true`
 *   "trial"        — `is_trial = true` AND no active tariff
 *   "none"         — anything else (registered but no tariff / no trial)
 *
 * The field is rendered via the standard `formatted-value` display with
 * conditional styles, so admins see colored chips («Активен» green,
 * «Истёк» red, «Промо» orange, «Триал» purple, «Заблокирован» grey).
 *
 * Field schema is bootstrapped on `app.after` (idempotent insert into
 * `directus_fields`). Per-row computation runs as an `items.read` filter
 * scoped to the `users` collection, fetches the source fields in a single
 * batched query joined to `active_tariffs`, and injects the computed
 * value into each payload row.
 */

const COLLECTION = "users";
const FIELD = "subscription_status";

const STATUS = Object.freeze({
  BLOCKED: "blocked",
  PAID_ACTIVE: "paid_active",
  PAID_EXPIRED: "paid_expired",
  PROMO: "promo",
  TRIAL: "trial",
  NONE: "none",
});

const FIELD_ROW = {
  collection: COLLECTION,
  field: FIELD,
  // `special` in directus_fields is stored as a CSV string.
  special: "alias,no-data",
  interface: "select-dropdown",
  options: JSON.stringify({
    choices: [
      { value: STATUS.PAID_ACTIVE, text: "Активен (платный)", icon: "check_circle", color: "#10B981" },
      { value: STATUS.PROMO, text: "Промо (синтетический)", icon: "card_giftcard", color: "#F59E0B" },
      { value: STATUS.TRIAL, text: "Триал", icon: "schedule", color: "#A855F7" },
      { value: STATUS.PAID_EXPIRED, text: "Истёк (платный)", icon: "block", color: "#EF4444" },
      { value: STATUS.BLOCKED, text: "Заблокирован", icon: "lock", color: "#6B7280" },
      { value: STATUS.NONE, text: "—", icon: "remove", color: "#9CA3AF" },
    ],
    allowOther: false,
    allowNone: false,
  }),
  display: "labels",
  display_options: JSON.stringify({
    choices: [
      { value: STATUS.PAID_ACTIVE, text: "Активен", foreground: "#FFFFFF", background: "#10B981" },
      { value: STATUS.PROMO, text: "Промо", foreground: "#FFFFFF", background: "#F59E0B" },
      { value: STATUS.TRIAL, text: "Триал", foreground: "#FFFFFF", background: "#A855F7" },
      { value: STATUS.PAID_EXPIRED, text: "Истёк", foreground: "#FFFFFF", background: "#EF4444" },
      { value: STATUS.BLOCKED, text: "Заблокирован", foreground: "#FFFFFF", background: "#6B7280" },
      { value: STATUS.NONE, text: "—", foreground: "#374151", background: "#E5E7EB" },
    ],
    showAsDot: false,
  }),
  readonly: true,
  hidden: false,
  sort: 2,
  width: "half",
  translations: JSON.stringify([
    { language: "ru-RU", translation: "Статус подписки" },
  ]),
  note: "Вычисляется автоматически из expired_at, is_blocked, is_trial и is_promo_synthetic активного тарифа. У Платеги нет рекуррентов — это единый источник статуса.",
  conditions: null,
  required: false,
  group: null,
  validation: null,
  validation_message: null,
};

/**
 * Pure helper: compute the status label for a single user row.
 * Exposed for unit tests via the named export below.
 */
export function computeStatus(row, today = new Date()) {
  if (!row || typeof row !== "object") return STATUS.NONE;
  if (row.is_blocked === true) return STATUS.BLOCKED;

  const hasActiveTariff = row.active_tariff_id != null && row.active_tariff_id !== "";
  if (!hasActiveTariff) {
    return row.is_trial === true ? STATUS.TRIAL : STATUS.NONE;
  }

  if (row.is_promo_synthetic === true) return STATUS.PROMO;

  // Compare at calendar-day granularity to match backend `_user_subscription_lapsed_days`.
  const todayYmd = toYmdUtc(today);
  const expiredYmd = normalizeYmd(row.expired_at);
  if (expiredYmd && expiredYmd < todayYmd) return STATUS.PAID_EXPIRED;
  return STATUS.PAID_ACTIVE;
}

function toYmdUtc(d) {
  const dt = d instanceof Date ? d : new Date(d);
  if (!isFinite(dt.getTime())) return null;
  const y = dt.getUTCFullYear();
  const m = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const day = String(dt.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function normalizeYmd(value) {
  if (value == null) return null;
  if (typeof value === "string") {
    // Accept both "YYYY-MM-DD" and full ISO strings.
    const m = value.match(/^(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : null;
  }
  return toYmdUtc(value);
}

async function fetchSourceRows(database, ids) {
  if (!Array.isArray(ids) || ids.length === 0) return new Map();
  const rows = await database("users")
    .leftJoin("active_tariffs", "active_tariffs.id", "users.active_tariff_id")
    .whereIn("users.id", ids)
    .select(
      "users.id as id",
      "users.is_blocked as is_blocked",
      "users.is_trial as is_trial",
      "users.expired_at as expired_at",
      "users.active_tariff_id as active_tariff_id",
      "active_tariffs.is_promo_synthetic as is_promo_synthetic"
    );
  const byId = new Map();
  for (const r of rows) byId.set(String(r.id), r);
  return byId;
}

async function ensureFieldExists(database, logger) {
  if (!database) {
    logger?.warn?.("[users-subscription-status] no database accessor — skipping bootstrap");
    return;
  }
  let existing = null;
  try {
    existing = await database("directus_fields")
      .where({ collection: COLLECTION, field: FIELD })
      .first();
  } catch (err) {
    logger?.error?.(`[users-subscription-status] lookup failed: ${err?.message || err}`);
    return;
  }
  if (existing) {
    logger?.info?.(`[users-subscription-status] field ${COLLECTION}.${FIELD} already present — leaving as-is`);
    return;
  }
  try {
    await database("directus_fields").insert(FIELD_ROW);
    logger?.info?.(`[users-subscription-status] created field ${COLLECTION}.${FIELD}`);
  } catch (err) {
    logger?.error?.(`[users-subscription-status] insert failed: ${err?.message || err}`);
  }
}

export default function registerHook({ init, filter }, { database, logger }) {
  init("app.after", async () => {
    await ensureFieldExists(database, logger);
  });

  filter("items.read", async (payload, meta, context) => {
    if (meta?.collection !== COLLECTION) return payload;
    if (!Array.isArray(payload) || payload.length === 0) return payload;

    const db = context?.database;
    if (typeof db !== "function") return payload;

    const ids = payload
      .map((item) => item && item.id != null ? String(item.id) : null)
      .filter(Boolean);
    if (ids.length === 0) return payload;

    let sourceById;
    try {
      sourceById = await fetchSourceRows(db, ids);
    } catch (err) {
      logger?.warn?.(`[users-subscription-status] read enrichment failed: ${err?.message || err}`);
      return payload;
    }

    const now = new Date();
    for (const item of payload) {
      if (!item || item.id == null) continue;
      const src = sourceById.get(String(item.id));
      if (!src) continue;
      item[FIELD] = computeStatus(src, now);
    }
    return payload;
  });
}
