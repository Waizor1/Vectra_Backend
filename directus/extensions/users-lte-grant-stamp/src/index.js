/**
 * users-lte-grant-stamp
 *
 * Defense in depth for the LTE quota anchor introduced in BE 1.48.0.
 *
 * The backend `sync_user_lte` service stamps `users.admin_lte_granted_at`
 * on first grant, so the LTE limiter anchors the quota window to the
 * grant moment rather than the user's `created_at`. But admins who edit
 * `users.lte_gb_total` directly through the Directus UI bypass that
 * service entirely — the stamp stays NULL and the limiter falls back to
 * `created_at`, which is exactly the case 2026-05-11 review called out.
 *
 * This hook plugs that hole on the Directus side:
 *
 *   - Listens to `items.update` on the `users` collection.
 *   - When `lte_gb_total` is being set to a positive value AND every
 *     target row currently has a NULL `admin_lte_granted_at`, stamps
 *     `admin_lte_granted_at = now()` in the same write.
 *   - If the payload already includes `admin_lte_granted_at`, respects
 *     the explicit value (admin override).
 *   - If the target set is mixed (some rows already stamped), skips
 *     stamping to avoid sliding existing anchors forward — the affected
 *     NULL rows can be stamped in a follow-up edit.
 */

export default function registerHook({ filter }) {
  filter("items.update", async (payload, meta, context) => {
    if (meta?.collection !== "users") return payload;
    if (!payload || typeof payload !== "object") return payload;

    // Only act when lte_gb_total is being explicitly set in this write.
    if (
      !Object.prototype.hasOwnProperty.call(payload, "lte_gb_total") ||
      payload.lte_gb_total === null
    ) {
      return payload;
    }
    const newTotal = Number(payload.lte_gb_total);
    if (!Number.isFinite(newTotal) || newTotal <= 0) {
      return payload;
    }

    // Respect an explicit admin override of the stamp.
    if (Object.prototype.hasOwnProperty.call(payload, "admin_lte_granted_at")) {
      return payload;
    }

    const keys = Array.isArray(meta?.keys) ? meta.keys : [];
    if (keys.length === 0) return payload;

    const database = context?.database;
    if (typeof database !== "function") return payload;

    try {
      const rows = await database("users")
        .whereIn("id", keys)
        .select("id", "admin_lte_granted_at");
      if (rows.length === 0) return payload;
      const allNull = rows.every((r) => r.admin_lte_granted_at == null);
      if (!allNull) return payload;
      payload.admin_lte_granted_at = new Date().toISOString();
    } catch (_e) {
      // Stamping is best-effort; never block the admin write because of it.
      return payload;
    }

    return payload;
  });
}
