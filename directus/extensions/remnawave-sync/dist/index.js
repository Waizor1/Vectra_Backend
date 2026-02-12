const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);

const TARIFF_PRICING_FIELDS = new Set([
  "base_price",
  "progressive_multiplier",
  "devices_limit_default",
  "devices_limit_family",
  "family_plan_enabled",
  "final_price_default",
  "final_price_family",
]);

const hasTariffPricingInput = (payload) => {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  return Object.keys(payload).some((key) => TARIFF_PRICING_FIELDS.has(key));
};

export default function registerHook({ action, filter }) {
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
      const computed = response?.computed;
      if (!computed || typeof computed !== "object") {
        return payload;
      }
      return {
        ...payload,
        base_price: computed.base_price,
        progressive_multiplier: computed.progressive_multiplier,
      };
    } catch (error) {
      console.error("[remnawave-sync] tariff pricing compute skipped:", error?.message || error);
      return payload;
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

  action("items.delete", async (event) => {
    const collection = event?.collection;
    const keys = event?.keys || [];
    if (collection !== "users" || !keys || keys.length === 0) {
      return;
    }
    if (!baseUrl || !token) {
      return;
    }
    const ids = Array.isArray(keys) ? keys : [keys];
    for (const userId of ids) {
      await callBackend("DELETE", `/admin/integration/users/${userId}`);
    }
  });
}
