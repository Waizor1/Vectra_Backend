<template>
  <private-view title="UTM Stats">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">SVPN</div>
          <div>
            <div class="nav__brand-title">UTM Stats</div>
            <div class="nav__brand-subtitle">Конверсия по источникам</div>
          </div>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-utm-stats' }">
            <span class="nav__item-icon"><v-icon name="trending_up" /></span>
            <span class="nav__item-label">UTM Stats</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Promo Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-segment-studio' }">
            <span class="nav__item-icon"><v-icon name="campaign" /></span>
            <span class="nav__item-label">Segment Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-tariff-studio' }">
            <span class="nav__item-icon"><v-icon name="tune" /></span>
            <span class="nav__item-label">Tariff Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
        </div>
      </div>
    </template>

    <template #actions>
      <v-button secondary :disabled="!sources.length" @click="exportCsv">
        <v-icon name="download" left />
        Экспорт CSV
      </v-button>
      <v-button secondary :loading="loading" @click="refresh">
        <v-icon name="refresh" left />
        Обновить
      </v-button>
    </template>

    <div class="page">
      <div class="page__main">
        <section class="hero">
          <div class="hero__left">
            <div class="hero__kicker">Vectra Connect</div>
            <h1 class="hero__title">UTM Stats</h1>
            <p class="hero__subtitle">
              Полная воронка по источникам трафика: от первой регистрации до активной подписки и оплаты.
              После PR feat/acquisition-source-attribution цифры включают всю цепочку приглашений
              (друзья пришедших по UTM-ссылкам наследуют тег источника).
            </p>
          </div>

          <div class="hero__right">
            <label class="field field--compact">
              <span>Префикс UTM (сервер)</span>
              <input
                v-model="filters.utm_prefix"
                type="text"
                class="input"
                placeholder="напр. qr_rt_"
                @keyup.enter="refresh"
              />
            </label>
            <label class="field field--compact">
              <span>Поиск (локально)</span>
              <input
                v-model="localSearch"
                type="text"
                class="input"
                placeholder="фильтр по подстроке"
              />
            </label>
            <label class="field field--compact">
              <span>С даты регистрации</span>
              <input v-model="filters.since" type="date" class="input" @change="refresh" />
            </label>
            <label class="field field--compact">
              <span>Лимит строк</span>
              <select v-model.number="filters.limit" class="input input--select" @change="refresh">
                <option v-for="opt in limitOptions" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </label>
          </div>
        </section>

        <section v-if="loading" class="state-card">
          <v-progress-circular indeterminate />
          <div class="state-card__title">Загружаем данные…</div>
        </section>
        <section v-else-if="errorMessage" class="state-card state-card--error">
          <v-icon name="error_outline" />
          <div class="state-card__title">{{ errorMessage }}</div>
          <v-button @click="refresh"><v-icon name="refresh" left /> Попробовать снова</v-button>
        </section>

        <template v-else>
          <section class="totals">
            <div class="metric-card">
              <div class="metric-card__label">Всего пользователей</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_total) }}</div>
              <div class="metric-card__hint">в выбранном диапазоне</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">С UTM-меткой</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_with_utm) }}</div>
              <div class="metric-card__hint">{{ withUtmPercent }}% от всего</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">Без UTM</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_no_utm) }}</div>
              <div class="metric-card__hint">органика и старые юзеры</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">Источников</div>
              <div class="metric-card__value">{{ formatNumber(sources.length) }}</div>
              <div class="metric-card__hint">отдельных UTM-меток</div>
            </div>
          </section>

          <section v-if="sources.length === 0" class="state-card">
            <v-icon name="inbox" />
            <div class="state-card__title">Нет данных по выбранным фильтрам</div>
            <div class="state-card__hint">Попробуй убрать префикс или сдвинуть дату.</div>
          </section>

          <section v-else class="table-card">
            <div class="table-card__head">
              <div>Источники по конверсии — {{ visibleSources.length }} из {{ sources.length }}</div>
              <div class="table-card__hint">
                <strong>Клик по тегу UTM</strong> — поставить точный фильтр на сервер и подгрузить только эту кампанию.
                <strong>Клик по заголовку</strong> — сортировка по столбцу (↑ asc / ↓ desc / × сброс).
                <strong>Клик по строке</strong> — раскрыть метрики кампании (конверсия по воронке, ARPU, длительность жизни тега).
                В ячейках «Всего», «Активная подписка», «Платных», «Доход» под основной цифрой — сплит на прямых (D) и косвенных (I)
                согласно PR feat/acquisition-source-attribution.
              </div>
            </div>
            <div class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th
                      v-for="col in columns"
                      :key="col.key"
                      :class="['table__col', col.alignClass, 'table__col--sortable', sort.key === col.key ? 'table__col--sorted' : '']"
                      @click="toggleSort(col.key)"
                    >
                      <span class="sort-head">
                        <span>{{ col.label }}</span>
                        <span class="sort-indicator">{{ sortIndicator(col.key) }}</span>
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="row in visibleSources" :key="row.utm ?? '__no_utm__'">
                    <tr
                      :class="['table__row--clickable', expanded.has(row.utm ?? '__no_utm__') ? 'table__row--expanded' : '']"
                      @click="toggleExpand(row)"
                    >
                      <td class="table__col table__col--utm">
                        <button
                          v-if="row.utm"
                          type="button"
                          class="tag tag--campaign tag--clickable"
                          :title="'Подгрузить только ' + row.utm"
                          @click.stop="setExactFilter(row.utm)"
                        >{{ row.utm }}</button>
                        <span v-else class="tag tag--null">— без UTM —</span>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_total) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_registered) }}</td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_used_trial) }}</td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_key_activated) }}</td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_active_subscription) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_active_subscription_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_active_subscription_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_paid) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_paid_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_paid_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatRub(row.revenue_rub) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatRub(row.revenue_rub_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatRub(row.revenue_rub_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--date">{{ formatDate(row.first_seen) }}</td>
                      <td class="table__col table__col--date">{{ formatDate(row.last_seen) }}</td>
                    </tr>
                    <tr v-if="expanded.has(row.utm ?? '__no_utm__')" class="table__row--detail">
                      <td :colspan="columns.length">
                        <div class="detail-grid">
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в регистрацию</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_registered, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_registered) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в триал</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_used_trial, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_used_trial) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в платных</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_paid, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_paid) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">ARPU (по платным)</div>
                            <div class="detail-card__value">{{ formatRub(arpu(row)) }}</div>
                            <div class="detail-card__hint">{{ formatRub(row.revenue_rub) }} / {{ formatNumber(row.users_paid) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Доля косвенных</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_indirect, row.users_total) }}</div>
                            <div class="detail-card__hint">D {{ formatNumber(row.users_direct) }} · I {{ formatNumber(row.users_indirect) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Длительность жизни тега</div>
                            <div class="detail-card__value">{{ campaignDuration(row) }}</div>
                            <div class="detail-card__hint">{{ formatDate(row.first_seen) }} → {{ formatDate(row.last_seen) }}</div>
                          </div>
                        </div>
                        <div v-if="subTags(row).length" class="sub-tags">
                          <div class="sub-tags__label">Сабтеги в текущей выборке (одного префикса)</div>
                          <div class="sub-tags__list">
                            <button
                              v-for="sub in subTags(row)"
                              :key="sub.utm"
                              type="button"
                              class="tag tag--campaign tag--clickable"
                              :title="'Подгрузить только ' + sub.utm"
                              @click.stop="setExactFilter(sub.utm)"
                            >{{ sub.utm }} · {{ formatNumber(sub.users_total) }}</button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </section>

          <section class="footer-meta">
            <div>Сгенерировано: {{ formatDate(generatedAt) }}</div>
            <div v-if="filtersApplied.utm_prefix">Префикс: {{ filtersApplied.utm_prefix }}</div>
            <div v-if="filtersApplied.since">С даты: {{ formatDate(filtersApplied.since) }}</div>
          </section>
        </template>
      </div>
    </div>
  </private-view>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useApi } from "@directus/extensions-sdk";

const api = useApi();

const loading = ref(false);
const errorMessage = ref("");
const sources = ref([]);
const totals = reactive({ users_total: 0, users_with_utm: 0, users_no_utm: 0 });
const generatedAt = ref(null);
const filtersApplied = reactive({ utm_prefix: null, since: null, limit: 200 });

const limitOptions = [50, 100, 200, 500, 1000];

const filters = reactive({
  utm_prefix: "",
  since: "",
  limit: 200,
});

// Client-side state: local search + sort + expanded rows.
const localSearch = ref("");
const sort = reactive({ key: "users_total", dir: "desc" });
const expanded = ref(new Set());

// Columns descriptor drives header rendering AND sort dispatch. `alignClass`
// keeps numeric vs UTM vs date alignment in sync with body cells.
const columns = [
  { key: "utm", label: "UTM", alignClass: "table__col--utm" },
  { key: "users_total", label: "Всего", alignClass: "table__col--num" },
  { key: "users_registered", label: "Регистр.", alignClass: "table__col--num" },
  { key: "users_used_trial", label: "Триал", alignClass: "table__col--num" },
  { key: "users_key_activated", label: "Активация ключа", alignClass: "table__col--num" },
  { key: "users_active_subscription", label: "Активная подписка", alignClass: "table__col--num" },
  { key: "users_paid", label: "Платных", alignClass: "table__col--num" },
  { key: "revenue_rub", label: "Доход, ₽", alignClass: "table__col--num" },
  { key: "first_seen", label: "Первый", alignClass: "table__col--date" },
  { key: "last_seen", label: "Последний", alignClass: "table__col--date" },
];

const withUtmPercent = computed(() => {
  if (!totals.users_total) return 0;
  return Math.round((totals.users_with_utm / totals.users_total) * 100);
});

// Filter (local) -> sort -> render. Filtering and sorting both run client-side
// against the rows fetched by the server prefix filter, so toggling search /
// sort never hits the API.
const visibleSources = computed(() => {
  const needle = localSearch.value.trim().toLowerCase();
  let rows = sources.value;
  if (needle) {
    rows = rows.filter((row) => {
      const utm = (row.utm || "").toLowerCase();
      return utm.includes(needle) || (!row.utm && needle === "null");
    });
  }
  if (!sort.key) return rows;
  const dir = sort.dir === "asc" ? 1 : -1;
  const sortKey = sort.key;
  return [...rows].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    // null/undefined / utm comparison
    if (av == null && bv == null) return 0;
    if (av == null) return 1; // empty goes last
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv), "ru") * dir;
  });
});

// Sub-tags = rows whose utm starts with current row's utm + "_" or "." or "/".
// Surfaced when expanding a row so the user can drill into per-section
// breakdowns of a campaign family (e.g. qr_rt_launch_2026_05 -> _hero/_about).
function subTags(row) {
  if (!row.utm) return [];
  const base = row.utm;
  return sources.value.filter((other) => {
    if (!other.utm || other.utm === base) return false;
    return other.utm.startsWith(base + "_") || other.utm.startsWith(base + ".") || other.utm.startsWith(base + "/");
  });
}

function toggleSort(key) {
  if (sort.key !== key) {
    sort.key = key;
    // Numeric columns default to desc (biggest channel first); textual to asc.
    const numeric = ["users_total","users_registered","users_used_trial","users_key_activated","users_active_subscription","users_paid","revenue_rub"];
    sort.dir = numeric.includes(key) || key === "first_seen" || key === "last_seen" ? "desc" : "asc";
    return;
  }
  if (sort.dir === "desc") {
    sort.dir = "asc";
  } else if (sort.dir === "asc") {
    sort.key = null;
    sort.dir = "desc";
  }
}

function sortIndicator(key) {
  if (sort.key !== key) return "";
  return sort.dir === "asc" ? "↑" : "↓";
}

function toggleExpand(row) {
  const key = row.utm ?? "__no_utm__";
  const next = new Set(expanded.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expanded.value = next;
}

function setExactFilter(utm) {
  if (!utm) return;
  filters.utm_prefix = utm;
  expanded.value = new Set();
  refresh();
}

function arpu(row) {
  if (!row.users_paid || row.users_paid <= 0) return 0;
  return (row.revenue_rub || 0) / row.users_paid;
}

function campaignDuration(row) {
  if (!row.first_seen || !row.last_seen) return "—";
  try {
    const start = new Date(row.first_seen);
    const end = new Date(row.last_seen);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "—";
    const days = Math.round((end - start) / 86400000);
    if (days <= 0) return "в течение одного дня";
    if (days === 1) return "1 день";
    if (days < 5) return `${days} дня`;
    return `${days} дней`;
  } catch {
    return "—";
  }
}

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("ru-RU");
}

function formatRub(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value)).toLocaleString("ru-RU")} ₽`;
}

function formatPercent(part, whole) {
  const w = Number(whole);
  if (!w || w <= 0) return "—";
  const p = Number(part) || 0;
  const pct = (p / w) * 100;
  if (pct >= 10) return `${pct.toFixed(1)}%`;
  return `${pct.toFixed(2)}%`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return "—";
  }
}

function csvEscape(value) {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function exportCsv() {
  if (!visibleSources.value.length) return;
  const header = [
    "utm","users_total","users_direct","users_indirect",
    "users_registered","users_used_trial","users_key_activated",
    "users_active_subscription","users_active_subscription_direct","users_active_subscription_indirect",
    "users_paid","users_paid_direct","users_paid_indirect",
    "revenue_rub","revenue_rub_direct","revenue_rub_indirect",
    "first_seen","last_seen",
  ];
  const body = visibleSources.value.map((row) =>
    header.map((key) => csvEscape(row[key] ?? (key === "utm" ? "" : 0))).join(",")
  );
  const csv = [header.join(","), ...body].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
  const prefixPart = (filters.utm_prefix || "all").replace(/[^a-zA-Z0-9_]/g, "_");
  a.download = `utm-stats_${prefixPart}_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const params = {};
    const trimmedPrefix = (filters.utm_prefix || "").trim();
    if (trimmedPrefix) params.utm_prefix = trimmedPrefix;
    const trimmedSince = (filters.since || "").trim();
    if (trimmedSince) params.since = trimmedSince;
    if (filters.limit) params.limit = filters.limit;

    const resp = await api.get("/admin-widgets/utm-stats", { params });
    const data = resp?.data ?? {};
    sources.value = Array.isArray(data.sources) ? data.sources : [];
    Object.assign(totals, data.totals ?? {});
    Object.assign(filtersApplied, data.filters_applied ?? {});
    generatedAt.value = data.generated_at ?? null;
  } catch (err) {
    errorMessage.value = err?.response?.data?.error || err?.message || "Не удалось загрузить статистику";
    sources.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  refresh();
});
</script>

<style scoped>
:root,
.page,
.hero,
.metric-card,
.table-card,
.state-card {
  color-scheme: dark;
}

.page {
  padding: 32px 40px 80px;
  background: #0d1117;
  min-height: 100vh;
}

.page__main {
  display: flex;
  flex-direction: column;
  gap: 28px;
  max-width: 1480px;
  margin: 0 auto;
}

.nav {
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.nav__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.nav__brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #1f6feb, #8957e5);
  color: #fff;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.04em;
}

.nav__brand-title {
  color: #f0f6fc;
  font-weight: 600;
  font-size: 15px;
}

.nav__brand-subtitle {
  color: #8b949e;
  font-size: 12px;
  margin-top: 2px;
}

.nav__section-title {
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: #6e7681;
  margin-bottom: 8px;
}

.nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #c9d1d9;
  text-decoration: none;
  transition: background 120ms ease;
}

.nav__item:hover {
  background: rgba(255, 255, 255, 0.04);
}

.nav__item--active {
  background: rgba(31, 111, 235, 0.15);
  color: #f0f6fc;
}

.nav__item-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, auto);
  gap: 32px;
  padding: 28px 32px;
  background: linear-gradient(180deg, rgba(31, 111, 235, 0.08), rgba(31, 111, 235, 0));
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
  align-items: center;
}

.hero__kicker {
  font-size: 12px;
  color: #58a6ff;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.hero__title {
  margin: 4px 0 12px;
  font-size: 28px;
  font-weight: 700;
  color: #f0f6fc;
  letter-spacing: -0.01em;
}

.hero__subtitle {
  color: #8b949e;
  font-size: 14px;
  line-height: 1.5;
  max-width: 720px;
}

.hero__right {
  display: grid;
  gap: 12px;
}

.field {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: #8b949e;
}

.field--compact span {
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.input,
.input--select {
  background: #0d1117;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  color: #f0f6fc;
  font-size: 14px;
  font-family: inherit;
}

.input:focus,
.input--select:focus {
  outline: none;
  border-color: #58a6ff;
  box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.18);
}

.totals {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}

.metric-card {
  padding: 20px 22px;
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-card__label {
  font-size: 12px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.metric-card__value {
  font-size: 28px;
  font-weight: 700;
  color: #f0f6fc;
  letter-spacing: -0.01em;
}

.metric-card__hint {
  font-size: 12px;
  color: #6e7681;
}

.table-card {
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  overflow: hidden;
}

.table-card__head {
  padding: 18px 22px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  color: #f0f6fc;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.table-card__hint {
  font-size: 12px;
  color: #6e7681;
  font-weight: 400;
}

.table-wrap {
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.table th,
.table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.table th {
  color: #8b949e;
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  background: #0d1117;
  position: sticky;
  top: 0;
}

.table tbody tr:hover {
  background: rgba(31, 111, 235, 0.05);
}

.table__col--num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #f0f6fc;
}

.table__col--date {
  white-space: nowrap;
  color: #8b949e;
}

.cell-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.cell-stack__main {
  font-weight: 600;
  color: #f0f6fc;
}

.cell-stack__split {
  display: flex;
  gap: 4px;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.split-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.split-pill--direct {
  background: rgba(59, 201, 219, 0.12);
  color: #66d9e8;
  border: 1px solid rgba(59, 201, 219, 0.22);
}

.split-pill--indirect {
  background: rgba(151, 117, 250, 0.12);
  color: #b692f6;
  border: 1px solid rgba(151, 117, 250, 0.22);
}

.tag {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
}

.tag--campaign {
  background: rgba(88, 166, 255, 0.12);
  color: #58a6ff;
  border: 1px solid rgba(88, 166, 255, 0.25);
}

.tag--null {
  background: rgba(110, 118, 129, 0.12);
  color: #6e7681;
  border: 1px solid rgba(110, 118, 129, 0.25);
}

.tag--clickable {
  cursor: pointer;
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  line-height: 1.2;
  transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.05s;
}

.tag--clickable:hover {
  background: rgba(88, 166, 255, 0.22);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.45);
}

.tag--clickable:active {
  transform: translateY(1px);
}

.table__col--sortable {
  cursor: pointer;
  user-select: none;
  transition: background 0.12s, color 0.12s;
}

.table__col--sortable:hover {
  background: rgba(88, 166, 255, 0.05);
  color: #79b8ff;
}

.table__col--sorted {
  color: #58a6ff;
}

.sort-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.sort-indicator {
  min-width: 10px;
  text-align: center;
  font-weight: 700;
  color: #58a6ff;
}

.table__row--clickable {
  cursor: pointer;
  transition: background 0.12s;
}

.table__row--expanded {
  background: rgba(88, 166, 255, 0.04);
}

.table__row--detail > td {
  background: rgba(13, 17, 23, 0.55);
  border-top: 0;
  padding: 18px 22px 22px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}

.detail-card {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.55), rgba(13, 17, 23, 0.55));
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-card__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(201, 209, 217, 0.62);
  font-weight: 700;
}

.detail-card__value {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
  font-variant-numeric: tabular-nums;
}

.detail-card__hint {
  font-size: 11.5px;
  color: rgba(201, 209, 217, 0.55);
}

.sub-tags {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed rgba(110, 118, 129, 0.2);
}

.sub-tags__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(201, 209, 217, 0.62);
  font-weight: 700;
  margin-bottom: 8px;
}

.sub-tags__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 56px 32px;
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  color: #8b949e;
  text-align: center;
}

.state-card--error {
  border-color: rgba(248, 81, 73, 0.4);
}

.state-card__title {
  color: #f0f6fc;
  font-weight: 600;
}

.state-card__hint {
  color: #8b949e;
  font-size: 13px;
}

.footer-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 12px;
  color: #6e7681;
  padding-top: 4px;
}
</style>
