const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);

const SCHEMA_CAPABILITY_CACHE = {
  tables: new Map(),
  columns: new Map(),
};

const CLEANUP_SCHEMA_CHECK_FAILED_REASON = "cleanup_schema_check_failed";

const toAffectedCount = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const hasTableSafe = async (database, tableName) => {
  if (!database?.schema || !tableName) {
    return { ok: false, value: false };
  }
  if (SCHEMA_CAPABILITY_CACHE.tables.has(tableName)) {
    return { ok: true, value: SCHEMA_CAPABILITY_CACHE.tables.get(tableName) };
  }
  try {
    const value = await database.schema.hasTable(tableName);
    SCHEMA_CAPABILITY_CACHE.tables.set(tableName, value);
    return { ok: true, value };
  } catch (error) {
    console.debug("[remnawave-sync] schema capability check failed:", {
      check: "hasTable",
      table: tableName,
      message: error?.message || String(error),
    });
    return { ok: false, value: false };
  }
};

const hasColumnSafe = async (database, tableName, columnName) => {
  if (!database?.schema || !tableName || !columnName) {
    return { ok: false, value: false };
  }
  const cacheKey = `${tableName}.${columnName}`;
  if (SCHEMA_CAPABILITY_CACHE.columns.has(cacheKey)) {
    return { ok: true, value: SCHEMA_CAPABILITY_CACHE.columns.get(cacheKey) };
  }
  try {
    const value = await database.schema.hasColumn(tableName, columnName);
    SCHEMA_CAPABILITY_CACHE.columns.set(cacheKey, value);
    return { ok: true, value };
  } catch (error) {
    console.debug("[remnawave-sync] schema capability check failed:", {
      check: "hasColumn",
      table: tableName,
      column: columnName,
      message: error?.message || String(error),
    });
    return { ok: false, value: false };
  }
};

const TARIFF_PRICING_FIELDS = new Set([
  "base_price",
  "progressive_multiplier",
  "devices_limit_default",
  "devices_limit_family",
  "devices_max",
  "family_plan_enabled",
  "final_price_default",
  "final_price_family",
  "price_per_device",
  "price_for_one_device",
  "price_1_device",
  "one_device_price",
  "anchor_device_count",
  "anchor_total_price",
  "target_final_price",
  "lte_enabled",
  "lte_price_per_gb",
  "lte_min_gb",
  "lte_max_gb",
  "lte_step_gb",
]);

const hasTariffPricingInput = (payload) => {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  return Object.keys(payload).some((key) => TARIFF_PRICING_FIELDS.has(key));
};

const normalizeUserIds = (payload, meta) => {
  const ids = Array.isArray(payload)
    ? payload
    : Array.isArray(meta?.keys)
      ? meta.keys
      : payload != null
        ? [payload]
        : [];

  const normalized = [];
  for (const rawId of ids) {
    if (typeof rawId === "number") {
      if (Number.isSafeInteger(rawId) && rawId > 0) {
        normalized.push(rawId);
      }
      continue;
    }
    if (typeof rawId === "string" && /^[1-9]\d*$/.test(rawId)) {
      const parsed = Number(rawId);
      if (Number.isSafeInteger(parsed) && parsed > 0) {
        normalized.push(parsed);
      }
    }
  }
  return [...new Set(normalized)];
};

const applyDeleteSafetyCleanup = async (database, userIds) => {
  if (!Array.isArray(userIds) || userIds.length === 0) {
    return {
      activeTariffsDeleted: 0,
      familyInvitesDeleted: 0,
      notificationMarksDeleted: 0,
      promoUsagesDeleted: 0,
      subscriptionFreezesDeleted: 0,
      usersReferredByNullified: 0,
      schemaCheckFailed: false,
    };
  }

  if (
    !database?.schema ||
    typeof database.schema.hasTable !== "function" ||
    typeof database.schema.hasColumn !== "function" ||
    typeof database.transaction !== "function"
  ) {
    return {
      activeTariffsDeleted: 0,
      familyInvitesDeleted: 0,
      notificationMarksDeleted: 0,
      promoUsagesDeleted: 0,
      subscriptionFreezesDeleted: 0,
      usersReferredByNullified: 0,
      schemaCheckFailed: true,
    };
  }

  const report = {
    activeTariffsDeleted: 0,
    familyInvitesDeleted: 0,
    notificationMarksDeleted: 0,
    promoUsagesDeleted: 0,
    subscriptionFreezesDeleted: 0,
    usersReferredByNullified: 0,
    schemaCheckFailed: false,
  };

  const usersTable = await hasTableSafe(database, "users");
  if (!usersTable.ok) {
    report.schemaCheckFailed = true;
    return report;
  }
  if (!usersTable.value) {
    return report;
  }

  const activeTariffsTable = await hasTableSafe(database, "active_tariffs");
  if (!activeTariffsTable.ok) {
    report.schemaCheckFailed = true;
  }
  const hasActiveTariffsUserId = activeTariffsTable.value
    ? await hasColumnSafe(database, "active_tariffs", "user_id")
    : { ok: true, value: false };
  if (activeTariffsTable.value && !hasActiveTariffsUserId.ok) {
    report.schemaCheckFailed = true;
  }

  const notificationMarksTable = await hasTableSafe(database, "notification_marks");
  if (!notificationMarksTable.ok) {
    report.schemaCheckFailed = true;
  }
  const hasNotificationMarksUserId = notificationMarksTable.value
    ? await hasColumnSafe(database, "notification_marks", "user_id")
    : { ok: true, value: false };
  if (notificationMarksTable.value && !hasNotificationMarksUserId.ok) {
    report.schemaCheckFailed = true;
  }

  const promoUsagesTable = await hasTableSafe(database, "promo_usages");
  if (!promoUsagesTable.ok) {
    report.schemaCheckFailed = true;
  }
  const hasPromoUsagesUserId = promoUsagesTable.value
    ? await hasColumnSafe(database, "promo_usages", "user_id")
    : { ok: true, value: false };
  if (promoUsagesTable.value && !hasPromoUsagesUserId.ok) {
    report.schemaCheckFailed = true;
  }

  const familyInvitesTable = await hasTableSafe(database, "family_invites");
  if (!familyInvitesTable.ok) {
    report.schemaCheckFailed = true;
  }
  const hasFamilyInvitesOwnerId = familyInvitesTable.value
    ? await hasColumnSafe(database, "family_invites", "owner_id")
    : { ok: true, value: false };
  if (familyInvitesTable.value && !hasFamilyInvitesOwnerId.ok) {
    report.schemaCheckFailed = true;
  }

  const subscriptionFreezesTable = await hasTableSafe(database, "subscription_freezes");
  if (!subscriptionFreezesTable.ok) {
    report.schemaCheckFailed = true;
  }
  const hasSubscriptionFreezesUserId = subscriptionFreezesTable.value
    ? await hasColumnSafe(database, "subscription_freezes", "user_id")
    : { ok: true, value: false };
  if (subscriptionFreezesTable.value && !hasSubscriptionFreezesUserId.ok) {
    report.schemaCheckFailed = true;
  }

  const hasUsersReferredBy = await hasColumnSafe(database, "users", "referred_by");
  if (!hasUsersReferredBy.ok) {
    report.schemaCheckFailed = true;
  }

  if (report.schemaCheckFailed) {
    return report;
  }

  await database.transaction(async (trx) => {
    if (typeof trx !== "function") {
      throw new Error("cleanup_transaction_query_builder_unavailable");
    }

    if (activeTariffsTable.value && hasActiveTariffsUserId.value) {
      report.activeTariffsDeleted = toAffectedCount(
        await trx("active_tariffs").whereIn("user_id", userIds).delete()
      );
    }

    if (notificationMarksTable.value && hasNotificationMarksUserId.value) {
      report.notificationMarksDeleted = toAffectedCount(
        await trx("notification_marks").whereIn("user_id", userIds).delete()
      );
    }

    if (promoUsagesTable.value && hasPromoUsagesUserId.value) {
      report.promoUsagesDeleted = toAffectedCount(
        await trx("promo_usages").whereIn("user_id", userIds).delete()
      );
    }

    if (familyInvitesTable.value && hasFamilyInvitesOwnerId.value) {
      report.familyInvitesDeleted = toAffectedCount(
        await trx("family_invites").whereIn("owner_id", userIds).delete()
      );
    }

    if (subscriptionFreezesTable.value && hasSubscriptionFreezesUserId.value) {
      report.subscriptionFreezesDeleted = toAffectedCount(
        await trx("subscription_freezes").whereIn("user_id", userIds).delete()
      );
    }

    if (hasUsersReferredBy.value) {
      report.usersReferredByNullified = toAffectedCount(
        await trx("users").whereIn("referred_by", userIds).update({ referred_by: null })
      );
    }
  });

  return report;
};

export default function registerHook({ action, filter }, { database }) {
  const baseUrl = process.env.ADMIN_INTEGRATION_URL;
  const token = process.env.ADMIN_INTEGRATION_TOKEN;

  const callBackend = async (method, path, body) => {
    if (!baseUrl || !token) {
      return;
    }
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Integration-Token": token,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Admin integration failed: ${res.status} ${text}`);
    }
    const contentType = String(res.headers.get("content-type") || "").toLowerCase();
    if (contentType.includes("application/json")) {
      return await res.json();
    }
    return null;
  };

  const enrichTariffPayload = async (payload, tariffId) => {
    if (!baseUrl || !token) {
      return payload;
    }
    if (!hasTariffPricingInput(payload)) {
      return payload;
    }
    try {
      const response = await callBackend(
        "POST",
        "/admin/integration/tariffs/compute-pricing",
        {
          tariff_id: tariffId != null ? Number(tariffId) : null,
          patch: payload,
        }
      );
      const blockingErrors = response?.blockingErrors || response?.blocking_errors || [];
      if (Array.isArray(blockingErrors) && blockingErrors.length > 0) {
        const message = blockingErrors
          .map((item) => item?.message || String(item))
          .filter(Boolean)
          .join("; ");
        throw new Error(`Tariff validation failed: ${message || "blocking errors"}`);
      }
      const computed = response?.computed;
      if (!computed || typeof computed !== "object") {
        return payload;
      }
      return {
        ...payload,
        base_price: computed.base_price,
        progressive_multiplier: computed.progressive_multiplier,
        devices_limit_default: computed.devices_limit_default ?? payload.devices_limit_default,
        devices_limit_family: computed.devices_limit_family ?? payload.devices_limit_family,
        family_plan_enabled: computed.family_plan_enabled ?? payload.family_plan_enabled,
        final_price_default: computed.final_price_default ?? payload.final_price_default,
        final_price_family: computed.final_price_family ?? payload.final_price_family,
        lte_enabled: computed.lte_enabled ?? payload.lte_enabled,
        lte_price_per_gb: computed.lte_price_per_gb ?? payload.lte_price_per_gb,
        lte_min_gb: computed.lte_min_gb ?? payload.lte_min_gb,
        lte_max_gb: computed.lte_max_gb ?? payload.lte_max_gb,
        lte_step_gb: computed.lte_step_gb ?? payload.lte_step_gb,
      };
    } catch (error) {
      console.error("[remnawave-sync] tariff pricing compute failed:", error?.message || error);
      throw error;
    }
  };

  filter("items.create", async (payload, meta) => {
    if (meta?.collection !== "tariffs") {
      return payload;
    }
    return await enrichTariffPayload(payload || {}, null);
  });

  filter("items.update", async (payload, meta) => {
    if (meta?.collection !== "tariffs") {
      return payload;
    }
    const keys = meta?.keys;
    const tariffId = Array.isArray(keys) ? keys[0] : keys;
    return await enrichTariffPayload(payload || {}, tariffId);
  });

  action("items.update", async (event) => {
    const collection = event?.collection;
    const keys = event?.keys || [];
    const payload = event?.payload || {};

    if (!collection || !keys || keys.length === 0) {
      return;
    }
    if (!baseUrl || !token) {
      return;
    }

    if (collection === "users") {
      const syncPayload = {};
      if (hasOwn(payload, "lte_gb_total")) {
        syncPayload.lte_gb_total = payload.lte_gb_total;
      }
      if (hasOwn(payload, "expired_at")) {
        syncPayload.expired_at = payload.expired_at;
      }
      if (hasOwn(payload, "hwid_limit")) {
        syncPayload.hwid_limit = payload.hwid_limit;
      }
      if (Object.keys(syncPayload).length === 0) {
        return;
      }
      const userId = Array.isArray(keys) ? keys[0] : keys;
      await callBackend("POST", `/admin/integration/users/${userId}/sync`, syncPayload);
      return;
    }

    if (collection === "active_tariffs") {
      const syncPayload = {};
      if (hasOwn(payload, "lte_gb_total")) {
        syncPayload.lte_gb_total = payload.lte_gb_total;
      }
      if (hasOwn(payload, "lte_gb_used")) {
        syncPayload.lte_gb_used = payload.lte_gb_used;
      }
      if (Object.keys(syncPayload).length === 0) {
        return;
      }
      const activeTariffId = Array.isArray(keys) ? keys[0] : keys;
      await callBackend(
        "POST",
        `/admin/integration/active-tariffs/${activeTariffId}/sync`,
        syncPayload
      );
      return;
    }
  });

  filter("items.delete", async (payload, meta) => {
    const collection = meta?.collection;
    if (collection !== "users") {
      return payload;
    }
    const ids = normalizeUserIds(payload, meta);
    const cleanupReport = await applyDeleteSafetyCleanup(database, ids);
    console.info("[remnawave-sync] users pre-delete cleanup", {
      usersRequested: ids.length,
      ...cleanupReport,
    });

    if (cleanupReport.schemaCheckFailed) {
      console.warn("[remnawave-sync] users pre-delete skipped", {
        reason: CLEANUP_SCHEMA_CHECK_FAILED_REASON,
        userIds: ids,
      });
      const error = new Error(CLEANUP_SCHEMA_CHECK_FAILED_REASON);
      error.reason = CLEANUP_SCHEMA_CHECK_FAILED_REASON;
      throw error;
    }

    for (const userId of ids) {
      if (!baseUrl || !token) {
        continue;
      }
      try {
        await callBackend("POST", `/admin/integration/users/${userId}/pre-delete`);
      } catch (error) {
        console.error("[remnawave-sync] pre-delete failed:", userId, error?.message || error);
      }
    }
    return payload;
  });
}
