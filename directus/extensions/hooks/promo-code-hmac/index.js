import crypto from "crypto";

const isHex64 = (value) =>
  typeof value === "string" && /^[0-9a-f]{64}$/i.test(value.trim());

export default function registerHook({ filter }) {
  const secret = process.env.PROMO_HMAC_SECRET || "";

  const applyHmac = (payload) => {
    if (!payload || payload === null) {
      return payload;
    }
    const next = { ...payload };
    const rawCode = typeof next.raw_code === "string" ? next.raw_code.trim() : "";
    const codeHmac = typeof next.code_hmac === "string" ? next.code_hmac.trim() : "";
    const needsSecret = Boolean(rawCode || (codeHmac && !isHex64(codeHmac)));

    if (!secret) {
      if (needsSecret) {
        throw new Error("PROMO_HMAC_SECRET is not configured");
      }
      return next;
    }

    if (rawCode) {
      const hmac = crypto.createHmac("sha256", secret).update(rawCode).digest("hex");
      next.code_hmac = hmac;
      delete next.raw_code;
      return next;
    }

    if (codeHmac && !isHex64(codeHmac)) {
      const hmac = crypto.createHmac("sha256", secret).update(codeHmac).digest("hex");
      next.code_hmac = hmac;
      return next;
    }

    return next;
  };

  filter("items.create", async (payload, meta) => {
    if (meta?.collection !== "promo_codes") {
      return payload;
    }
    return applyHmac(payload);
  });

  filter("items.update", async (payload, meta) => {
    if (meta?.collection !== "promo_codes") {
      return payload;
    }
    return applyHmac(payload);
  });
};
