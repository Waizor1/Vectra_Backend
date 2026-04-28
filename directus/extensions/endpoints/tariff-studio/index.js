function isAuthenticatedRequest(req) {
  return Boolean(req?.accountability && (req.accountability.admin === true || req.accountability.user));
}

function normalizePlainObject(value, maxJsonLength = 12000) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  try {
    const serialized = JSON.stringify(value);
    if (!serialized || serialized.length > maxJsonLength) return {};
    return JSON.parse(serialized);
  } catch (_err) {
    return {};
  }
}

async function callBackend(path, body) {
  const baseUrl = process.env.ADMIN_INTEGRATION_URL;
  const token = process.env.ADMIN_INTEGRATION_TOKEN;
  if (!baseUrl || !token) {
    const err = new Error("Admin integration is not configured");
    err.status = 503;
    throw err;
  }
  const res = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Integration-Token": token,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_err) {
      payload = { message: text.slice(0, 500) };
    }
  }
  if (!res.ok) {
    const err = new Error(payload?.detail || payload?.message || `Backend returned ${res.status}`);
    err.status = res.status;
    err.payload = payload;
    throw err;
  }
  return payload || { ok: true };
}

export default function registerEndpoint(router) {
  router.post("/quote-preview", async (req, res) => {
    if (!isAuthenticatedRequest(req)) {
      return res.status(401).json({ ok: false, message: "Unauthorized" });
    }
    const body = normalizePlainObject(req.body);
    const tariffIdRaw = body.tariff_id ?? body.tariffId ?? null;
    const tariffId = tariffIdRaw === null || tariffIdRaw === undefined || tariffIdRaw === "" ? null : Number(tariffIdRaw);
    const patch = normalizePlainObject(body.patch);
    try {
      const result = await callBackend("/admin/integration/tariffs/quote-preview", {
        tariff_id: Number.isFinite(tariffId) ? tariffId : null,
        patch,
      });
      return res.json(result);
    } catch (error) {
      const status = Number.isInteger(error?.status) ? error.status : 500;
      return res.status(status).json({
        ok: false,
        message: error?.message || "Tariff Studio preview failed",
        backend: error?.payload || null,
      });
    }
  });
}
