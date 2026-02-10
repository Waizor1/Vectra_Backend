const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);

export default function registerHook({ action }) {
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
  };

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
