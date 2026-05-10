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
              <span>Префикс UTM</span>
              <input
                v-model="filters.utm_prefix"
                type="text"
                class="input"
                placeholder="напр. qr_rt_"
                @keyup.enter="refresh"
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
              <div>Источники по конверсии</div>
              <div class="table-card__hint">Сортировка по убыванию total — самый большой канал сверху</div>
            </div>
            <div class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th class="table__col table__col--utm">UTM</th>
                    <th class="table__col table__col--num">Всего</th>
                    <th class="table__col table__col--num">Регистр.</th>
                    <th class="table__col table__col--num">Триал</th>
                    <th class="table__col table__col--num">Активация ключа</th>
                    <th class="table__col table__col--num">Активная подписка</th>
                    <th class="table__col table__col--num">Платных</th>
                    <th class="table__col table__col--num">Доход, ₽</th>
                    <th class="table__col table__col--date">Первый</th>
                    <th class="table__col table__col--date">Последний</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in sources" :key="row.utm ?? '__no_utm__'">
                    <td class="table__col table__col--utm">
                      <span v-if="row.utm" class="tag tag--campaign">{{ row.utm }}</span>
                      <span v-else class="tag tag--null">— без UTM —</span>
                    </td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_total) }}</td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_registered) }}</td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_used_trial) }}</td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_key_activated) }}</td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_active_subscription) }}</td>
                    <td class="table__col table__col--num">{{ formatNumber(row.users_paid) }}</td>
                    <td class="table__col table__col--num">{{ formatRub(row.revenue_rub) }}</td>
                    <td class="table__col table__col--date">{{ formatDate(row.first_seen) }}</td>
                    <td class="table__col table__col--date">{{ formatDate(row.last_seen) }}</td>
                  </tr>
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

const withUtmPercent = computed(() => {
  if (!totals.users_total) return 0;
  return Math.round((totals.users_with_utm / totals.users_total) * 100);
});

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("ru-RU");
}

function formatRub(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value)).toLocaleString("ru-RU")} ₽`;
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

.tag {
  display: inline-flex;
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
