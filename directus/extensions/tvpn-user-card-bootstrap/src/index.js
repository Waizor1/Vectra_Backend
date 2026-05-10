// Bootstrap hook: ensure the `users` collection has a presentation field bound
// to the `tvpn-user-card` interface so the rich card renders on
// /admin/content/users/<id> without a manual Settings → Data Model step.
// Idempotent: inserts only when missing; never overwrites a manually-tuned row.

const COLLECTION = "users";
const FIELD = "tvpn_user_card_presentation";
const INTERFACE_ID = "tvpn-user-card";
const ENDPOINT = "/admin-widgets/user-card";

export default function registerHook({ init }, { database, logger }) {
  const log = (level, msg) => {
    try {
      logger?.[level]?.(`[tvpn-user-card-bootstrap] ${msg}`);
    } catch (_e) {
      // Logger optional — hook must never crash the app on a failed log.
    }
  };

  const ensureField = async () => {
    if (!database) {
      log("warn", "no database accessor — skipping bootstrap");
      return;
    }

    let existing = null;
    try {
      existing = await database("directus_fields")
        .where({ collection: COLLECTION, field: FIELD })
        .first();
    } catch (err) {
      log("error", `lookup failed: ${err?.message || err}`);
      return;
    }

    if (existing) {
      log("info", `field ${COLLECTION}.${FIELD} already exists — leaving as-is`);
      return;
    }

    const row = {
      collection: COLLECTION,
      field: FIELD,
      // `special` is stored CSV in directus_fields.
      special: "alias,no-data",
      interface: INTERFACE_ID,
      options: JSON.stringify({
        endpoint: ENDPOINT,
        showRawJson: false,
      }),
      display: null,
      readonly: true,
      hidden: false,
      sort: 1,
      width: "full",
      translations: null,
      note: "Богатая карточка пользователя — рендерится на /admin/content/users/<id>",
      conditions: null,
      required: false,
      group: null,
      validation: null,
      validation_message: null,
    };

    try {
      await database("directus_fields").insert(row);
      log("info", `created presentation field ${COLLECTION}.${FIELD} with interface=${INTERFACE_ID}`);
    } catch (err) {
      log("error", `insert failed: ${err?.message || err}`);
    }
  };

  init("app.after", async () => {
    await ensureField();
  });
}
