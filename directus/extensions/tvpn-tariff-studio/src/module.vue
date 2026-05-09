<template>
  <private-view title="Tariff Studio">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">TVPN</div>
          <div>
            <div class="nav__brand-title">Tariff Studio</div>
            <div class="nav__brand-subtitle">Конструктор сроков, устройств и LTE</div>
          </div>
        </div>
        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-tariff-studio' }">
            <span class="nav__item-icon"><v-icon name="tune" /></span>
            <span class="nav__item-label">Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-segment-studio' }">
            <span class="nav__item-icon"><v-icon name="campaign" /></span>
            <span class="nav__item-label">Сегментные акции</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Промокоды</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/content/tariffs' }">
            <span class="nav__item-icon"><v-icon name="table_view" /></span>
            <span class="nav__item-label">Raw тарифы</span>
          </router-link>
        </div>
      </div>
    </template>

    <template #actions>
      <v-button secondary :loading="loading" @click="loadTariffs">
        <v-icon name="refresh" left />Обновить
      </v-button>
      <v-button :disabled="!selected || localBlockingErrors.length > 0 || serverBlockingErrors.length > 0" :loading="saving" @click="saveSelected">
        <v-icon name="save" left />Сохранить
      </v-button>
    </template>

    <div class="page">
      <div class="page__main">
        <section class="hero">
          <div>
            <div class="hero__kicker">Vectra Connect</div>
            <h1 class="hero__title">Настройка тарифного конструктора</h1>
            <p class="hero__subtitle">
              Основной UX — 4 срока, цена за 1 устройство, семейная ёмкость от 2 устройств и LTE как добавка к периоду.
              Служебные поля base_price/progressive_multiplier пересчитает backend hook при сохранении.
            </p>
            <div class="hero__chips">
              <span>Семья от 2 устройств</span>
              <span>Backend quote — источник истины</span>
              <span>Advanced-поля скрыты в raw тарифах</span>
            </div>
          </div>
          <div class="hero__policy">
            <strong>Политика конструктора</strong>
            <span>1 устройство = персональная подписка</span>
            <span>2+ устройства = семейная подписка/ёмкость</span>
            <span>v1 лимит конструктора: до 30 устройств</span>
            <span>LTE включён по умолчанию для всех сроков</span>
          </div>
        </section>

        <v-notice v-if="error" type="danger">{{ error }}</v-notice>
        <v-notice v-if="saveOk" type="success">Тариф сохранён. Derived pricing обновляется backend hook'ом.</v-notice>

        <div class="layout">
          <section class="cards">
            <article
              v-for="tariff in orderedTariffs"
              :key="tariff.id"
              class="tariff-card"
              :class="{ 'tariff-card--selected': selected?.id === tariff.id }"
              @click="selectTariff(tariff)"
            >
              <div class="tariff-card__top">
                <span class="status" :class="tariff.is_active ? 'status--active' : 'status--off'">
                  {{ tariff.is_active ? 'активен' : 'выкл.' }}
                </span>
                <span class="mono">#{{ tariff.order ?? '—' }}</span>
              </div>
              <h2>{{ tariff.months }} мес.</h2>
              <p>{{ tariff.name }}</p>
              <dl>
                <div><dt>от</dt><dd>{{ money(priceFor(tariff, 1)) }}</dd></div>
                <div><dt>max</dt><dd>{{ tariff.devices_limit_family || 30 }} устройств</dd></div>
                <div><dt>LTE</dt><dd>{{ tariff.lte_enabled !== false ? `${tariff.lte_price_per_gb || DEFAULT_LTE_PRICE_PER_GB} ₽/ГБ` : 'выкл.' }}</dd></div>
              </dl>
            </article>
          </section>

          <section class="editor" v-if="selected">
            <div class="panel__header">
              <div>
                <div class="panel__title">{{ selected.months }} месяцев</div>
                <div class="panel__subtitle">Операторские поля вместо raw pricing internals</div>
              </div>
              <span class="status" :class="form.is_active ? 'status--active' : 'status--off'">{{ form.is_active ? 'Активен' : 'Выключен' }}</span>
            </div>

            <div class="fields">
              <label class="field field--check"><input v-model="form.is_active" type="checkbox" /> Активен</label>
              <label class="field"><span>Порядок</span><input v-model.number="form.order" type="number" /></label>
              <label class="field"><span>Срок, мес.</span><input v-model.number="form.months" type="number" min="1" /></label>
              <label class="field"><span>Цена за 1 устройство</span><input v-model.number="form.base_price" type="number" min="1" /></label>
              <label class="field"><span>Множитель прогрессивной скидки</span><input v-model.number="form.progressive_multiplier" type="number" min="0.1" max="0.9999" step="0.01" /></label>
              <label class="field"><span>Максимум устройств</span><input v-model.number="form.devices_limit_family" type="number" min="1" max="30" /></label>
              <label class="field"><span>Бейдж/подсказка</span><input v-model="form.storefront_badge" type="text" placeholder="выгодно" /></label>
              <label class="field field--check"><input v-model="form.lte_enabled" type="checkbox" /> LTE включён</label>
              <label class="field"><span>Цена LTE за 1 ГБ</span><input v-model.number="form.lte_price_per_gb" type="number" min="0" step="0.1" /></label>
              <label class="field"><span>Мин LTE, ГБ</span><input v-model.number="form.lte_min_gb" type="number" min="0" /></label>
              <label class="field"><span>Макс LTE, ГБ</span><input v-model.number="form.lte_max_gb" type="number" min="0" /></label>
              <label class="field"><span>Шаг LTE, ГБ</span><input v-model.number="form.lte_step_gb" type="number" min="1" /></label>
              <label class="field field--wide"><span>Подсказка на витрине</span><input v-model="form.storefront_hint" type="text" /></label>
            </div>

            <div class="notice notice--policy">Семья от 2 устройств — read-only policy. Отдельной family-card больше нет.</div>
            <div v-if="localBlockingErrors.length || serverBlockingErrors.length || localWarnings.length || serverWarnings.length" class="validation">
              <div v-for="item in [...localBlockingErrors, ...serverBlockingErrors]" :key="`error-${item.field}-${item.message}`" class="validation__item validation__item--error">
                <v-icon name="error" />
                <span>{{ item.message }}</span>
              </div>
              <div v-for="item in [...localWarnings, ...serverWarnings]" :key="`warning-${item.field}-${item.message}`" class="validation__item validation__item--warning">
                <v-icon name="warning" />
                <span>{{ item.message }}</span>
              </div>
            </div>

            <div class="preview">
              <div class="preview__title">
                Предпросмотр расчёта
                <span v-if="previewLoading" class="preview__loading">backend…</span>
              </div>
              <table>
                <thead><tr><th>Устройства</th><th>Тип</th><th>Итог</th><th>Средняя</th><th>LTE +10ГБ</th></tr></thead>
                <tbody>
                  <tr v-for="row in previewRows" :key="row.deviceCount">
                    <td>{{ row.deviceCount }}</td>
                    <td><span class="badge">{{ row.tariffKind }}</span></td>
                    <td>{{ money(row.totalRub) }}</td>
                    <td>{{ money(row.avgPerDeviceRub) }}</td>
                    <td>{{ money(row.totalRub + lteExamplePrice) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <details class="advanced">
              <summary>Advanced pricing internals</summary>
              <div class="advanced__grid">
                <span>base_price: <b>{{ form.base_price }}</b></span>
                <span>progressive_multiplier: <b>{{ Number(form.progressive_multiplier || 0).toFixed(4) }}</b></span>
                <span>legacy final_price_default: <b>{{ form.final_price_default || '—' }}</b></span>
                <span>legacy final_price_family: <b>{{ form.final_price_family || '—' }}</b></span>
              </div>
            </details>
          </section>

          <section class="editor editor--empty" v-else>Выберите срок слева, чтобы открыть настройку.</section>
        </div>
      </div>
    </div>
  </private-view>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useApi } from '@directus/extensions-sdk';

const api = useApi();
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const saveOk = ref(false);
const tariffs = ref([]);
const selected = ref(null);
const form = reactive({});
const previewLoading = ref(false);
const serverPreviewRows = ref([]);
const serverWarnings = ref([]);
const serverBlockingErrors = ref([]);
const DEFAULT_LTE_PRICE_PER_GB = 1.5;

const orderedTariffs = computed(() => [...tariffs.value].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || (a.months ?? 0) - (b.months ?? 0)));
const lteExamplePrice = computed(() => Math.round((Number(form.lte_price_per_gb) || 0) * 10));

const localBlockingErrors = computed(() => {
  const errors = [];
  const basePrice = Number(form.base_price || 0);
  const devicesMax = Number(form.devices_limit_family || 0);
  const lteMin = Number(form.lte_min_gb || 0);
  const lteMax = Number(form.lte_max_gb || 0);
  const lteStep = Number(form.lte_step_gb || 0);

  if (!Number.isFinite(basePrice) || basePrice <= 0) {
    errors.push({ field: 'base_price', message: 'Цена за 1 устройство должна быть больше 0.' });
  }
  const multiplier = Number(form.progressive_multiplier || 0);
  if (!Number.isFinite(multiplier) || multiplier < 0.1 || multiplier >= 1) {
    errors.push({ field: 'progressive_multiplier', message: 'Множитель должен быть в диапазоне 0.1–0.9999. Меньше — агрессивнее скидка, ближе к 1 — почти полная цена.' });
  }
  if (!Number.isInteger(devicesMax) || devicesMax < 1) {
    errors.push({ field: 'devices_limit_family', message: 'Максимум устройств должен быть целым числом от 1 до 30.' });
  } else if (devicesMax > 30) {
    errors.push({ field: 'devices_limit_family', message: 'v1 конструктора поддерживает максимум 30 устройств.' });
  }
  if (lteMax < lteMin) {
    errors.push({ field: 'lte_max_gb', message: 'Максимум LTE не может быть меньше минимума.' });
  }
  if (!Number.isInteger(lteStep) || lteStep <= 0) {
    errors.push({ field: 'lte_step_gb', message: 'Шаг LTE должен быть целым числом больше 0.' });
  }
  return errors;
});

const localWarnings = computed(() => {
  const warnings = [];
  const multiplier = Number(form.progressive_multiplier || 0);
  const lteEnabled = Boolean(form.lte_enabled);
  const ltePrice = Number(form.lte_price_per_gb || 0);
  const devicesMax = Number(form.devices_limit_family || 0);

  if (devicesMax === 1) {
    warnings.push({ field: 'devices_limit_family', message: 'При максимуме 1 устройство семейная ёмкость фактически недоступна.' });
  }
  if (multiplier >= 0.98) {
    warnings.push({ field: 'progressive_multiplier', message: 'Скидочная кривая почти линейная: каждое следующее устройство почти по полной цене.' });
  }
  if (multiplier > 0 && multiplier <= 0.2) {
    warnings.push({ field: 'progressive_multiplier', message: 'Очень агрессивная скидочная кривая — проверьте preview перед сохранением.' });
  }
  if (lteEnabled && ltePrice <= 0) {
    warnings.push({ field: 'lte_price_per_gb', message: 'LTE включён, но цена за 1 ГБ равна 0.' });
  }
  return warnings;
});

function money(value) {
  return `${Math.round(Number(value) || 0).toLocaleString('ru-RU')} ₽`;
}

function priceFor(tariff, devices) {
  const base = Number(tariff.base_price || 0);
  const multiplier = Math.max(0.1, Math.min(0.9999, Number(tariff.progressive_multiplier || 0.9)));
  let total = base;
  for (let i = 2; i <= devices; i += 1) total += base * Math.pow(multiplier, i - 1);
  return Math.round(total);
}

function selectTariff(tariff) {
  selected.value = tariff;
  Object.assign(form, {
    id: tariff.id,
    name: tariff.name,
    months: tariff.months,
    order: tariff.order ?? 0,
    is_active: tariff.is_active !== false,
    base_price: tariff.base_price || 1,
    progressive_multiplier: tariff.progressive_multiplier || 0.9,
    devices_limit_default: 1,
    devices_limit_family: tariff.devices_limit_family || 30,
    family_plan_enabled: false,
    final_price_default: tariff.final_price_default || tariff.base_price || 1,
    final_price_family: tariff.final_price_family || null,
    lte_enabled: tariff.lte_enabled !== false,
    lte_price_per_gb: tariff.lte_price_per_gb || DEFAULT_LTE_PRICE_PER_GB,
    lte_min_gb: tariff.lte_min_gb ?? 0,
    lte_max_gb: tariff.lte_max_gb ?? 500,
    lte_step_gb: tariff.lte_step_gb ?? 1,
    storefront_badge: tariff.storefront_badge || '',
    storefront_hint: tariff.storefront_hint || '',
  });
  saveOk.value = false;
  scheduleBackendPreview();
}

const localPreviewRows = computed(() => {
  const max = Math.max(1, Math.min(30, Number(form.devices_limit_family) || 30));
  return [1, 2, 5, 10, 30]
    .filter((devices) => devices <= max)
    .map((devices) => {
      const total = priceFor(form, devices);
      return {
        deviceCount: devices,
        tariffKind: devices >= 2 ? 'family' : 'base',
        totalRub: total,
        avgPerDeviceRub: Math.round(total / devices),
      };
    });
});

const previewRows = computed(() => (serverPreviewRows.value.length > 0 ? serverPreviewRows.value : localPreviewRows.value));

function buildTariffPayload() {
  const devicesMax = Math.max(1, Math.min(30, Number(form.devices_limit_family) || 30));
  return {
    name: form.name || `${form.months} months`,
    months: Number(form.months) || selected.value?.months || 1,
    order: Number(form.order) || 0,
    is_active: Boolean(form.is_active),
    base_price: Number(form.base_price) || 1,
    price_per_device: Number(form.base_price) || 1,
    devices_limit_default: 1,
    devices_limit_family: devicesMax,
    devices_max: devicesMax,
    family_plan_enabled: false,
    final_price_default: Number(form.base_price) || 1,
    final_price_family: localPreviewRows.value.at(-1)?.totalRub || null,
    progressive_multiplier: Number(form.progressive_multiplier) || 0.9,
    lte_enabled: Boolean(form.lte_enabled),
    lte_price_per_gb: Number(form.lte_price_per_gb) || (Boolean(form.lte_enabled) ? DEFAULT_LTE_PRICE_PER_GB : 0),
    lte_min_gb: Number(form.lte_min_gb) || 0,
    lte_max_gb: Number(form.lte_max_gb) || 0,
    lte_step_gb: Number(form.lte_step_gb) || 1,
    storefront_badge: form.storefront_badge || null,
    storefront_hint: form.storefront_hint || null,
  };
}

let previewTimer = null;

async function refreshBackendPreview() {
  if (!selected.value) return;
  if (localBlockingErrors.value.length > 0) {
    serverPreviewRows.value = [];
    serverWarnings.value = [];
    serverBlockingErrors.value = [];
    return;
  }
  previewLoading.value = true;
  try {
    const { data } = await api.post('/tariff-studio/quote-preview', {
      tariff_id: selected.value.id,
      patch: buildTariffPayload(),
    });
    const payload = data || {};
    serverPreviewRows.value = Array.isArray(payload.preview) ? payload.preview : [];
    serverWarnings.value = Array.isArray(payload.warnings) ? payload.warnings : [];
    serverBlockingErrors.value = Array.isArray(payload.blockingErrors || payload.blocking_errors)
      ? (payload.blockingErrors || payload.blocking_errors)
      : [];
  } catch (err) {
    serverPreviewRows.value = [];
    serverWarnings.value = [];
    serverBlockingErrors.value = [
      {
        field: 'preview',
        message: err?.response?.data?.message || err?.message || 'Backend preview временно недоступен',
      },
    ];
  } finally {
    previewLoading.value = false;
  }
}

function scheduleBackendPreview() {
  if (previewTimer) window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => {
    void refreshBackendPreview();
  }, 280);
}

async function loadTariffs() {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await api.get('/items/tariffs', { params: { limit: -1, sort: 'order,months' } });
    tariffs.value = Array.isArray(data?.data) ? data.data : [];
    if (!selected.value && tariffs.value.length) selectTariff(orderedTariffs.value[0]);
  } catch (err) {
    error.value = err?.message || 'Не удалось загрузить тарифы';
  } finally {
    loading.value = false;
  }
}

async function saveSelected() {
  if (!selected.value) return;
  if (localBlockingErrors.value.length > 0 || serverBlockingErrors.value.length > 0) {
    error.value = [...localBlockingErrors.value, ...serverBlockingErrors.value].map((item) => item.message).join(' ');
    return;
  }
  saving.value = true;
  error.value = '';
  saveOk.value = false;
  try {
    const payload = buildTariffPayload();
    await api.patch(`/items/tariffs/${encodeURIComponent(String(selected.value.id))}`, payload);
    saveOk.value = true;
    await loadTariffs();
    const fresh = tariffs.value.find((item) => String(item.id) === String(selected.value?.id));
    if (fresh) selectTariff(fresh);
  } catch (err) {
    error.value = err?.message || 'Не удалось сохранить тариф';
  } finally {
    saving.value = false;
  }
}

onMounted(loadTariffs);

watch(
  () => [
    form.base_price,
    form.progressive_multiplier,
    form.devices_limit_family,
    form.lte_enabled,
    form.lte_price_per_gb,
    form.lte_min_gb,
    form.lte_max_gb,
    form.lte_step_gb,
  ],
  scheduleBackendPreview
);
</script>

<style scoped>
:deep(.private-view) { width: 100%; }
:deep(.private-view__main),
:deep(.private-view__content),
:deep(.private-view__content > *) {
  max-width: none !important;
  width: 100% !important;
}
:deep(.private-view__content) {
  justify-content: stretch !important;
  justify-items: stretch !important;
  align-items: stretch !important;
}

.page {
  padding: 16px 20px;
  width: 100%;
  min-width: 0;
  min-height: 100%;
  display: grid;
  background:
    radial-gradient(circle at 2% -10%, rgba(34, 211, 238, 0.22), transparent 36%),
    radial-gradient(circle at 105% 2%, rgba(59, 130, 246, 0.18), transparent 40%),
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

.nav { padding: 10px; }
.nav__section { margin-bottom: 12px; }
.nav__section-title {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.6;
  margin: 8px 8px 6px;
}
.nav__item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 10px;
  text-decoration: none;
  color: inherit;
}
.nav__item:hover { background: rgba(255, 255, 255, 0.05); }
.nav__item--active { background: rgba(34, 211, 238, 0.14); }
.nav__item-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(34, 211, 238, 0.18);
}
.nav__item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.16), rgba(59, 130, 246, 0.10));
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
  background: linear-gradient(120deg, rgba(125, 211, 252, 0.95), rgba(165, 180, 252, 0.95));
}

.nav__brand-title { font-size: 13px; font-weight: 700; }
.nav__brand-subtitle { font-size: 11px; opacity: 0.7; }

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  gap: 14px;
  padding: 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(140deg, rgba(34, 211, 238, 0.14), rgba(59, 130, 246, 0.10));
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
  background: rgba(34, 211, 238, 0.2);
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

.hero__chips span {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
}

.hero__policy {
  display: grid;
  gap: 6px;
  align-content: start;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(2, 8, 23, 0.4);
  font-size: 12px;
}

.hero__policy strong {
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.85;
}

.hero__policy span { opacity: 0.84; }

.layout {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.cards {
  display: grid;
  gap: 10px;
  align-content: start;
}

.tariff-card {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.tariff-card:hover {
  transform: translateY(-1px);
  border-color: rgba(34, 211, 238, 0.45);
  background: rgba(255, 255, 255, 0.05);
}

.tariff-card--selected {
  border-color: rgba(34, 211, 238, 0.7);
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.16), rgba(255, 255, 255, 0.04));
  box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.45);
}

.tariff-card__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.tariff-card h2 {
  margin: 4px 0 0;
  font-size: 22px;
  letter-spacing: 0.01em;
}

.tariff-card p {
  margin: 2px 0 6px;
  opacity: 0.78;
  font-size: 13px;
}

dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}

dt {
  font-size: 11px;
  opacity: 0.7;
}

dd {
  margin: 2px 0 0;
  font-weight: 700;
  font-size: 13px;
}

.mono {
  font-family: "JetBrains Mono", "Fira Mono", ui-monospace, monospace;
  font-size: 11px;
  opacity: 0.65;
}

.status {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 11px;
  border: 1px solid transparent;
  font-weight: 700;
}

.status--active {
  background: rgba(16, 185, 129, 0.16);
  color: #7bf1ca;
  border-color: rgba(16, 185, 129, 0.45);
}

.status--off {
  background: rgba(245, 158, 11, 0.16);
  color: #fdd280;
  border-color: rgba(245, 158, 11, 0.4);
}

.editor {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  min-height: 320px;
}

.editor--empty {
  place-items: center;
  text-align: center;
  font-size: 14px;
  opacity: 0.74;
  padding: 36px;
}

.panel__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.panel__title {
  font-weight: 800;
  font-size: 19px;
}

.panel__subtitle {
  margin-top: 3px;
  font-size: 12px;
  opacity: 0.74;
}

.fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.field {
  display: grid;
  gap: 6px;
  font-size: 12px;
}

.field--wide { grid-column: 1 / -1; }

.field--check {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}

input {
  min-height: 38px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: inherit;
  padding: 9px 10px;
  font: inherit;
}

input:focus-visible {
  outline: 2px solid rgba(34, 211, 238, 0.45);
  outline-offset: 1px;
}

.notice--policy {
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12px;
  color: #67e8f9;
  border: 1px solid rgba(34, 211, 238, 0.32);
  background: rgba(34, 211, 238, 0.08);
}

.validation {
  display: grid;
  gap: 8px;
}

.validation__item {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 8px;
  align-items: start;
  border-radius: 10px;
  padding: 9px 10px;
  font-size: 13px;
  line-height: 18px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(2, 8, 23, 0.4);
}

.validation__item :deep(.v-icon) { --v-icon-size: 18px; }

.validation__item--error {
  color: #ffb4b4;
  border-color: rgba(239, 68, 68, 0.46);
  background: rgba(239, 68, 68, 0.10);
}

.validation__item--warning {
  color: #fde68a;
  border-color: rgba(245, 158, 11, 0.42);
  background: rgba(245, 158, 11, 0.12);
}

.preview {
  display: grid;
  gap: 8px;
  overflow-x: auto;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(2, 8, 23, 0.34);
}

.preview__title {
  font-weight: 800;
  font-size: 14px;
  letter-spacing: 0.02em;
}

.preview__loading {
  margin-left: 8px;
  color: #67e8f9;
  font-size: 12px;
  font-weight: 700;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th, td {
  text-align: left;
  padding: 9px 7px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  white-space: nowrap;
}

th {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.7;
}

tbody tr:hover {
  background: rgba(34, 211, 238, 0.07);
}

.badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  border: 1px solid rgba(34, 211, 238, 0.4);
  background: rgba(34, 211, 238, 0.14);
  color: #67e8f9;
  font-weight: 700;
}

.advanced {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 10px;
  font-size: 12px;
}

.advanced summary {
  cursor: pointer;
  opacity: 0.78;
}

.advanced__grid {
  margin-top: 8px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  opacity: 0.84;
}

@media (max-width: 1280px) {
  .layout { grid-template-columns: 1fr; }
  .fields { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 980px) {
  .hero { grid-template-columns: 1fr; }
  .fields { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 640px) {
  .page { padding: 12px; }
  .fields { grid-template-columns: 1fr; }
  dl { grid-template-columns: 1fr; }
}

@media (max-width: 720px) {
  .nav--premium {
    display: flex;
    overflow-x: auto;
    gap: 10px;
    padding-bottom: 4px;
  }
  .nav--premium .nav__brand,
  .nav--premium .nav__section { min-width: 240px; }
}

@media (max-width: 480px) {
  .page { padding: 8px; gap: 8px; overflow-x: hidden; }
  .page__main { gap: 10px; }
  .hero { padding: 12px; gap: 10px; }
  .hero__title { font-size: 18px; line-height: 1.25; }
  .hero__subtitle { font-size: 13px; }
  .editor, .tariff-card { padding: 10px; }
  .tariff-card h2 { font-size: 18px; }
  .panel__title { font-size: 16px; }
  .fields { gap: 8px; }
  input { min-height: 44px; font-size: 16px; }
  .advanced__grid { grid-template-columns: 1fr; }
  .preview { font-size: 12px; }
  table { font-size: 12px; }
  th, td { padding: 6px 4px; }
  :deep(.v-button) { min-height: 44px; }
  .nav--premium { flex-direction: column; overflow: visible; }
  .nav--premium .nav__brand,
  .nav--premium .nav__section { min-width: 0; width: 100%; }
}

@media (max-width: 360px) {
  .page { padding: 6px; }
  .hero__title { font-size: 16px; }
}
</style>
