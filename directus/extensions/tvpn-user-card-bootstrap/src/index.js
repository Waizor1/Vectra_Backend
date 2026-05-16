// Bootstrap hook: ensure the `users` collection has a presentation field bound
// to the `tvpn-user-card` interface so the rich card renders on
// /admin/content/users/<id> without a manual Settings → Data Model step.
//
// Also seeds `directus_fields` rows for `users.email` and `auth_identities.email`
// so the columns are explicitly visible (hidden=false) in the admin UI — without
// this, Google-only registrations are easy to miss in the content search bar.
//
// Also seeds a global `directus_presets` bookmark "Заблокировавшие бота" that
// pre-filters the `users` collection by `is_blocked = true` — gives admins a
// one-click sidebar entry to triage users who blocked the Telegram bot.
//
// Idempotent: inserts only when missing; never overwrites a manually-tuned row.

const PRESENTATION_FIELD = {
  collection: "users",
  field: "tvpn_user_card_presentation",
  special: "alias,no-data",
  interface: "tvpn-user-card",
  options: JSON.stringify({
    endpoint: "/admin-widgets/user-card",
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

const EMAIL_FIELD_DEFAULTS = {
  special: null,
  interface: "input",
  options: JSON.stringify({ trim: true }),
  display: "raw",
  readonly: false,
  hidden: false,
  sort: null,
  width: "half",
  translations: null,
  conditions: null,
  required: false,
  group: null,
  validation: null,
  validation_message: null,
};

const USERS_EMAIL_FIELD = {
  ...EMAIL_FIELD_DEFAULTS,
  collection: "users",
  field: "email",
  note: "Email пользователя (Google/Apple/E-mail). Виден в списке и в content-search admin UI.",
};

const AUTH_IDENTITIES_EMAIL_FIELD = {
  ...EMAIL_FIELD_DEFAULTS,
  collection: "auth_identities",
  field: "email",
  note: "Email из OAuth-провайдера. Используется для поиска юзера по email со стороны провайдера.",
};

const FIELDS_TO_ENSURE = [
  PRESENTATION_FIELD,
  USERS_EMAIL_FIELD,
  AUTH_IDENTITIES_EMAIL_FIELD,
];

// Global bookmark on `/admin/content/users` — appears in the left sidebar
// nav under the Users collection. user=null + role=null = visible to all roles
// with read access. Filter is the Directus JSON filter format; layout_query
// drives the tabular columns + in-layout sort.
const BLOCKED_USERS_BOOKMARK = {
  bookmark: "Заблокировавшие бота",
  user: null,
  role: null,
  collection: "users",
  search: null,
  layout: "tabular",
  layout_query: JSON.stringify({
    tabular: {
      sort: ["-blocked_at"],
      fields: [
        "id",
        "username",
        "full_name",
        "email",
        "balance",
        "expired_at",
        "blocked_at",
      ],
    },
  }),
  layout_options: null,
  refresh_interval: null,
  filter: JSON.stringify({ is_blocked: { _eq: true } }),
  icon: "block",
  color: "#E35169",
};

const BOOKMARKS_TO_ENSURE = [BLOCKED_USERS_BOOKMARK];

export default function registerHook({ init }, { database, logger }) {
  const log = (level, msg) => {
    try {
      logger?.[level]?.(`[tvpn-user-card-bootstrap] ${msg}`);
    } catch (_e) {
      // Logger optional — hook must never crash the app on a failed log.
    }
  };

  const ensureField = async (row) => {
    if (!database) {
      log("warn", "no database accessor — skipping bootstrap");
      return;
    }

    let existing = null;
    try {
      existing = await database("directus_fields")
        .where({ collection: row.collection, field: row.field })
        .first();
    } catch (err) {
      log("error", `lookup ${row.collection}.${row.field} failed: ${err?.message || err}`);
      return;
    }

    if (existing) {
      log("info", `field ${row.collection}.${row.field} already exists — leaving as-is`);
      return;
    }

    try {
      await database("directus_fields").insert(row);
      log("info", `created field metadata ${row.collection}.${row.field}`);
    } catch (err) {
      log("error", `insert ${row.collection}.${row.field} failed: ${err?.message || err}`);
    }
  };

  const ensureAll = async () => {
    for (const row of FIELDS_TO_ENSURE) {
      await ensureField(row);
    }
  };

  const ensureBookmark = async (row) => {
    if (!database) {
      log("warn", "no database accessor — skipping bookmark bootstrap");
      return;
    }

    let existing = null;
    try {
      existing = await database("directus_presets")
        .where({ bookmark: row.bookmark, collection: row.collection })
        .whereNull("user")
        .whereNull("role")
        .first();
    } catch (err) {
      log(
        "error",
        `lookup bookmark "${row.bookmark}" on ${row.collection} failed: ${err?.message || err}`,
      );
      return;
    }

    if (existing) {
      log(
        "info",
        `bookmark "${row.bookmark}" on ${row.collection} already exists — leaving as-is`,
      );
      return;
    }

    try {
      await database("directus_presets").insert(row);
      log(
        "info",
        `created bookmark "${row.bookmark}" on ${row.collection}`,
      );
    } catch (err) {
      log(
        "error",
        `insert bookmark "${row.bookmark}" on ${row.collection} failed: ${err?.message || err}`,
      );
    }
  };

  const ensureAllBookmarks = async () => {
    for (const row of BOOKMARKS_TO_ENSURE) {
      await ensureBookmark(row);
    }
  };

  init("app.after", async () => {
    await ensureAll();
    await ensureAllBookmarks();
  });
}
