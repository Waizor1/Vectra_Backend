<template>
  <private-view title="Promo Studio">
    <template #navigation>
      <div class="nav">
        <div class="nav__section-title">Promo Studio</div>
        <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-promo-studio' }">
          <v-icon name="workspace_premium" />
          <span>Studio</span>
        </router-link>
        <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
          <v-icon name="home" />
          <span>Главная</span>
        </router-link>
        <router-link class="nav__item" :to="{ path: '/content/promo_batches' }">
          <v-icon name="inventory_2" />
          <span>Кампании</span>
        </router-link>
        <router-link class="nav__item" :to="{ path: '/content/promo_codes' }">
          <v-icon name="confirmation_number" />
          <span>Промокоды</span>
        </router-link>
      </div>
    </template>

    <template #actions>
      <v-button secondary :loading="refreshLoading" @click="refreshAll">
        <v-icon name="refresh" left />
        Обновить
      </v-button>
    </template>

    <div class="studio">
      <section class="hero panel-base">
        <div class="hero__top">
          <div class="hero__text">
            <h1 class="hero__title">Promo Studio</h1>
            <p class="hero__subtitle">
              Новый поток создания промокодов: без ручного ввода кода, с быстрой генерацией, копированием и
              прозрачной аналитикой по активациям и доходу.
            </p>
          </div>
          <div class="hero__filters">
            <label>
              <span>Окно аналитики</span>
              <select v-model.number="filters.days" class="input input--select">
                <option v-for="days in daysOptions" :key="days" :value="days">{{ days }} дней</option>
              </select>
            </label>
            <label>
              <span>Кампания</span>
              <select v-model="filters.campaign_id" class="input input--select">
                <option value="">Все кампании</option>
                <option v-for="campaign in campaignOptions" :key="campaign.id" :value="String(campaign.id)">
                  {{ campaign.title }}
                </option>
              </select>
            </label>
          </div>
        </div>
        <div v-if="globalError" class="notice notice--error">{{ globalError }}</div>
        <div class="kpi-grid">
          <div class="kpi">
            <div class="kpi__label">Коды</div>
            <div class="kpi__value">{{ fmtInt(analytics.summary.codes_total) }}</div>
            <div class="kpi__meta">Активных {{ fmtInt(analytics.summary.active_codes) }}</div>
          </div>
          <div class="kpi">
            <div class="kpi__label">Активации</div>
            <div class="kpi__value">{{ fmtInt(analytics.summary.activations_total) }}</div>
            <div class="kpi__meta">Уникальных {{ fmtInt(analytics.summary.unique_users_total) }}</div>
          </div>
          <div class="kpi">
            <div class="kpi__label">Доход</div>
            <div class="kpi__value">{{ fmtMoney(analytics.summary.attributed_revenue) }} ₽</div>
            <div class="kpi__meta">Платежей {{ fmtInt(analytics.summary.attributed_payments) }}</div>
          </div>
          <div class="kpi">
            <div class="kpi__label">ARPU после активации</div>
            <div class="kpi__value">{{ fmtMoney(analytics.summary.avg_revenue_per_user) }} ₽</div>
            <div class="kpi__meta">На активацию {{ fmtMoney(analytics.summary.avg_revenue_per_activation) }} ₽</div>
          </div>
        </div>
      </section>

      <section class="split">
        <v-card class="panel-base panel">
          <div class="panel__header">
            <div>
              <div class="panel__title">Создание промокода</div>
              <div class="panel__subtitle">Простой flow: выбери кампанию → задай параметры → создай и скопируй</div>
            </div>
          </div>

          <div class="flow">
            <div class="flow__step">
              <div class="flow__step-title">1. Кампания</div>
              <div class="mode-switch">
                <button
                  type="button"
                  class="mode-switch__btn"
                  :class="{ 'mode-switch__btn--active': createForm.campaign_mode === 'existing' }"
                  @click="createForm.campaign_mode = 'existing'"
                >
                  В существующую
                </button>
                <button
                  type="button"
                  class="mode-switch__btn"
                  :class="{ 'mode-switch__btn--active': createForm.campaign_mode === 'new' }"
                  @click="createForm.campaign_mode = 'new'"
                >
                  Новая кампания
                </button>
                <button
                  type="button"
                  class="mode-switch__btn"
                  :class="{ 'mode-switch__btn--active': createForm.campaign_mode === 'none' }"
                  @click="createForm.campaign_mode = 'none'"
                >
                  Без кампании
                </button>
              </div>

              <div v-if="createForm.campaign_mode === 'existing'" class="fields fields--one">
                <label>
                  <span>Кампания</span>
                  <select v-model="createForm.campaign_id" class="input input--select">
                    <option value="">Выбери кампанию</option>
                    <option v-for="campaign in campaignOptions" :key="campaign.id" :value="String(campaign.id)">
                      {{ campaign.title }}
                    </option>
                  </select>
                </label>
              </div>

              <div v-if="createForm.campaign_mode === 'new'" class="fields fields--one">
                <label>
                  <span>Название кампании</span>
                  <input v-model.trim="createForm.campaign_title" class="input" placeholder="Например: Telegram Ads март" />
                </label>
                <label>
                  <span>Заметка</span>
                  <textarea
                    v-model.trim="createForm.campaign_notes"
                    class="input input--textarea"
                    rows="2"
                    placeholder="Источник трафика, гипотеза, комментарий"
                  />
                </label>
              </div>
            </div>

            <div class="flow__step">
              <div class="flow__step-title">2. Параметры</div>
              <div class="fields">
                <label>
                  <span>Название промокода (в админке)</span>
                  <input v-model.trim="createForm.name" class="input" placeholder="Например: TG Welcome 30%" />
                </label>
                <label>
                  <span>Префикс кода</span>
                  <input v-model.trim="createForm.code_prefix" class="input" placeholder="TVPN" />
                </label>
                <label>
                  <span>Сколько кодов создать</span>
                  <input v-model.number="createForm.count" type="number" min="1" max="150" class="input" />
                </label>
                <label>
                  <span>Лимит активаций (всего)</span>
                  <input v-model.number="createForm.max_activations" type="number" min="1" class="input" />
                </label>
                <label>
                  <span>Лимит на пользователя</span>
                  <input v-model.number="createForm.per_user_limit" type="number" min="1" class="input" />
                </label>
                <label>
                  <span>Дата окончания</span>
                  <input v-model="createForm.expires_at" type="date" class="input" />
                </label>
              </div>

              <div class="effects">
                <div class="effects__title">Эффекты</div>
                <div class="fields">
                  <label>
                    <span>Продлить на дней</span>
                    <input v-model.number="createForm.extend_days" type="number" min="0" class="input" />
                  </label>
                  <label>
                    <span>Скидка, %</span>
                    <input v-model.number="createForm.discount_percent" type="number" min="0" max="100" class="input" />
                  </label>
                  <label>
                    <span>Добавить устройств</span>
                    <input v-model.number="createForm.add_hwid" type="number" min="0" class="input" />
                  </label>
                </div>
                <label>
                  <span>Доп. JSON эффектов (опционально)</span>
                  <textarea
                    v-model.trim="createForm.effects_json"
                    class="input input--textarea"
                    rows="3"
                    placeholder='{"one_time": true}'
                  />
                </label>
              </div>
            </div>

            <div class="flow__step">
              <div class="flow__step-title">3. Создание</div>
              <div class="flow__actions">
                <v-button kind="primary" :loading="createLoading" @click="createPromoCodes">
                  <v-icon name="auto_awesome" left />
                  Создать и сгенерировать коды
                </v-button>
              </div>
              <div v-if="createError" class="notice notice--error">{{ createError }}</div>
              <div v-if="createNotice" class="notice notice--ok">{{ createNotice }}</div>
            </div>
          </div>

          <div v-if="createdCodes.length" class="created">
            <div class="created__header">
              <div class="created__title">Созданные коды</div>
              <v-button x-small secondary @click="copyAllCodes">
                <v-icon name="content_copy" left />
                Копировать все
              </v-button>
            </div>
            <div class="created__list">
              <div v-for="row in createdCodes" :key="row.id" class="created__row">
                <div class="created__main">
                  <div class="created__code">{{ row.plain_code }}</div>
                  <div class="created__meta">
                    {{ row.name || ('promo #' + row.id) }} · id {{ row.id }} · лимит {{ row.max_activations }} / {{
                      row.per_user_limit
                    }}
                  </div>
                </div>
                <v-button x-small secondary @click="copyCode(row.plain_code)">Копировать</v-button>
              </div>
            </div>
          </div>
        </v-card>

        <v-card class="panel-base panel">
          <div class="panel__header">
            <div>
              <div class="panel__title">Кампании: активации и доход</div>
              <div class="panel__subtitle">Сравнение рекламных кампаний по результату</div>
            </div>
          </div>
          <div v-if="analyticsLoading" class="empty">Загрузка аналитики...</div>
          <div v-else-if="!analytics.campaigns.length" class="empty">Нет данных за выбранный период.</div>
          <div v-else class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>Кампания</th>
                  <th>Коды</th>
                  <th>Активации</th>
                  <th>Пользователи</th>
                  <th>Доход</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in analytics.campaigns" :key="`camp-${row.campaign_id || 'none'}`">
                  <td>{{ row.campaign_title }}</td>
                  <td>{{ fmtInt(row.codes_total) }}</td>
                  <td>{{ fmtInt(row.activations) }}</td>
                  <td>{{ fmtInt(row.unique_users) }}</td>
                  <td>{{ fmtMoney(row.attributed_revenue) }} ₽</td>
                </tr>
              </tbody>
            </table>
          </div>
        </v-card>
      </section>

      <section class="split">
        <v-card class="panel-base panel">
          <div class="panel__header">
            <div>
              <div class="panel__title">Промокоды: лидеры</div>
              <div class="panel__subtitle">Какие коды дали больше активаций и денег</div>
            </div>
          </div>
          <div v-if="analyticsLoading" class="empty">Загрузка аналитики...</div>
          <div v-else-if="!analytics.codes.length" class="empty">Нет данных за выбранный период.</div>
          <div v-else class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>Код</th>
                  <th>Кампания</th>
                  <th>Статус</th>
                  <th>Активации</th>
                  <th>Доход</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in analytics.codes" :key="`code-${row.promo_code_id}`">
                  <td>
                    <div class="cell-main">
                      <span>{{ row.name || ('promo #' + row.promo_code_id) }}</span>
                      <small>id {{ row.promo_code_id }}</small>
                    </div>
                  </td>
                  <td>{{ row.campaign_title }}</td>
                  <td>
                    <span class="badge" :class="`badge--${row.status || 'active'}`">{{ statusLabel(row.status) }}</span>
                  </td>
                  <td>{{ fmtInt(row.activations) }}</td>
                  <td>{{ fmtMoney(row.attributed_revenue) }} ₽</td>
                </tr>
              </tbody>
            </table>
          </div>
        </v-card>

        <v-card class="panel-base panel">
          <div class="panel__header">
            <div>
              <div class="panel__title">Динамика</div>
              <div class="panel__subtitle">Активации и доход по дням</div>
            </div>
          </div>
          <div v-if="analyticsLoading" class="empty">Загрузка аналитики...</div>
          <div v-else-if="!analytics.timeline.length" class="empty">Нет данных за выбранный период.</div>
          <div v-else class="timeline">
            <div v-for="point in analytics.timeline" :key="`day-${point.day}`" class="timeline__row">
              <div class="timeline__day">{{ point.day }}</div>
              <div class="timeline__bars">
                <div class="timeline__bar timeline__bar--act" :style="{ width: activationBarWidth(point.activations) }" />
                <div class="timeline__bar timeline__bar--rev" :style="{ width: revenueBarWidth(point.attributed_revenue) }" />
              </div>
              <div class="timeline__meta">
                <span>{{ fmtInt(point.activations) }} акт.</span>
                <span>{{ fmtMoney(point.attributed_revenue) }} ₽</span>
              </div>
            </div>
          </div>
        </v-card>
      </section>

      <section class="panel-base panel panel--links">
        <div class="panel__header">
          <div>
            <div class="panel__title">Raw-режим (без потери функциональности)</div>
            <div class="panel__subtitle">Если нужно тонко править данные, доступны исходные коллекции Directus</div>
          </div>
        </div>
        <div class="links-grid">
          <router-link class="link-tile" :to="{ path: '/content/promo_batches' }">
            <v-icon name="inventory_2" />
            <span>Кампании</span>
          </router-link>
          <router-link class="link-tile" :to="{ path: '/content/promo_codes' }">
            <v-icon name="confirmation_number" />
            <span>Промокоды</span>
          </router-link>
          <router-link class="link-tile" :to="{ path: '/content/promo_usages' }">
            <v-icon name="history" />
            <span>Использования</span>
          </router-link>
        </div>
      </section>
    </div>
  </private-view>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const api = useApi();

const daysOptions = [7, 14, 30, 60, 90, 180, 365];

const refreshLoading = ref(false);
const analyticsLoading = ref(false);
const createLoading = ref(false);
const globalError = ref("");
const createError = ref("");
const createNotice = ref("");

const filters = ref({
  days: 30,
  campaign_id: "",
});

const bootstrap = ref({
  setup_ready: false,
  campaigns: [],
  counters: {
    campaigns_total: 0,
    codes_total: 0,
    active_codes: 0,
    activations_total: 0,
  },
});

const analytics = ref({
  summary: {
    codes_total: 0,
    active_codes: 0,
    campaigns_total: 0,
    activations_total: 0,
    unique_users_total: 0,
    attributed_revenue: 0,
    attributed_payments: 0,
    avg_revenue_per_activation: 0,
    avg_revenue_per_user: 0,
  },
  campaigns: [],
  codes: [],
  timeline: [],
});

const createForm = ref({
  campaign_mode: "existing",
  campaign_id: "",
  campaign_title: "",
  campaign_notes: "",
  name: "",
  code_prefix: "TVPN",
  count: 1,
  max_activations: 1,
  per_user_limit: 1,
  expires_at: "",
  extend_days: 0,
  discount_percent: 0,
  add_hwid: 0,
  effects_json: "",
});

const createdCodes = ref([]);
let analyticsRequestId = 0;

const campaignOptions = computed(() =>
  (Array.isArray(bootstrap.value.campaigns) ? bootstrap.value.campaigns : []).map((row) => ({
    id: row.id,
    title: row.title || `Кампания #${row.id}`,
  }))
);

const timelineMaxActivations = computed(() =>
  Math.max(1, ...analytics.value.timeline.map((row) => toNum(row.activations)))
);
const timelineMaxRevenue = computed(() =>
  Math.max(1, ...analytics.value.timeline.map((row) => toNum(row.attributed_revenue)))
);

function toNum(value) {
  const asNumber = Number(value ?? 0);
  return Number.isFinite(asNumber) ? asNumber : 0;
}

function fmtInt(value) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(toNum(value));
}

function fmtMoney(value) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(toNum(value));
}

function statusLabel(status) {
  if (status === "disabled") return "Отключен";
  if (status === "expired") return "Истек";
  return "Активен";
}

function activationBarWidth(value) {
  const width = (toNum(value) / timelineMaxActivations.value) * 100;
  return `${Math.max(3, Math.min(100, width))}%`;
}

function revenueBarWidth(value) {
  const width = (toNum(value) / timelineMaxRevenue.value) * 100;
  return `${Math.max(3, Math.min(100, width))}%`;
}

function parseError(err, fallback) {
  const directusErr = err?.response?.data?.error;
  const details = err?.response?.data?.details;
  if (directusErr && details) return `${directusErr}: ${details}`;
  if (directusErr) return String(directusErr);
  if (err?.message) return String(err.message);
  return fallback;
}

async function loadBootstrap() {
  const resp = await api.get("/admin-widgets/promo-studio/bootstrap");
  const data = resp?.data || {};
  bootstrap.value = {
    setup_ready: data.setup_ready === true,
    campaigns: Array.isArray(data.campaigns) ? data.campaigns : [],
    counters: data.counters || bootstrap.value.counters,
  };

  const campaignIds = new Set(campaignOptions.value.map((row) => String(row.id)));
  if (filters.value.campaign_id && !campaignIds.has(filters.value.campaign_id)) {
    filters.value.campaign_id = "";
  }
  if (createForm.value.campaign_id && !campaignIds.has(createForm.value.campaign_id)) {
    createForm.value.campaign_id = "";
  }
}

async function loadAnalytics() {
  const reqId = ++analyticsRequestId;
  analyticsLoading.value = true;
  try {
    const params = {
      days: Math.max(1, Math.min(365, toNum(filters.value.days) || 30)),
      limit: 40,
    };
    if (filters.value.campaign_id) {
      params.campaign_id = Number.parseInt(filters.value.campaign_id, 10);
    }
    const resp = await api.get("/admin-widgets/promo-studio/analytics", { params });
    if (reqId !== analyticsRequestId) return;
    const data = resp?.data || {};
    analytics.value = {
      summary: data.summary || analytics.value.summary,
      campaigns: Array.isArray(data.campaigns) ? data.campaigns : [],
      codes: Array.isArray(data.codes) ? data.codes : [],
      timeline: Array.isArray(data.timeline) ? data.timeline : [],
    };
  } catch (err) {
    if (reqId !== analyticsRequestId) return;
    globalError.value = parseError(err, "Не удалось загрузить аналитику промокодов");
  } finally {
    if (reqId === analyticsRequestId) {
      analyticsLoading.value = false;
    }
  }
}

async function refreshAll() {
  refreshLoading.value = true;
  globalError.value = "";
  try {
    await loadBootstrap();
    await loadAnalytics();
  } catch (err) {
    globalError.value = parseError(err, "Не удалось обновить Promo Studio");
  } finally {
    refreshLoading.value = false;
  }
}

function buildEffects() {
  const effects = {};
  const extendDays = Math.max(0, toNum(createForm.value.extend_days));
  const discountPercent = Math.max(0, toNum(createForm.value.discount_percent));
  const addHwid = Math.max(0, toNum(createForm.value.add_hwid));
  if (extendDays > 0) effects.extend_days = Math.floor(extendDays);
  if (discountPercent > 0) effects.discount_percent = Math.floor(discountPercent);
  if (addHwid > 0) effects.add_hwid = Math.floor(addHwid);

  const jsonRaw = String(createForm.value.effects_json || "").trim();
  if (jsonRaw) {
    const parsed = JSON.parse(jsonRaw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Доп. JSON эффектов должен быть объектом");
    }
    Object.assign(effects, parsed);
  }
  return effects;
}

async function createPromoCodes() {
  createError.value = "";
  createNotice.value = "";
  createLoading.value = true;
  try {
    if (createForm.value.campaign_mode === "existing" && !createForm.value.campaign_id) {
      throw new Error("Выберите существующую кампанию или переключитесь на новый режим");
    }
    if (createForm.value.campaign_mode === "new" && !String(createForm.value.campaign_title || "").trim()) {
      throw new Error("Укажите название новой кампании");
    }

    const payload = {
      count: Math.max(1, Math.min(150, Math.floor(toNum(createForm.value.count) || 1))),
      max_activations: Math.max(1, Math.floor(toNum(createForm.value.max_activations) || 1)),
      per_user_limit: Math.max(1, Math.floor(toNum(createForm.value.per_user_limit) || 1)),
      expires_at: createForm.value.expires_at || null,
      code_prefix: createForm.value.code_prefix || "TVPN",
      name: createForm.value.name || "",
      effects: buildEffects(),
    };

    if (createForm.value.campaign_mode === "existing") {
      payload.campaign_id = Number.parseInt(createForm.value.campaign_id, 10);
    }
    if (createForm.value.campaign_mode === "new") {
      payload.campaign_title = createForm.value.campaign_title;
      payload.campaign_notes = createForm.value.campaign_notes;
    }

    const resp = await api.post("/admin-widgets/promo-studio/create", payload);
    const data = resp?.data || {};
    createdCodes.value = Array.isArray(data.created) ? data.created : [];

    if (data?.campaign?.id) {
      const campaignIdStr = String(data.campaign.id);
      createForm.value.campaign_mode = "existing";
      createForm.value.campaign_id = campaignIdStr;
      filters.value.campaign_id = campaignIdStr;
    }

    createNotice.value = `Готово: создано ${fmtInt(data.created_count || createdCodes.value.length)} кодов.`;

    await loadBootstrap();
    await loadAnalytics();
  } catch (err) {
    createError.value = parseError(err, "Не удалось создать промокоды");
  } finally {
    createLoading.value = false;
  }
}

async function copyCode(value) {
  try {
    await navigator.clipboard.writeText(String(value || ""));
    createNotice.value = "Код скопирован";
  } catch (_err) {
    createError.value = "Не удалось скопировать код";
  }
}

async function copyAllCodes() {
  try {
    const payload = createdCodes.value.map((row) => row.plain_code).filter(Boolean).join("\n");
    await navigator.clipboard.writeText(payload);
    createNotice.value = "Список кодов скопирован";
  } catch (_err) {
    createError.value = "Не удалось скопировать список";
  }
}

watch(
  () => [filters.value.days, filters.value.campaign_id],
  () => {
    loadAnalytics();
  }
);

onMounted(() => {
  refreshAll();
});
</script>

<style scoped>
.studio {
  --studio-ink: #10222b;
  --studio-teal: #0e9f9a;
  --studio-amber: #d18b2a;
  --studio-border: rgba(15, 23, 42, 0.16);
  --studio-soft: rgba(255, 255, 255, 0.78);
  --studio-soft-2: rgba(255, 255, 255, 0.56);
  --studio-bg: radial-gradient(circle at 8% 0%, rgba(14, 159, 154, 0.2), transparent 45%),
    radial-gradient(circle at 100% 0%, rgba(209, 139, 42, 0.24), transparent 50%),
    linear-gradient(180deg, rgba(247, 249, 250, 0.95), rgba(239, 243, 246, 0.96));
  font-family: "Manrope", "Segoe UI", "Trebuchet MS", sans-serif;
  color: var(--studio-ink);
  display: grid;
  gap: 12px;
  padding: 14px 18px;
  background: var(--studio-bg);
  min-height: 100%;
}

.panel-base {
  border-radius: 16px;
  border: 1px solid var(--studio-border);
  background: linear-gradient(165deg, var(--studio-soft), var(--studio-soft-2));
  backdrop-filter: blur(10px);
  animation: panel-in 0.28s ease both;
}

@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.hero {
  padding: 16px;
}

.hero__top {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  align-items: end;
}

.hero__title {
  margin: 0;
  font-size: 28px;
  line-height: 1.1;
  letter-spacing: 0.01em;
}

.hero__subtitle {
  margin: 8px 0 0;
  max-width: 920px;
  opacity: 0.84;
}

.hero__filters {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.hero__filters label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  opacity: 0.88;
}

.kpi-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
}

.kpi {
  border-radius: 12px;
  border: 1px solid rgba(16, 34, 43, 0.12);
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.66);
}

.kpi__label {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.66;
}

.kpi__value {
  margin-top: 4px;
  font-size: 24px;
  font-weight: 800;
  color: #114f60;
}

.kpi__meta {
  margin-top: 3px;
  font-size: 12px;
  opacity: 0.8;
}

.split {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 12px;
}

.panel {
  padding: 14px;
}

.panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.panel__title {
  font-size: 17px;
  font-weight: 770;
}

.panel__subtitle {
  margin-top: 2px;
  font-size: 12px;
  opacity: 0.72;
}

.flow {
  display: grid;
  gap: 10px;
}

.flow__step {
  border: 1px solid rgba(16, 34, 43, 0.12);
  border-radius: 12px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.58);
}

.flow__step-title {
  font-size: 13px;
  font-weight: 720;
  margin-bottom: 8px;
}

.mode-switch {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mode-switch__btn {
  border: 1px solid rgba(16, 34, 43, 0.22);
  background: rgba(255, 255, 255, 0.65);
  border-radius: 999px;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 12px;
}

.mode-switch__btn--active {
  border-color: rgba(14, 159, 154, 0.75);
  background: rgba(14, 159, 154, 0.14);
}

.fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.fields--one {
  grid-template-columns: 1fr;
  margin-top: 8px;
}

.fields label {
  display: grid;
  gap: 5px;
  font-size: 12px;
  opacity: 0.92;
}

.input {
  width: 100%;
  border-radius: 10px;
  border: 1px solid rgba(16, 34, 43, 0.22);
  background: rgba(255, 255, 255, 0.82);
  color: inherit;
  padding: 8px 10px;
}

.input--select {
  appearance: none;
}

.input--textarea {
  resize: vertical;
}

.effects {
  margin-top: 10px;
  display: grid;
  gap: 10px;
}

.effects__title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.62;
}

.flow__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.created {
  margin-top: 12px;
  border-top: 1px solid rgba(16, 34, 43, 0.14);
  padding-top: 10px;
  display: grid;
  gap: 8px;
}

.created__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.created__title {
  font-size: 13px;
  font-weight: 700;
}

.created__list {
  display: grid;
  gap: 6px;
  max-height: 250px;
  overflow: auto;
  padding-right: 2px;
}

.created__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid rgba(16, 34, 43, 0.14);
  border-radius: 10px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.7);
}

.created__code {
  font-family: "Consolas", "JetBrains Mono", monospace;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.created__meta {
  font-size: 12px;
  opacity: 0.75;
  margin-top: 2px;
}

.notice {
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 12px;
  margin-top: 8px;
}

.notice--ok {
  background: rgba(10, 138, 104, 0.12);
  border: 1px solid rgba(10, 138, 104, 0.34);
  color: #0a7d5f;
}

.notice--error {
  background: rgba(185, 28, 28, 0.1);
  border: 1px solid rgba(185, 28, 28, 0.34);
  color: #991b1b;
}

.table-wrap {
  overflow: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.table th,
.table td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid rgba(16, 34, 43, 0.12);
  white-space: nowrap;
}

.table th {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.65;
}

.cell-main {
  display: grid;
}

.cell-main small {
  opacity: 0.65;
}

.badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  border: 1px solid transparent;
}

.badge--active {
  background: rgba(10, 138, 104, 0.12);
  color: #0a7d5f;
  border-color: rgba(10, 138, 104, 0.34);
}

.badge--expired {
  background: rgba(209, 139, 42, 0.14);
  color: #9c5f0f;
  border-color: rgba(209, 139, 42, 0.38);
}

.badge--disabled {
  background: rgba(148, 163, 184, 0.18);
  color: #334155;
  border-color: rgba(100, 116, 139, 0.45);
}

.timeline {
  display: grid;
  gap: 7px;
}

.timeline__row {
  display: grid;
  grid-template-columns: 96px 1fr auto;
  align-items: center;
  gap: 8px;
}

.timeline__day {
  font-size: 12px;
  opacity: 0.75;
}

.timeline__bars {
  position: relative;
  display: grid;
  gap: 3px;
}

.timeline__bar {
  height: 8px;
  border-radius: 999px;
  transition: width 0.24s ease;
}

.timeline__bar--act {
  background: linear-gradient(90deg, rgba(14, 159, 154, 0.88), rgba(12, 121, 120, 0.9));
}

.timeline__bar--rev {
  background: linear-gradient(90deg, rgba(209, 139, 42, 0.88), rgba(164, 102, 19, 0.92));
}

.timeline__meta {
  min-width: 138px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.empty {
  font-size: 13px;
  opacity: 0.74;
  padding: 6px 0;
}

.panel--links {
  display: grid;
  gap: 10px;
}

.links-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.link-tile {
  display: flex;
  align-items: center;
  gap: 8px;
  text-decoration: none;
  color: inherit;
  border: 1px solid rgba(16, 34, 43, 0.15);
  border-radius: 10px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.64);
}

.link-tile:hover {
  border-color: rgba(14, 159, 154, 0.45);
  background: rgba(14, 159, 154, 0.08);
}

.nav {
  padding: 10px;
}

.nav__section-title {
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.65;
  margin: 8px 8px 6px;
}

.nav__item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 10px;
  text-decoration: none;
  color: inherit;
}

.nav__item:hover {
  background: rgba(14, 159, 154, 0.1);
}

.nav__item--active {
  background: rgba(14, 159, 154, 0.18);
}

@media (max-width: 1200px) {
  .split {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 860px) {
  .hero__top {
    grid-template-columns: 1fr;
  }

  .fields {
    grid-template-columns: 1fr;
  }

  .timeline__row {
    grid-template-columns: 1fr;
    gap: 5px;
  }

  .timeline__meta {
    min-width: 0;
    justify-content: flex-start;
  }
}
</style>
