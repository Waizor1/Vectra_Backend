<template>
  <private-view title="Promo Studio">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">SVPN</div>
          <div>
            <div class="nav__brand-title">Promo Studio</div>
            <div class="nav__brand-subtitle">Промокоды и аналитика</div>
          </div>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/content/promo_batches' }">
            <span class="nav__item-icon"><v-icon name="inventory_2" /></span>
            <span class="nav__item-label">Кампании</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/content/promo_codes' }">
            <span class="nav__item-icon"><v-icon name="confirmation_number" /></span>
            <span class="nav__item-label">Промокоды</span>
          </router-link>
        </div>
      </div>
    </template>

    <template #actions>
      <v-button secondary :loading="refreshLoading" @click="refreshAll">
        <v-icon name="refresh" left />
        Обновить
      </v-button>
    </template>

    <div class="page">
      <div class="page__main">
        <section class="hero">
          <div class="hero__left">
            <div class="hero__kicker">Vectra Connect</div>
            <h1 class="hero__title">Promo Studio</h1>
            <p class="hero__subtitle">
              Операционный рабочий стол промокодов: ручной ввод или генерация, быстрый выпуск и прозрачная аналитика по
              активациям и доходу.
            </p>
            <div class="hero__chips">
              <span class="hero__chip">Ручной код или авто-генерация</span>
              <span class="hero__chip">Аналитика кампаний</span>
              <span class="hero__chip">Экспорт и копирование</span>
            </div>
          </div>

          <div class="hero__right">
            <label class="field field--compact">
              <span>Окно аналитики</span>
              <select v-model.number="filters.days" class="input input--select">
                <option v-for="days in daysOptions" :key="days" :value="days">{{ days }} дней</option>
              </select>
            </label>
            <label class="field field--compact">
              <span>Кампания</span>
              <select v-model="filters.campaign_id" class="input input--select">
                <option value="">Все кампании</option>
                <option v-for="campaign in campaignOptions" :key="campaign.id" :value="String(campaign.id)">
                  {{ campaign.title }}
                </option>
              </select>
            </label>
          </div>
        </section>

        <v-notice v-if="globalError" type="danger">
          {{ globalError }}
        </v-notice>

        <div class="kpi-grid">
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--blue">
                <v-icon name="confirmation_number" />
              </div>
              <div class="kpi__content">
                <div class="kpi__label">Коды</div>
                <div class="kpi__value">{{ fmtInt(analytics.summary.codes_total) }}</div>
                <div class="kpi__meta">Активных {{ fmtInt(analytics.summary.active_codes) }}</div>
              </div>
            </div>
          </v-card>

          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--green">
                <v-icon name="bolt" />
              </div>
              <div class="kpi__content">
                <div class="kpi__label">Активации</div>
                <div class="kpi__value">{{ fmtInt(analytics.summary.activations_total) }}</div>
                <div class="kpi__meta">Уникальных {{ fmtInt(analytics.summary.unique_users_total) }}</div>
              </div>
            </div>
          </v-card>

          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--amber">
                <v-icon name="payments" />
              </div>
              <div class="kpi__content">
                <div class="kpi__label">Доход</div>
                <div class="kpi__value">{{ fmtMoney(analytics.summary.attributed_revenue) }} ₽</div>
                <div class="kpi__meta">Платежей {{ fmtInt(analytics.summary.attributed_payments) }}</div>
              </div>
            </div>
          </v-card>

          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--purple">
                <v-icon name="trending_up" />
              </div>
              <div class="kpi__content">
                <div class="kpi__label">ARPU после активации</div>
                <div class="kpi__value">{{ fmtMoney(analytics.summary.avg_revenue_per_user) }} ₽</div>
                <div class="kpi__meta">На активацию {{ fmtMoney(analytics.summary.avg_revenue_per_activation) }} ₽</div>
              </div>
            </div>
          </v-card>
        </div>

        <section class="split">
          <v-card class="panel panel--form">
            <div class="panel__header">
              <div>
                <div class="panel__title">Создание промокода</div>
                <div class="panel__subtitle">
                  Выбери кампанию, задай параметры и выбери режим: генерация кода или ручной список.
                </div>
              </div>
            </div>

            <div class="flow">
              <div class="flow__step">
                <div class="flow__step-title">1. Кампания</div>
                <div class="segmented">
                  <button
                    type="button"
                    class="segmented__btn"
                    :class="{ 'segmented__btn--active': createForm.campaign_mode === 'existing' }"
                    @click="createForm.campaign_mode = 'existing'"
                  >
                    В существующую
                  </button>
                  <button
                    type="button"
                    class="segmented__btn"
                    :class="{ 'segmented__btn--active': createForm.campaign_mode === 'new' }"
                    @click="createForm.campaign_mode = 'new'"
                  >
                    Новая кампания
                  </button>
                  <button
                    type="button"
                    class="segmented__btn"
                    :class="{ 'segmented__btn--active': createForm.campaign_mode === 'none' }"
                    @click="createForm.campaign_mode = 'none'"
                  >
                    Без кампании
                  </button>
                </div>

                <div v-if="createForm.campaign_mode === 'existing'" class="fields fields--one">
                  <label class="field">
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
                  <label class="field">
                    <span>Название кампании</span>
                    <input v-model.trim="createForm.campaign_title" class="input" placeholder="Например: Telegram Ads март" />
                  </label>
                  <label class="field field--textarea">
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
                <div class="flow__step-title">2. Параметры и код</div>
                <div class="fields">
                  <label class="field">
                    <span>Название промокода (в админке)</span>
                    <input v-model.trim="createForm.name" class="input" placeholder="Например: TG Welcome 30%" />
                  </label>
                  <label class="field">
                    <span>Лимит активаций (всего)</span>
                    <input v-model.number="createForm.max_activations" type="number" min="1" class="input" />
                  </label>
                  <label class="field">
                    <span>Лимит на пользователя</span>
                    <input v-model.number="createForm.per_user_limit" type="number" min="1" class="input" />
                  </label>
                  <label class="field">
                    <span>Дата окончания</span>
                    <input v-model="createForm.expires_at" type="date" class="input" />
                  </label>
                </div>

                <div class="code-mode">
                  <div class="code-mode__title">Формат кода</div>
                  <div class="segmented segmented--compact">
                    <button
                      type="button"
                      class="segmented__btn"
                      :class="{ 'segmented__btn--active': createForm.code_mode === 'generate' }"
                      @click="createForm.code_mode = 'generate'"
                    >
                      Генерировать
                    </button>
                    <button
                      type="button"
                      class="segmented__btn"
                      :class="{ 'segmented__btn--active': createForm.code_mode === 'manual' }"
                      @click="createForm.code_mode = 'manual'"
                    >
                      Ввести вручную
                    </button>
                  </div>
                </div>

                <div v-if="createForm.code_mode === 'generate'" class="fields">
                  <label class="field">
                    <span>Префикс кода</span>
                    <input v-model.trim="createForm.code_prefix" class="input" placeholder="VECTRA" />
                  </label>
                  <label class="field">
                    <span>Сколько кодов создать</span>
                    <input v-model.number="createForm.count" type="number" min="1" max="150" class="input" />
                  </label>
                </div>

                <div v-else class="manual">
                  <label class="field field--textarea">
                    <span>Список кодов (по одному в строке или через запятую)</span>
                    <textarea
                      v-model.trim="createForm.manual_codes"
                      class="input input--textarea input--manual"
                      rows="5"
                      placeholder="WELCOME_2026&#10;TG_START_50&#10;VIP-ALPHA-01"
                    />
                  </label>
                  <div class="manual__meta">
                    <span>К созданию: {{ fmtInt(manualCodesState.codes.length) }}</span>
                    <span v-if="manualCodesState.duplicates.length">
                      Дубликатов: {{ fmtInt(manualCodesState.duplicates.length) }}
                    </span>
                    <span v-if="manualCodesState.invalid.length">
                      Ошибочных: {{ fmtInt(manualCodesState.invalid.length) }}
                    </span>
                  </div>
                  <div class="manual__hint">Формат: A-Z, 0-9, _, - и длина 4-64 символа.</div>
                  <div v-if="manualCodesState.invalid.length" class="notice notice--warn">
                    Некорректные коды: {{ manualCodesInvalidPreview }}
                    <span v-if="manualCodesInvalidRemainder > 0"> (+{{ manualCodesInvalidRemainder }})</span>
                  </div>
                </div>

                <div class="effects">
                  <div class="effects__title">Эффекты</div>
                  <div class="fields">
                    <label class="field">
                      <span>Продлить на дней</span>
                      <input v-model.number="createForm.extend_days" type="number" min="0" class="input" />
                    </label>
                    <label class="field">
                      <span>Скидка, %</span>
                      <input v-model.number="createForm.discount_percent" type="number" min="0" max="100" class="input" />
                    </label>
                    <label class="field">
                      <span>Добавить устройств</span>
                      <input v-model.number="createForm.add_hwid" type="number" min="0" class="input" />
                    </label>
                  </div>
                  <label class="field field--textarea">
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
                    <v-icon :name="createButtonIcon" left />
                    {{ createButtonLabel }}
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
                      {{ row.name || ("promo #" + row.id) }} · id {{ row.id }} · лимит {{ row.max_activations }} /
                      {{ row.per_user_limit }}
                    </div>
                  </div>
                  <v-button x-small secondary @click="copyCode(row.plain_code)">Копировать</v-button>
                </div>
              </div>
            </div>
          </v-card>

          <v-card class="panel">
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
          <v-card class="panel">
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
                        <span>{{ row.name || ("promo #" + row.promo_code_id) }}</span>
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

          <v-card class="panel">
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

        <section class="panel panel--links">
          <div class="panel__header">
            <div>
              <div class="panel__title">Raw-режим (без потери функциональности)</div>
              <div class="panel__subtitle">Для тонкой ручной правки доступны исходные коллекции Directus</div>
            </div>
          </div>
          <div class="control-links">
            <router-link class="control-link" :to="{ path: '/content/promo_batches' }">
              <div class="control-link__head">
                <v-icon name="inventory_2" />
                <span>Кампании</span>
              </div>
              <div class="control-link__desc">Сегменты, источники, гипотезы и заметки по рекламным потокам.</div>
              <div class="control-link__path">/content/promo_batches</div>
            </router-link>
            <router-link class="control-link" :to="{ path: '/content/promo_codes' }">
              <div class="control-link__head">
                <v-icon name="confirmation_number" />
                <span>Промокоды</span>
              </div>
              <div class="control-link__desc">Список кодов, статусы, лимиты и срок действия.</div>
              <div class="control-link__path">/content/promo_codes</div>
            </router-link>
            <router-link class="control-link" :to="{ path: '/content/promo_usages' }">
              <div class="control-link__head">
                <v-icon name="history" />
                <span>Использования</span>
              </div>
              <div class="control-link__desc">История активаций и пользовательский контекст.</div>
              <div class="control-link__path">/content/promo_usages</div>
            </router-link>
          </div>
        </section>
      </div>
    </div>
  </private-view>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const api = useApi();

const daysOptions = [7, 14, 30, 60, 90, 180, 365];
const MAX_CREATE_COUNT = 150;
const MANUAL_CODE_REGEX = /^[A-Z0-9_-]{4,64}$/;

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
  code_mode: "generate",
  code_prefix: "VECTRA",
  count: 1,
  manual_codes: "",
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

const isManualCodeMode = computed(() => createForm.value.code_mode === "manual");
const createButtonLabel = computed(() =>
  isManualCodeMode.value ? "Создать коды из списка" : "Создать и сгенерировать коды"
);
const createButtonIcon = computed(() => (isManualCodeMode.value ? "edit_note" : "auto_awesome"));
const manualCodesState = computed(() => parseManualCodes(createForm.value.manual_codes));
const manualCodesInvalidPreview = computed(() => manualCodesState.value.invalid.slice(0, 5).join(", "));
const manualCodesInvalidRemainder = computed(() =>
  Math.max(0, manualCodesState.value.invalid.length - 5)
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

function normalizeManualCode(value) {
  return String(value ?? "").trim().toUpperCase();
}

function parseManualCodes(value) {
  const chunks = String(value || "").split(/[\n,;]+/g);
  const seen = new Set();
  const duplicateSet = new Set();
  const codes = [];
  const invalid = [];

  for (const chunk of chunks) {
    const code = normalizeManualCode(chunk);
    if (!code) continue;

    if (!MANUAL_CODE_REGEX.test(code)) {
      invalid.push(code);
      continue;
    }

    if (seen.has(code)) {
      duplicateSet.add(code);
      continue;
    }

    seen.add(code);
    codes.push(code);
  }

  return {
    codes,
    invalid,
    duplicates: Array.from(duplicateSet),
  };
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
      code_mode: isManualCodeMode.value ? "manual" : "generate",
      max_activations: Math.max(1, Math.floor(toNum(createForm.value.max_activations) || 1)),
      per_user_limit: Math.max(1, Math.floor(toNum(createForm.value.per_user_limit) || 1)),
      expires_at: createForm.value.expires_at || null,
      name: createForm.value.name || "",
      effects: buildEffects(),
    };

    let manualDuplicates = 0;
    if (payload.code_mode === "manual") {
      const parsedManual = parseManualCodes(createForm.value.manual_codes);
      manualDuplicates = parsedManual.duplicates.length;

      if (parsedManual.invalid.length) {
        const invalidPreview = parsedManual.invalid.slice(0, 5).join(", ");
        const invalidRest = parsedManual.invalid.length > 5 ? ` (+${parsedManual.invalid.length - 5})` : "";
        throw new Error(
          `Некорректные коды: ${invalidPreview}${invalidRest}. Разрешены A-Z, 0-9, "_" и "-".`
        );
      }
      if (!parsedManual.codes.length) {
        throw new Error("Добавьте хотя бы один код для ручного режима");
      }
      if (parsedManual.codes.length > MAX_CREATE_COUNT) {
        throw new Error(`За один запуск можно создать не больше ${MAX_CREATE_COUNT} кодов`);
      }
      payload.manual_codes = parsedManual.codes;
    } else {
      payload.count = Math.max(1, Math.min(MAX_CREATE_COUNT, Math.floor(toNum(createForm.value.count) || 1)));
      payload.code_prefix = createForm.value.code_prefix || "VECTRA";
    }

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

    const createdCount = toNum(data.created_count || createdCodes.value.length);
    const skippedDuplicates = Math.max(manualDuplicates, toNum(data.skipped_duplicates));
    createNotice.value =
      payload.code_mode === "manual" && skippedDuplicates > 0
        ? `Готово: создано ${fmtInt(createdCount)} кодов. Дубликаты пропущены: ${fmtInt(skippedDuplicates)}.`
        : `Готово: создано ${fmtInt(createdCount)} кодов.`;

    if (payload.code_mode === "manual") {
      createForm.value.manual_codes = "";
    }

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
:deep(.private-view) {
  width: 100%;
}

:deep(.private-view__main) {
  max-width: none !important;
  width: 100% !important;
}

:deep(.private-view__content) {
  max-width: none !important;
  width: 100% !important;
  justify-content: stretch !important;
  justify-items: stretch !important;
  align-items: stretch !important;
}

:deep(.private-view__content > *) {
  max-width: none !important;
  width: 100% !important;
}

.page {
  padding: 16px 20px;
  width: 100%;
  min-width: 0;
  min-height: 100%;
  display: grid;
  background:
    radial-gradient(circle at 2% -10%, rgba(59, 130, 246, 0.28), transparent 36%),
    radial-gradient(circle at 105% 2%, rgba(16, 185, 129, 0.2), transparent 40%),
    linear-gradient(180deg, rgba(5, 11, 24, 0.96), rgba(8, 17, 35, 0.98));
  color: #e7edf8;
  font-family: "Manrope", "Segoe UI", sans-serif;
}

.page__main {
  display: grid;
  gap: 12px;
  min-width: 0;
  width: 100%;
  max-width: 1560px;
  margin: 0 auto;
}

.page__main > * {
  width: 100%;
  min-width: 0;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  gap: 14px;
  padding: 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(140deg, rgba(59, 130, 246, 0.14), rgba(16, 185, 129, 0.08));
}

.hero__kicker {
  display: inline-flex;
  width: fit-content;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(59, 130, 246, 0.2);
}

.hero__title {
  margin: 8px 0 0;
  font-size: 26px;
  line-height: 1.12;
  letter-spacing: 0.01em;
}

.hero__subtitle {
  margin: 8px 0 0;
  max-width: 800px;
  opacity: 0.86;
  font-size: 13px;
  line-height: 1.42;
}

.hero__chips {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.hero__chip {
  font-size: 11px;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
  opacity: 0.9;
}

.hero__right {
  display: grid;
  gap: 10px;
  align-content: start;
}

.field {
  display: grid;
  gap: 6px;
  font-size: 12px;
  opacity: 0.94;
}

.field--compact {
  gap: 5px;
}

.field--textarea {
  grid-column: 1 / -1;
}

.input {
  width: 100%;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: inherit;
  padding: 9px 10px;
}

.input:focus-visible {
  outline: 2px solid rgba(59, 130, 246, 0.44);
  outline-offset: 1px;
}

.input--select {
  appearance: none;
}

.input--select option {
  color: #0f172a;
  background: #f8fafc;
}

.input--select option:checked {
  color: #0b3d79;
  background: #bfdbfe;
}

.input--textarea {
  resize: vertical;
  min-height: 68px;
}

.input--manual {
  font-family: "JetBrains Mono", "Consolas", monospace;
  letter-spacing: 0.01em;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.kpi {
  padding: 14px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
}

.kpi__row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.kpi__icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: grid;
  place-items: center;
}

.kpi__icon--blue {
  background: rgba(59, 130, 246, 0.18);
}

.kpi__icon--green {
  background: rgba(16, 185, 129, 0.16);
}

.kpi__icon--amber {
  background: rgba(245, 158, 11, 0.2);
}

.kpi__icon--purple {
  background: rgba(139, 92, 246, 0.18);
}

.kpi__label {
  font-size: 12px;
  opacity: 0.78;
}

.kpi__value {
  margin-top: 2px;
  font-size: 21px;
  font-weight: 800;
  line-height: 1.1;
}

.kpi__meta {
  margin-top: 3px;
  font-size: 12px;
  opacity: 0.75;
}

.split {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 12px;
}

.panel {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  padding: 14px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  width: 100%;
  max-width: none !important;
  box-sizing: border-box;
}

.panel > * {
  width: 100%;
  min-width: 0;
}

.panel--form {
  background: linear-gradient(165deg, rgba(59, 130, 246, 0.06), rgba(255, 255, 255, 0.02));
}

.panel__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.panel__title {
  font-weight: 700;
  font-size: 17px;
}

.panel__subtitle {
  margin-top: 4px;
  font-size: 12px;
  opacity: 0.75;
  line-height: 1.35;
}

.flow {
  display: grid;
  gap: 10px;
}

.flow__step {
  display: grid;
  gap: 10px;
  padding: 11px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(2, 8, 23, 0.34);
}

.flow__step-title {
  font-size: 13px;
  font-weight: 700;
}

.segmented {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.02);
}

.segmented--compact {
  width: fit-content;
  max-width: 100%;
}

.segmented__btn {
  border: none;
  outline: none;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: transparent;
  color: inherit;
  opacity: 0.78;
  cursor: pointer;
}

.segmented__btn:hover {
  opacity: 1;
  background: rgba(255, 255, 255, 0.06);
}

.segmented__btn--active {
  opacity: 1;
  background: linear-gradient(120deg, rgba(59, 130, 246, 0.35), rgba(16, 185, 129, 0.26));
}

.fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.fields--one {
  grid-template-columns: 1fr;
}

.code-mode {
  display: grid;
  gap: 8px;
  margin-top: 2px;
}

.code-mode__title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.7;
}

.manual {
  display: grid;
  gap: 8px;
}

.manual__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  opacity: 0.85;
}

.manual__hint {
  font-size: 12px;
  opacity: 0.74;
}

.effects {
  display: grid;
  gap: 10px;
  margin-top: 4px;
}

.effects__title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.66;
}

.flow__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.created {
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
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
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.03);
}

.created__main {
  min-width: 0;
}

.created__code {
  font-family: "JetBrains Mono", "Consolas", monospace;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.created__meta {
  margin-top: 2px;
  font-size: 12px;
  opacity: 0.75;
}

.notice {
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 12px;
}

.notice--ok {
  background: rgba(16, 185, 129, 0.14);
  border: 1px solid rgba(16, 185, 129, 0.42);
  color: #7bf1ca;
}

.notice--warn {
  background: rgba(245, 158, 11, 0.16);
  border: 1px solid rgba(245, 158, 11, 0.4);
  color: #fdd280;
}

.notice--error {
  background: rgba(239, 68, 68, 0.13);
  border: 1px solid rgba(239, 68, 68, 0.36);
  color: #ffb4b4;
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
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  white-space: nowrap;
}

.table th {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.68;
}

.table tbody tr:hover {
  background: rgba(59, 130, 246, 0.07);
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
  background: rgba(16, 185, 129, 0.14);
  color: #7bf1ca;
  border-color: rgba(16, 185, 129, 0.4);
}

.badge--expired {
  background: rgba(245, 158, 11, 0.16);
  color: #fdd280;
  border-color: rgba(245, 158, 11, 0.38);
}

.badge--disabled {
  background: rgba(148, 163, 184, 0.16);
  color: #dbe8ff;
  border-color: rgba(148, 163, 184, 0.38);
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
  opacity: 0.76;
}

.timeline__bars {
  display: grid;
  gap: 3px;
}

.timeline__bar {
  height: 8px;
  border-radius: 999px;
  transition: width 0.24s ease;
}

.timeline__bar--act {
  background: linear-gradient(90deg, rgba(16, 185, 129, 0.88), rgba(6, 95, 70, 0.9));
}

.timeline__bar--rev {
  background: linear-gradient(90deg, rgba(245, 158, 11, 0.88), rgba(180, 83, 9, 0.9));
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
  gap: 10px;
}

.control-links {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}

.control-link {
  display: grid;
  gap: 6px;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid rgba(59, 130, 246, 0.24);
  background: linear-gradient(150deg, rgba(59, 130, 246, 0.12), rgba(255, 255, 255, 0.03));
  text-decoration: none;
  color: inherit;
}

.control-link:hover {
  background: linear-gradient(150deg, rgba(59, 130, 246, 0.18), rgba(255, 255, 255, 0.05));
}

.control-link__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
}

.control-link__desc {
  font-size: 12px;
  opacity: 0.82;
  line-height: 1.35;
}

.control-link__path {
  font-size: 11px;
  opacity: 0.68;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.nav {
  padding: 10px;
}

.nav__section {
  margin-bottom: 12px;
}

.nav__section-title {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.6;
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
  background: rgba(255, 255, 255, 0.05);
}

.nav__item--active {
  background: rgba(59, 130, 246, 0.14);
}

.nav--premium {
  display: grid;
  gap: 10px;
}

.nav__brand {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.16), rgba(16, 185, 129, 0.08));
}

.nav__brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 800;
  color: rgba(2, 8, 23, 0.95);
  background: linear-gradient(120deg, rgba(125, 211, 252, 0.95), rgba(110, 231, 183, 0.95));
}

.nav__brand-title {
  font-size: 13px;
  font-weight: 700;
}

.nav__brand-subtitle {
  font-size: 11px;
  opacity: 0.7;
}

.nav--premium .nav__item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 10px;
}

.nav__item-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.15);
}

.nav__item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1400px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .split {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .hero {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 860px) {
  .page {
    padding: 12px;
  }

  .kpi-grid {
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

  .control-links {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .hero__title {
    font-size: 22px;
  }

  .nav--premium {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 4px;
  }

  .nav--premium .nav__brand,
  .nav--premium .nav__section {
    min-width: 240px;
  }
}

/* Узкие смартфоны: вертикальный nav, full-width кнопки, 44px tap targets. */
@media (max-width: 480px) {
  .page { padding: 8px; gap: 8px; overflow-x: hidden; }
  .panel { padding: 10px; border-radius: 12px; }
  .hero { padding: 12px; }
  .hero__title { font-size: 18px; line-height: 1.25; }
  .hero__subtitle { font-size: 13px; }

  .nav--premium {
    flex-direction: column;
    overflow: visible;
  }
  .nav--premium .nav__brand,
  .nav--premium .nav__section {
    min-width: 0;
    width: 100%;
  }
  .nav--premium .nav__item {
    min-height: 44px;
    width: 100%;
    justify-content: flex-start;
  }

  .control-links { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: 1fr; gap: 8px; }
  .kpi-grid > * { padding: 12px; min-height: 72px; }

  /* Inputs и кнопки достигают минимум 44px для удобного тапа. */
  input, select, textarea { min-height: 44px; font-size: 16px; }
  :deep(.v-button) { min-height: 44px; }
}

@media (max-width: 360px) {
  .page { padding: 6px; }
  .panel, .hero { padding: 8px; }
}
</style>
