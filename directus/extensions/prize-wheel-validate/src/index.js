const toBool = (value) => {
  if (typeof value === "boolean") return value;
  if (value === null || value === undefined) return false;
  const str = String(value).trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(str);
};

export default function registerHook({ filter }, { database }) {
  const raiseInvalid = (message) => {
    const err = new Error(message);
    err.name = "InvalidPayloadException";
    err.status = 400;
    err.statusCode = 400;
    err.code = "INVALID_PAYLOAD";
    err.extensions = { code: "INVALID_PAYLOAD" };
    throw err;
  };

  const validate = async (payload, keys) => {
    const incoming = payload || {};
    const id = Array.isArray(keys) ? keys[0] : keys;

    let current = null;
    if (id) {
      current = await database("prize_wheel_config").where({ id }).first();
    }

    const newProbRaw = incoming.probability ?? (current ? current.probability : 0);
    const newProb = Number(newProbRaw);
    if (Number.isNaN(newProb) || newProb < 0 || newProb > 1) {
      raiseInvalid("Вероятность приза должна быть в диапазоне от 0 до 1");
    }

    const newActive =
      incoming.is_active !== undefined
        ? toBool(incoming.is_active)
        : current
        ? toBool(current.is_active)
        : true;

    const newType = (incoming.prize_type ?? (current ? current.prize_type : "")).toString().trim();
    const newValue = (incoming.prize_value ?? (current ? current.prize_value : "")).toString().trim();
    if (newType === "subscription") {
      const days = Number.parseInt(newValue, 10);
      if (!Number.isFinite(days) || days <= 0) {
        raiseInvalid("Для типа 'subscription' поле 'prize_value' должно быть целым числом дней (> 0)");
      }
    }

    const sumQuery = database("prize_wheel_config").where({ is_active: true });
    if (id) {
      sumQuery.andWhere("id", "!=", id);
    }
    const sumRow = await sumQuery.sum({ total: "probability" }).first();
    const othersSum = Number(sumRow?.total || 0);
    const candidateTotal = othersSum + (newActive ? newProb : 0);

    if (candidateTotal > 1.0 + 1e-9) {
      raiseInvalid(`Сумма вероятностей активных призов превышает 100%: ${candidateTotal.toFixed(4)}`);
    }

    return payload;
  };

  filter("items.create", async (payload, meta) => {
    if (meta?.collection !== "prize_wheel_config") {
      return payload;
    }
    return validate(payload, null);
  });

  filter("items.update", async (payload, meta) => {
    if (meta?.collection !== "prize_wheel_config") {
      return payload;
    }
    return validate(payload, meta?.keys);
  });
}
