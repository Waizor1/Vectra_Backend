// Bootstrap hook: ensure the `utm_campaigns` collection exists and is
// registered with Directus on startup. Idempotent — never overwrites a
// manually-tuned row and never destroys data.
//
// Collection shape:
//   id              integer  primary key, autoincrement
//   utm             text     unique, indexed — the actual tag string
//   label           text     friendly title shown in dashboards
//   description     text     long-form what/why for the campaign
//   status          text     'active' | 'archived' — soft delete semantics
//   partner_user_id integer  optional link to users.id (no FK constraint)
//   promo_code      text     optional promo code attached to campaign
//   notes           text     internal notes
//   tags            json     freeform array of strings for categorisation
//   created_at      timestamptz default now
//   updated_at      timestamptz
//   created_by      text     Directus user uuid (so we can show owner)

const COLLECTION = "utm_campaigns";

const FIELD_DEFS = [
  { field: "id", type: "integer", interface: "input", special: null, readonly: true, hidden: true, sort: 1 },
  { field: "utm", type: "string", interface: "input", special: null, readonly: false, hidden: false, sort: 2, note: "UTM-тег (alphanumeric + underscore)" },
  { field: "label", type: "string", interface: "input", special: null, readonly: false, hidden: false, sort: 3, note: "Подпись кампании" },
  { field: "description", type: "text", interface: "input-multiline", special: null, readonly: false, hidden: false, sort: 4 },
  { field: "status", type: "string", interface: "select-dropdown", special: null, readonly: false, hidden: false, sort: 5, options: { choices: [{ text: "Active", value: "active" }, { text: "Archived", value: "archived" }] } },
  { field: "partner_user_id", type: "integer", interface: "input", special: null, readonly: false, hidden: false, sort: 6, note: "users.id владельца кампании" },
  { field: "promo_code", type: "string", interface: "input", special: null, readonly: false, hidden: false, sort: 7 },
  { field: "notes", type: "text", interface: "input-multiline", special: null, readonly: false, hidden: false, sort: 8 },
  { field: "tags", type: "json", interface: "tags", special: "cast-json", readonly: false, hidden: false, sort: 9 },
  { field: "created_at", type: "timestamp", interface: "datetime", special: "date-created", readonly: true, hidden: false, sort: 10 },
  { field: "updated_at", type: "timestamp", interface: "datetime", special: "date-updated", readonly: true, hidden: false, sort: 11 },
  { field: "created_by", type: "uuid", interface: "select-dropdown-m2o", special: "user-created", readonly: true, hidden: false, sort: 12 },
];

export default function registerHook({ init }, { database, logger }) {
  const log = (level, msg) => {
    try {
      logger?.[level]?.(`[tvpn-utm-campaigns-bootstrap] ${msg}`);
    } catch (_e) {
      // Logger optional — hook must never crash the app on a failed log.
    }
  };

  const ensureTable = async () => {
    if (!database) {
      log("warn", "no database accessor — skipping bootstrap");
      return false;
    }
    try {
      const exists = await database.schema.hasTable(COLLECTION);
      if (exists) {
        log("info", `table ${COLLECTION} already exists`);
        return true;
      }
      await database.schema.createTable(COLLECTION, (t) => {
        t.increments("id").primary();
        t.string("utm", 64).notNullable().unique();
        t.string("label", 200).nullable();
        t.text("description").nullable();
        t.string("status", 20).notNullable().defaultTo("active");
        t.integer("partner_user_id").nullable();
        t.string("promo_code", 100).nullable();
        t.text("notes").nullable();
        t.json("tags").nullable();
        t.timestamp("created_at").defaultTo(database.fn.now());
        t.timestamp("updated_at").nullable();
        t.uuid("created_by").nullable();
        t.index(["status"], "idx_utm_campaigns_status");
        t.index(["utm"], "idx_utm_campaigns_utm");
      });
      log("info", `created table ${COLLECTION}`);
      return true;
    } catch (err) {
      log("error", `ensureTable failed: ${err?.message || err}`);
      return false;
    }
  };

  const ensureCollectionRow = async () => {
    try {
      const existing = await database("directus_collections")
        .where({ collection: COLLECTION })
        .first();
      if (existing) {
        log("info", `directus_collections row for ${COLLECTION} already exists`);
        return;
      }
      await database("directus_collections").insert({
        collection: COLLECTION,
        icon: "campaign",
        note: "UTM-кампании Vectra Connect — метки, метаданные, история запусков",
        display_template: "{{utm}} — {{label}}",
        hidden: false,
        singleton: false,
        translations: null,
        archive_field: "status",
        archive_app_filter: true,
        archive_value: "archived",
        unarchive_value: "active",
        sort_field: "sort",
        accountability: "all",
        color: "#b692f6",
        item_duplication_fields: null,
        sort: null,
        group: null,
        collapse: "open",
        preview_url: null,
        versioning: false,
      });
      log("info", `inserted directus_collections row for ${COLLECTION}`);
    } catch (err) {
      log("error", `ensureCollectionRow failed: ${err?.message || err}`);
    }
  };

  const ensureFieldRow = async (def) => {
    try {
      const existing = await database("directus_fields")
        .where({ collection: COLLECTION, field: def.field })
        .first();
      if (existing) return;
      await database("directus_fields").insert({
        collection: COLLECTION,
        field: def.field,
        special: def.special ?? null,
        interface: def.interface ?? "input",
        options: def.options ? JSON.stringify(def.options) : null,
        display: null,
        readonly: !!def.readonly,
        hidden: !!def.hidden,
        sort: def.sort ?? null,
        width: "full",
        translations: null,
        note: def.note ?? null,
        conditions: null,
        required: false,
        group: null,
        validation: null,
        validation_message: null,
      });
      log("info", `inserted directus_fields row for ${COLLECTION}.${def.field}`);
    } catch (err) {
      log("error", `ensureFieldRow ${def.field} failed: ${err?.message || err}`);
    }
  };

  init("app.after", async () => {
    const tableOk = await ensureTable();
    if (!tableOk) return;
    await ensureCollectionRow();
    for (const def of FIELD_DEFS) {
      await ensureFieldRow(def);
    }
  });
}
