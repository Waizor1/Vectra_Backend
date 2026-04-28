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
            <span class="nav__item-label">Студия</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/content/tariffs' }">
            <span class="nav__item-icon"><v-icon name="table_view" /></span>
            <span class="nav__item-label">Raw тарифы</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
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
:deep(.private-view__main), :deep(.private-view__content), :deep(.private-view__content > *) { max-width: none !important; width: 100% !important; }
.page { min-height: 100%; padding: 16px 20px; color: #c9d1d9; font-family: Montserrat, Inter, sans-serif; background: radial-gradient(circle at 5% -10%, rgba(59,201,219,.18), transparent 32%), linear-gradient(180deg, #0d1117, #0b0f14); }
.page__main { max-width: 1480px; margin: 0 auto; display: grid; gap: 14px; }
.nav { padding: 10px; color: #c9d1d9; }
.nav__brand { display: grid; grid-template-columns: 36px 1fr; gap: 10px; align-items: center; padding: 10px; border-radius: 8px; border: 1px solid #484f58; background: linear-gradient(135deg,#21262d,#161b22); }
.nav__brand-logo { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; background: linear-gradient(135deg,#3bc9db,#0c8599); color: #fff; font-weight: 800; font-size: 11px; }
.nav__brand-title { font-weight: 700; }
.nav__brand-subtitle, .nav__section-title { color: #8b949e; font-size: 12px; }
.nav__section { margin-top: 12px; display: grid; gap: 6px; }
.nav__item { display: grid; grid-template-columns: 28px 1fr; gap: 8px; align-items: center; padding: 8px 10px; border-radius: 8px; color: inherit; text-decoration: none; }
.nav__item--active, .nav__item:hover { background: rgba(59,201,219,.12); }
.hero, .editor, .tariff-card { border: 1px solid #484f58; border-radius: 8px; background: linear-gradient(135deg,#21262d,#161b22); box-shadow: none; }
.hero { display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 14px; padding: 16px; }
.hero__kicker { color: #66d9e8; font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .08em; }
.hero__title { margin: 6px 0 0; color: #fff; font-size: 28px; line-height: 1.1; }
.hero__subtitle { color: #8b949e; max-width: 760px; line-height: 1.45; }
.hero__chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.hero__chips span, .badge, .status { border-radius: 999px; padding: 4px 9px; border: 1px solid rgba(59,201,219,.24); background: rgba(59,201,219,.09); color: #66d9e8; font-size: 12px; font-weight: 700; }
.hero__policy { display: grid; gap: 7px; align-content: start; padding: 12px; border-radius: 8px; background: rgba(13,17,23,.45); border: 1px solid rgba(72,79,88,.9); }
.layout { display: grid; grid-template-columns: minmax(260px, 420px) minmax(0, 1fr); gap: 14px; align-items: start; }
.cards { display: grid; gap: 10px; }
.tariff-card { padding: 14px; cursor: pointer; transition: border-color .18s ease, transform .18s ease; }
.tariff-card:hover, .tariff-card--selected { border-color: #3bc9db; transform: translateY(-1px); }
.tariff-card__top { display: flex; justify-content: space-between; align-items: center; }
.tariff-card h2 { margin: 10px 0 0; color: #fff; font-size: 24px; }
.tariff-card p { color: #8b949e; margin: 4px 0 12px; }
dl { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; margin: 0; }
dt { color: #8b949e; font-size: 11px; }
dd { margin: 2px 0 0; color: #fff; font-weight: 700; }
.status--active { color: #7bf1ca; border-color: rgba(16,185,129,.42); background: rgba(16,185,129,.12); }
.status--off { color: #fdd280; border-color: rgba(245,158,11,.4); background: rgba(245,158,11,.12); }
.mono { font-family: 'Fira Mono', ui-monospace, monospace; color: #8b949e; }
.editor { padding: 16px; display: grid; gap: 14px; }
.editor--empty { min-height: 220px; place-items: center; color: #8b949e; }
.panel__header { display: flex; justify-content: space-between; gap: 10px; }
.panel__title { color: #fff; font-size: 20px; font-weight: 800; }
.panel__subtitle { color: #8b949e; font-size: 13px; }
.fields { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 10px; }
.field { display: grid; gap: 6px; color: #8b949e; font-size: 12px; }
.field--wide { grid-column: 1 / -1; }
.field--check { display: flex; gap: 8px; align-items: center; color: #c9d1d9; }
input { min-height: 38px; border-radius: 8px; border: 1px solid #484f58; background: #0d1117; color: #c9d1d9; padding: 8px 10px; }
input:focus-visible { outline: 2px solid rgba(59,201,219,.42); border-color: #3bc9db; }
.notice--policy { border-radius: 8px; padding: 10px 12px; color: #66d9e8; border: 1px solid rgba(59,201,219,.22); background: rgba(59,201,219,.07); }
.validation { display: grid; gap: 8px; }
.validation__item { display: grid; grid-template-columns: 18px 1fr; gap: 8px; align-items: start; border-radius: 8px; padding: 9px 10px; font-size: 13px; line-height: 18px; border: 1px solid rgba(72,79,88,.85); background: rgba(13,17,23,.42); }
.validation__item :deep(.v-icon) { --v-icon-size: 18px; }
.validation__item--error { color: #ffb4b4; border-color: rgba(224,49,49,.46); background: rgba(224,49,49,.10); }
.validation__item--warning { color: #ffd43b; border-color: rgba(245,158,11,.42); background: rgba(245,158,11,.10); }
.preview { display: grid; gap: 8px; overflow: auto; }
.preview__title { color: #fff; font-weight: 800; }
.preview__loading { margin-left: 8px; color: #66d9e8; font-size: 12px; font-weight: 700; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 9px 7px; border-bottom: 1px solid rgba(72,79,88,.8); white-space: nowrap; }
th { color: #8b949e; font-size: 11px; text-transform: uppercase; }
.advanced { border-top: 1px solid rgba(72,79,88,.8); padding-top: 10px; }
.advanced summary { cursor: pointer; color: #8b949e; }
.advanced__grid { margin-top: 8px; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; color: #8b949e; }
@media (max-width: 980px) { .hero, .layout { grid-template-columns: 1fr; } .fields { grid-template-columns: 1fr 1fr; } }
@media (max-width: 640px) { .page { padding: 12px; } .fields { grid-template-columns: 1fr; } dl { grid-template-columns: 1fr; } }
</style>
