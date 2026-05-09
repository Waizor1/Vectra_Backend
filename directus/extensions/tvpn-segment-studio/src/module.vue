<template>
  <private-view title="Segment Studio">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">SEG</div>
          <div>
            <div class="nav__brand-title">Segment Studio</div>
            <div class="nav__brand-subtitle">Сегментные акции и таймеры</div>
          </div>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-segment-studio' }">
            <span class="nav__item-icon"><v-icon name="campaign" /></span>
            <span class="nav__item-label">Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Промокоды</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-tariff-studio' }">
            <span class="nav__item-icon"><v-icon name="tune" /></span>
            <span class="nav__item-label">Тарифы</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/content/segment_campaigns' }">
            <span class="nav__item-icon"><v-icon name="table_view" /></span>
            <span class="nav__item-label">Raw кампании</span>
          </router-link>
        </div>
      </div>
    </template>

    <template #actions>
      <v-button secondary :loading="loading" @click="loadCampaigns">
        <v-icon name="refresh" left />
        Обновить
      </v-button>
      <v-button kind="primary" @click="startNewCampaign">
        <v-icon name="add" left />
        Новая акция
      </v-button>
    </template>

    <div class="page">
      <div class="page__main">
        <section class="hero">
          <div class="hero__left">
            <div class="hero__kicker">Vectra Connect</div>
            <h1 class="hero__title">Сегментные акции</h1>
            <p class="hero__subtitle">
              Скидочные кампании по сегментам аудитории с обратным таймером в разделе «Подписка».
              Здесь можно собрать акцию, увидеть, как баннер выглядит у пользователя, и держать всё под контролем без правки сырой коллекции.
            </p>
            <div class="hero__chips">
              <span class="hero__chip"><v-icon name="bolt" small /> Сейчас активно: {{ liveCount }}</span>
              <span class="hero__chip"><v-icon name="schedule" small /> Запланировано: {{ scheduledCount }}</span>
              <span class="hero__chip"><v-icon name="history_toggle_off" small /> Завершено: {{ expiredCount }}</span>
            </div>
          </div>

          <div class="hero__right">
            <label class="field field--compact">
              <span>Фильтр сегмента</span>
              <select v-model="filters.segment" class="input input--select">
                <option value="">Все сегменты</option>
                <option v-for="opt in segmentOptions" :key="opt.value" :value="opt.value">
                  {{ opt.text }}
                </option>
              </select>
            </label>
            <label class="field field--compact">
              <span>Статус</span>
              <select v-model="filters.status" class="input input--select">
                <option value="all">Все</option>
                <option value="live">Активные сейчас</option>
                <option value="scheduled">Запланированные</option>
                <option value="expired">Завершённые</option>
                <option value="disabled">Выключенные</option>
              </select>
            </label>
          </div>
        </section>

        <v-notice v-if="globalError" type="danger">{{ globalError }}</v-notice>

        <div class="layout">
          <section class="cards">
            <div v-if="loading && !campaigns.length" class="empty">Загрузка кампаний…</div>
            <div v-else-if="!visibleCampaigns.length" class="empty">
              Под фильтр ничего не попало. Сбрось фильтры или нажми «Новая акция».
            </div>
            <article
              v-for="campaign in visibleCampaigns"
              :key="campaign.id"
              class="campaign-card"
              :class="[`accent--${accentKey(campaign.accent)}`, {
                'campaign-card--selected': isSelected(campaign),
                'campaign-card--inactive': !campaign.is_active,
              }]"
              @click="selectCampaign(campaign)"
            >
              <div class="campaign-card__top">
                <span class="status" :class="`status--${campaignStatus(campaign).key}`">
                  {{ campaignStatus(campaign).label }}
                </span>
                <span class="campaign-card__priority" :title="`Приоритет ${campaign.priority || 0}`">
                  P{{ campaign.priority || 0 }}
                </span>
              </div>
              <div class="campaign-card__title">{{ campaign.title || campaign.slug || `#${campaign.id}` }}</div>
              <div class="campaign-card__slug">{{ campaign.slug || '—' }}</div>
              <div class="campaign-card__meta">
                <span class="badge badge--segment">{{ segmentLabel(campaign.segment) }}</span>
                <span class="badge badge--discount">−{{ campaign.discount_percent || 0 }}%</span>
                <span class="badge badge--months">{{ monthsLabelShort(campaign.applies_to_months) }}</span>
              </div>
              <div class="campaign-card__time">
                <span><v-icon name="event_available" small /> {{ fmtDate(campaign.starts_at) }}</span>
                <span><v-icon name="event_busy" small /> {{ fmtDate(campaign.ends_at) }}</span>
              </div>
              <div v-if="campaignStatus(campaign).key === 'live'" class="campaign-card__countdown">
                До конца: {{ countdownLabel(campaign.ends_at) }}
              </div>
            </article>
          </section>

          <section v-if="selected || draft" class="editor">
            <div class="editor__header">
              <div>
                <div class="editor__title">
                  {{ isDraft ? 'Новая акция' : (form.title || form.slug || `Акция #${form.id}`) }}
                </div>
                <div class="editor__subtitle">
                  {{ isDraft ? 'Заполни поля и сохрани, чтобы создать кампанию.' : 'Меняй параметры — превью обновляется на лету.' }}
                </div>
              </div>
              <div class="editor__header-actions">
                <span class="status" :class="`status--${editorStatus.key}`">{{ editorStatus.label }}</span>
              </div>
            </div>

            <div class="preview-strip">
              <div class="preview-strip__label">Превью на витрине</div>
              <div class="banner" :class="`accent--${accentKey(form.accent)}`">
                <div class="banner__head">
                  <span class="banner__discount">−{{ form.discount_percent || 0 }}%</span>
                  <span class="banner__segment">{{ segmentLabel(form.segment) }}</span>
                </div>
                <div class="banner__title">{{ form.title || 'Заголовок акции' }}</div>
                <div v-if="form.subtitle" class="banner__subtitle">{{ form.subtitle }}</div>
                <div class="banner__bottom">
                  <div class="banner__timer">
                    <v-icon name="timer" small />
                    <span>{{ countdownLabel(form.ends_at) || 'Без таймера' }}</span>
                  </div>
                  <div class="banner__cta">
                    <v-icon name="arrow_forward" small />
                    <span>{{ form.cta_label || ctaLabelFallback(form.cta_target) }}</span>
                  </div>
                </div>
                <div class="banner__chips">
                  <span v-for="m in monthsToShow" :key="`m-${m}`" class="banner__chip">{{ m }} мес</span>
                  <span v-if="!monthsToShow.length" class="banner__chip banner__chip--all">все длительности</span>
                </div>
              </div>
            </div>

            <div class="flow">
              <div class="flow__step">
                <div class="flow__step-title">1. Базовые поля</div>
                <div class="fields">
                  <label class="field">
                    <span>Slug (машинный) *</span>
                    <input v-model.trim="form.slug" class="input" placeholder="mayday-30off" maxlength="80" />
                  </label>
                  <label class="field">
                    <span>Заголовок на витрине *</span>
                    <input v-model.trim="form.title" class="input" placeholder="Майские: −30% на 6 и 12 месяцев" maxlength="120" />
                  </label>
                  <label class="field field--wide">
                    <span>Подзаголовок</span>
                    <input v-model.trim="form.subtitle" class="input" placeholder="Скидка действует только до пятницы" maxlength="180" />
                  </label>
                  <label class="field field--wide field--textarea">
                    <span>Описание (модалка/тултип)</span>
                    <textarea v-model.trim="form.description" class="input input--textarea" rows="3" placeholder="Длинное описание для тех, кто кликнул на баннер." />
                  </label>
                </div>
              </div>

              <div class="flow__step">
                <div class="flow__step-title">2. Сегмент и применимость</div>
                <div class="field">
                  <span>Целевой сегмент *</span>
                  <div class="segmented segmented--vertical">
                    <button
                      v-for="opt in segmentOptions"
                      :key="opt.value"
                      type="button"
                      class="segmented__btn segmented__btn--block"
                      :class="{ 'segmented__btn--active': form.segment === opt.value }"
                      @click="form.segment = opt.value"
                    >
                      <span class="segmented__btn-title">{{ opt.text }}</span>
                      <span class="segmented__btn-hint">{{ opt.hint }}</span>
                    </button>
                  </div>
                </div>

                <div class="fields">
                  <div class="field">
                    <span>Скидка, % *</span>
                    <div class="slider-row">
                      <input v-model.number="form.discount_percent" type="range" min="1" max="90" step="1" class="slider" />
                      <input v-model.number="form.discount_percent" type="number" min="1" max="90" class="input input--small" />
                    </div>
                  </div>
                  <div class="field">
                    <span>Приоритет</span>
                    <input v-model.number="form.priority" type="number" min="0" step="1" class="input" />
                  </div>
                </div>

                <div class="field">
                  <span>Длительности тарифов, к которым применяется</span>
                  <div class="chips">
                    <button
                      v-for="opt in monthsOptions"
                      :key="`m-toggle-${opt.value}`"
                      type="button"
                      class="chip"
                      :class="{ 'chip--active': monthsSet.has(opt.value) }"
                      @click="toggleMonths(opt.value)"
                    >
                      {{ opt.text }}
                    </button>
                    <button
                      type="button"
                      class="chip chip--ghost"
                      :class="{ 'chip--active': !monthsSet.size }"
                      @click="form.applies_to_months = []"
                    >
                      Все
                    </button>
                  </div>
                  <div class="hint">Пустой список = на все длительности.</div>
                </div>
              </div>

              <div class="flow__step">
                <div class="flow__step-title">3. Внешний вид и CTA</div>
                <div class="field">
                  <span>Акцентная палитра</span>
                  <div class="swatches">
                    <button
                      v-for="opt in accentOptions"
                      :key="opt.value"
                      type="button"
                      class="swatch"
                      :class="[`swatch--${opt.value}`, { 'swatch--active': form.accent === opt.value }]"
                      :title="opt.text"
                      @click="form.accent = opt.value"
                    >
                      <span>{{ opt.text }}</span>
                    </button>
                  </div>
                </div>

                <div class="fields">
                  <label class="field">
                    <span>Подпись кнопки CTA</span>
                    <input v-model.trim="form.cta_label" class="input" placeholder="Подключить выгодно" maxlength="80" />
                  </label>
                  <div class="field">
                    <span>Куда ведёт CTA</span>
                    <select v-model="form.cta_target" class="input input--select">
                      <option v-for="opt in ctaOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
                    </select>
                  </div>
                </div>
              </div>

              <div class="flow__step">
                <div class="flow__step-title">4. Расписание и активность</div>
                <div class="fields">
                  <label class="field">
                    <span>Старт *</span>
                    <input v-model="form.starts_at_input" type="datetime-local" class="input" />
                  </label>
                  <label class="field">
                    <span>Конец (таймер) *</span>
                    <input v-model="form.ends_at_input" type="datetime-local" class="input" />
                  </label>
                </div>
                <div class="schedule-helpers">
                  <button type="button" class="chip" @click="setQuickDuration(24)">+24 часа</button>
                  <button type="button" class="chip" @click="setQuickDuration(72)">+3 дня</button>
                  <button type="button" class="chip" @click="setQuickDuration(168)">+7 дней</button>
                  <button type="button" class="chip" @click="setQuickDuration(336)">+14 дней</button>
                  <button type="button" class="chip" @click="setQuickDuration(720)">+30 дней</button>
                </div>
                <label class="field field--check">
                  <input v-model="form.is_active" type="checkbox" />
                  <span>Кампания активна (можно использовать как глобальный выключатель)</span>
                </label>
              </div>
            </div>

            <div v-if="validationErrors.length" class="validation">
              <div v-for="msg in validationErrors" :key="msg" class="validation__item validation__item--error">
                <v-icon name="error" />
                <span>{{ msg }}</span>
              </div>
            </div>
            <div v-if="saveOk" class="notice notice--ok">Сохранено. Изменения применятся на витрине после кэша (до 60 сек).</div>
            <div v-if="saveError" class="notice notice--error">{{ saveError }}</div>

            <div class="editor__actions">
              <v-button kind="primary" :loading="saving" :disabled="validationErrors.length > 0" @click="saveCampaign">
                <v-icon :name="isDraft ? 'add_circle' : 'save'" left />
                {{ isDraft ? 'Создать кампанию' : 'Сохранить' }}
              </v-button>
              <v-button v-if="!isDraft" secondary :loading="duplicating" @click="duplicateCampaign">
                <v-icon name="content_copy" left />
                Дублировать
              </v-button>
              <v-button v-if="!isDraft" secondary :loading="togglingActive" @click="toggleIsActive">
                <v-icon :name="form.is_active ? 'pause_circle' : 'play_circle'" left />
                {{ form.is_active ? 'Выключить' : 'Включить' }}
              </v-button>
              <v-button v-if="!isDraft" kind="warning" :loading="deleting" @click="deleteCampaign">
                <v-icon name="delete_forever" left />
                Удалить
              </v-button>
            </div>
          </section>

          <section v-else class="editor editor--empty">
            Выбери кампанию слева или нажми «Новая акция», чтобы начать.
          </section>
        </div>
      </div>
    </div>
  </private-view>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const api = useApi();

const SEGMENT_OPTIONS = [
  { value: "no_purchase_yet", text: "Ещё не платил", hint: "Первая покупка — пользователь без succeeded-платежей." },
  { value: "trial_active", text: "На триале", hint: "Триал активен, ещё не конвертировался." },
  { value: "lapsed", text: "Подписка истекла", hint: "Активной подписки нет более 7 дней." },
  { value: "loyal_renewer", text: "Постоянный клиент", hint: "≥2 платежа или ≥180 дней с первой покупки." },
  { value: "everyone", text: "Все пользователи", hint: "Без сегментации — показать всем подходящим." },
];

const ACCENT_OPTIONS = [
  { value: "gold", text: "Золото" },
  { value: "cyan", text: "Циан" },
  { value: "violet", text: "Фиолет" },
  { value: "blue", text: "Синий" },
  { value: "green", text: "Зелёный" },
];

const CTA_OPTIONS = [
  { value: "builder", text: "Открыть калькулятор" },
  { value: "tariff_1m", text: "Тариф 1 месяц" },
  { value: "tariff_3m", text: "Тариф 3 месяца" },
  { value: "tariff_6m", text: "Тариф 6 месяцев" },
  { value: "tariff_12m", text: "Тариф 12 месяцев" },
  { value: "family", text: "Семейный тариф" },
];

const MONTHS_OPTIONS = [
  { value: 1, text: "1 мес" },
  { value: 3, text: "3 мес" },
  { value: 6, text: "6 мес" },
  { value: 12, text: "12 мес" },
];

const segmentOptions = SEGMENT_OPTIONS;
const accentOptions = ACCENT_OPTIONS;
const ctaOptions = CTA_OPTIONS;
const monthsOptions = MONTHS_OPTIONS;

const loading = ref(false);
const saving = ref(false);
const duplicating = ref(false);
const togglingActive = ref(false);
const deleting = ref(false);
const globalError = ref("");
const saveError = ref("");
const saveOk = ref(false);

const campaigns = ref([]);
const selected = ref(null);
const draft = ref(false);
const now = ref(Date.now());

const filters = ref({
  segment: "",
  status: "all",
});

const form = reactive(emptyForm());

let nowTimer = null;

function emptyForm() {
  const start = new Date();
  const end = new Date(start.getTime() + 7 * 24 * 60 * 60 * 1000);
  return {
    id: null,
    slug: "",
    title: "",
    subtitle: "",
    description: "",
    segment: "everyone",
    discount_percent: 20,
    applies_to_months: [],
    accent: "gold",
    cta_label: "",
    cta_target: "builder",
    starts_at: toIso(start),
    ends_at: toIso(end),
    starts_at_input: toLocalInput(start),
    ends_at_input: toLocalInput(end),
    priority: 0,
    is_active: true,
  };
}

function toIso(value) {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function toLocalInput(value) {
  if (!value) return "";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

const monthsSet = computed(() => new Set(Array.isArray(form.applies_to_months) ? form.applies_to_months.map((v) => Number(v)) : []));

const monthsToShow = computed(() => {
  const list = Array.isArray(form.applies_to_months) ? form.applies_to_months.map((v) => Number(v)) : [];
  return list.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
});

const isDraft = computed(() => draft.value || form.id === null);

const isSelected = (campaign) => !isDraft.value && campaign.id === form.id;

const visibleCampaigns = computed(() => {
  return campaigns.value.filter((row) => {
    if (filters.value.segment && row.segment !== filters.value.segment) return false;
    const status = campaignStatus(row).key;
    if (filters.value.status === "live" && status !== "live") return false;
    if (filters.value.status === "scheduled" && status !== "scheduled") return false;
    if (filters.value.status === "expired" && status !== "expired") return false;
    if (filters.value.status === "disabled" && status !== "disabled") return false;
    return true;
  });
});

const liveCount = computed(() => campaigns.value.filter((r) => campaignStatus(r).key === "live").length);
const scheduledCount = computed(() => campaigns.value.filter((r) => campaignStatus(r).key === "scheduled").length);
const expiredCount = computed(() => campaigns.value.filter((r) => campaignStatus(r).key === "expired").length);

const editorStatus = computed(() => {
  if (isDraft.value) return { key: "draft", label: "Черновик" };
  return campaignStatus({
    is_active: form.is_active,
    starts_at: form.starts_at,
    ends_at: form.ends_at,
  });
});

const validationErrors = computed(() => {
  const errors = [];
  const slug = String(form.slug || "").trim();
  if (!slug) errors.push("Slug обязателен.");
  else if (!/^[a-z0-9_-]{2,80}$/i.test(slug)) errors.push("Slug должен быть латиницей с цифрами, _ и -, 2..80 символов.");

  if (!String(form.title || "").trim()) errors.push("Заголовок обязателен.");
  if (!form.segment) errors.push("Выбери целевой сегмент.");

  const discount = Number(form.discount_percent);
  if (!Number.isFinite(discount) || discount < 1 || discount > 90) {
    errors.push("Скидка должна быть от 1 до 90%.");
  }

  const startsIso = fromLocalInput(form.starts_at_input);
  const endsIso = fromLocalInput(form.ends_at_input);
  if (!startsIso) errors.push("Укажи дату старта.");
  if (!endsIso) errors.push("Укажи дату окончания.");
  if (startsIso && endsIso && new Date(endsIso).getTime() <= new Date(startsIso).getTime()) {
    errors.push("Конец должен быть позже старта.");
  }

  if (Array.isArray(form.applies_to_months)) {
    const bad = form.applies_to_months.filter((v) => ![1, 3, 6, 12].includes(Number(v)));
    if (bad.length) errors.push(`Некорректные длительности: ${bad.join(", ")}. Разрешены 1, 3, 6, 12.`);
  }
  return errors;
});

watch(() => form.starts_at_input, (val) => {
  form.starts_at = fromLocalInput(val);
});
watch(() => form.ends_at_input, (val) => {
  form.ends_at = fromLocalInput(val);
});

function accentKey(value) {
  return ACCENT_OPTIONS.some((opt) => opt.value === value) ? value : "gold";
}

function segmentLabel(value) {
  return SEGMENT_OPTIONS.find((opt) => opt.value === value)?.text || value || "—";
}

function ctaLabelFallback(target) {
  const opt = CTA_OPTIONS.find((o) => o.value === target);
  return opt ? opt.text : "Подробнее";
}

function monthsLabelShort(value) {
  if (!Array.isArray(value) || !value.length) return "Все длительности";
  const sorted = [...value].map((v) => Number(v)).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  return sorted.map((m) => `${m}m`).join(" / ");
}

function fmtDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function campaignStatus(campaign) {
  if (!campaign) return { key: "unknown", label: "—" };
  if (!campaign.is_active) return { key: "disabled", label: "Выключена" };
  const startMs = campaign.starts_at ? new Date(campaign.starts_at).getTime() : null;
  const endMs = campaign.ends_at ? new Date(campaign.ends_at).getTime() : null;
  if (endMs && endMs <= now.value) return { key: "expired", label: "Завершилась" };
  if (startMs && startMs > now.value) return { key: "scheduled", label: "Запланирована" };
  return { key: "live", label: "Идёт сейчас" };
}

function countdownLabel(value) {
  if (!value) return "";
  const target = new Date(value).getTime();
  if (!Number.isFinite(target)) return "";
  const diff = target - now.value;
  if (diff <= 0) return "истекла";
  const days = Math.floor(diff / 86_400_000);
  const hours = Math.floor((diff % 86_400_000) / 3_600_000);
  const mins = Math.floor((diff % 3_600_000) / 60_000);
  if (days > 0) return `${days}д ${hours}ч`;
  if (hours > 0) return `${hours}ч ${mins}м`;
  return `${mins}м`;
}

function toggleMonths(value) {
  const num = Number(value);
  const list = Array.isArray(form.applies_to_months) ? [...form.applies_to_months].map((v) => Number(v)) : [];
  const idx = list.indexOf(num);
  if (idx >= 0) list.splice(idx, 1);
  else list.push(num);
  list.sort((a, b) => a - b);
  form.applies_to_months = list;
}

function setQuickDuration(hours) {
  const start = fromLocalInput(form.starts_at_input);
  const startDate = start ? new Date(start) : new Date();
  const endDate = new Date(startDate.getTime() + hours * 60 * 60 * 1000);
  form.starts_at_input = toLocalInput(startDate);
  form.ends_at_input = toLocalInput(endDate);
  form.starts_at = startDate.toISOString();
  form.ends_at = endDate.toISOString();
}

function startNewCampaign() {
  selected.value = null;
  draft.value = true;
  Object.assign(form, emptyForm());
  saveOk.value = false;
  saveError.value = "";
}

function selectCampaign(campaign) {
  draft.value = false;
  selected.value = campaign;
  Object.assign(form, {
    id: campaign.id,
    slug: campaign.slug || "",
    title: campaign.title || "",
    subtitle: campaign.subtitle || "",
    description: campaign.description || "",
    segment: campaign.segment || "everyone",
    discount_percent: Number(campaign.discount_percent) || 0,
    applies_to_months: Array.isArray(campaign.applies_to_months)
      ? [...campaign.applies_to_months].map((v) => Number(v))
      : [],
    accent: campaign.accent || "gold",
    cta_label: campaign.cta_label || "",
    cta_target: campaign.cta_target || "builder",
    starts_at: campaign.starts_at,
    ends_at: campaign.ends_at,
    starts_at_input: toLocalInput(campaign.starts_at),
    ends_at_input: toLocalInput(campaign.ends_at),
    priority: Number(campaign.priority) || 0,
    is_active: campaign.is_active !== false,
  });
  saveOk.value = false;
  saveError.value = "";
}

function buildPayload() {
  const startsIso = fromLocalInput(form.starts_at_input);
  const endsIso = fromLocalInput(form.ends_at_input);
  return {
    slug: String(form.slug || "").trim(),
    title: String(form.title || "").trim(),
    subtitle: form.subtitle ? String(form.subtitle).trim() : null,
    description: form.description ? String(form.description).trim() : null,
    segment: form.segment,
    discount_percent: Math.max(1, Math.min(90, Math.round(Number(form.discount_percent) || 0))),
    applies_to_months: Array.isArray(form.applies_to_months)
      ? form.applies_to_months.map((v) => Number(v)).filter((v) => [1, 3, 6, 12].includes(v))
      : [],
    accent: accentKey(form.accent),
    cta_label: form.cta_label ? String(form.cta_label).trim() : null,
    cta_target: CTA_OPTIONS.some((o) => o.value === form.cta_target) ? form.cta_target : "builder",
    starts_at: startsIso,
    ends_at: endsIso,
    priority: Number.isFinite(Number(form.priority)) ? Math.round(Number(form.priority)) : 0,
    is_active: Boolean(form.is_active),
  };
}

function parseError(err, fallback) {
  const directusErr = err?.response?.data?.errors?.[0]?.message;
  if (directusErr) return directusErr;
  if (err?.message) return String(err.message);
  return fallback;
}

async function loadCampaigns() {
  loading.value = true;
  globalError.value = "";
  try {
    const { data } = await api.get("/items/segment_campaigns", {
      params: { limit: -1, sort: "-priority,starts_at" },
    });
    campaigns.value = Array.isArray(data?.data) ? data.data : [];
    if (!isDraft.value && form.id) {
      const fresh = campaigns.value.find((row) => row.id === form.id);
      if (fresh) selectCampaign(fresh);
    }
  } catch (err) {
    globalError.value = parseError(err, "Не удалось загрузить кампании");
  } finally {
    loading.value = false;
  }
}

async function saveCampaign() {
  if (validationErrors.value.length) return;
  saving.value = true;
  saveError.value = "";
  saveOk.value = false;
  try {
    const payload = buildPayload();
    if (isDraft.value) {
      const { data } = await api.post("/items/segment_campaigns", payload);
      const created = data?.data;
      saveOk.value = true;
      await loadCampaigns();
      if (created?.id) {
        const fresh = campaigns.value.find((row) => row.id === created.id);
        if (fresh) selectCampaign(fresh);
      }
    } else {
      await api.patch(`/items/segment_campaigns/${encodeURIComponent(String(form.id))}`, payload);
      saveOk.value = true;
      await loadCampaigns();
    }
  } catch (err) {
    saveError.value = parseError(err, "Не удалось сохранить кампанию");
  } finally {
    saving.value = false;
  }
}

async function duplicateCampaign() {
  if (isDraft.value) return;
  duplicating.value = true;
  saveError.value = "";
  saveOk.value = false;
  try {
    const payload = buildPayload();
    payload.slug = `${payload.slug || "campaign"}-copy-${Math.floor(Math.random() * 1000)}`;
    payload.title = `${payload.title} (копия)`;
    payload.is_active = false;
    const { data } = await api.post("/items/segment_campaigns", payload);
    const created = data?.data;
    await loadCampaigns();
    if (created?.id) {
      const fresh = campaigns.value.find((row) => row.id === created.id);
      if (fresh) selectCampaign(fresh);
      saveOk.value = true;
    }
  } catch (err) {
    saveError.value = parseError(err, "Не удалось дублировать кампанию");
  } finally {
    duplicating.value = false;
  }
}

async function toggleIsActive() {
  if (isDraft.value || !form.id) return;
  togglingActive.value = true;
  saveError.value = "";
  try {
    const next = !form.is_active;
    await api.patch(`/items/segment_campaigns/${encodeURIComponent(String(form.id))}`, { is_active: next });
    form.is_active = next;
    await loadCampaigns();
  } catch (err) {
    saveError.value = parseError(err, "Не удалось переключить статус");
  } finally {
    togglingActive.value = false;
  }
}

async function deleteCampaign() {
  if (isDraft.value || !form.id) return;
  if (!window.confirm(`Удалить кампанию «${form.title || form.slug}»? Действие нельзя отменить.`)) return;
  deleting.value = true;
  saveError.value = "";
  try {
    await api.delete(`/items/segment_campaigns/${encodeURIComponent(String(form.id))}`);
    selected.value = null;
    Object.assign(form, emptyForm());
    draft.value = false;
    await loadCampaigns();
  } catch (err) {
    saveError.value = parseError(err, "Не удалось удалить кампанию");
  } finally {
    deleting.value = false;
  }
}

onMounted(() => {
  loadCampaigns();
  nowTimer = window.setInterval(() => {
    now.value = Date.now();
  }, 1000);
});

onBeforeUnmount(() => {
  if (nowTimer) {
    window.clearInterval(nowTimer);
    nowTimer = null;
  }
});
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
    radial-gradient(circle at 2% -10%, rgba(245, 158, 11, 0.22), transparent 36%),
    radial-gradient(circle at 105% 2%, rgba(139, 92, 246, 0.18), transparent 40%),
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
  background: linear-gradient(140deg, rgba(245, 158, 11, 0.14), rgba(139, 92, 246, 0.10));
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
  background: rgba(245, 158, 11, 0.18);
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
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
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
}

.field--compact { gap: 5px; }
.field--wide { grid-column: 1 / -1; }
.field--textarea { grid-column: 1 / -1; }
.field--check { display: flex; gap: 8px; align-items: center; }

.input {
  width: 100%;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: inherit;
  padding: 9px 10px;
  font: inherit;
}

.input:focus-visible {
  outline: 2px solid rgba(245, 158, 11, 0.46);
  outline-offset: 1px;
}

.input--select {
  appearance: none;
}

.input--select option {
  color: #0f172a;
  background: #f8fafc;
}

.input--small {
  width: 80px;
}

.input--textarea {
  resize: vertical;
  min-height: 70px;
}

.layout {
  display: grid;
  grid-template-columns: minmax(280px, 380px) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.cards {
  display: grid;
  gap: 10px;
  align-content: start;
}

.empty {
  border: 1px dashed rgba(255, 255, 255, 0.16);
  border-radius: 12px;
  padding: 14px;
  text-align: center;
  font-size: 13px;
  opacity: 0.78;
}

.campaign-card {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.campaign-card:hover {
  transform: translateY(-1px);
  border-color: rgba(245, 158, 11, 0.45);
  background: rgba(255, 255, 255, 0.05);
}

.campaign-card--selected {
  border-color: rgba(245, 158, 11, 0.7);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.14), rgba(255, 255, 255, 0.04));
  box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.45);
}

.campaign-card--inactive {
  opacity: 0.65;
}

.campaign-card__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.campaign-card__priority {
  font-family: "JetBrains Mono", "Consolas", monospace;
  font-size: 11px;
  opacity: 0.7;
}

.campaign-card__title {
  font-weight: 700;
  font-size: 15px;
}

.campaign-card__slug {
  font-family: "JetBrains Mono", "Consolas", monospace;
  font-size: 11px;
  opacity: 0.6;
}

.campaign-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.campaign-card__time {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  opacity: 0.78;
}

.campaign-card__time .v-icon {
  margin-right: 2px;
}

.campaign-card__countdown {
  margin-top: 2px;
  font-size: 12px;
  font-weight: 700;
  color: #fcd34d;
}

.editor {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  min-height: 360px;
}

.editor--empty {
  place-items: center;
  text-align: center;
  font-size: 14px;
  opacity: 0.7;
  padding: 36px;
}

.editor__header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-start;
}

.editor__title {
  font-weight: 800;
  font-size: 19px;
}

.editor__subtitle {
  margin-top: 3px;
  font-size: 12px;
  opacity: 0.74;
}

.editor__header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.preview-strip {
  display: grid;
  gap: 8px;
}

.preview-strip__label {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.66;
}

.banner {
  display: grid;
  gap: 8px;
  padding: 14px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.16), rgba(2, 8, 23, 0.6));
  position: relative;
  overflow: hidden;
  isolation: isolate;
}

.banner::before {
  content: "";
  position: absolute;
  inset: -30%;
  background: radial-gradient(circle at top right, rgba(255, 255, 255, 0.14), transparent 60%);
  z-index: -1;
}

.banner__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.banner__discount {
  font-weight: 800;
  font-size: 22px;
  padding: 4px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.16);
  border: 1px solid rgba(255, 255, 255, 0.22);
}

.banner__segment {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.82;
}

.banner__title {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.2;
}

.banner__subtitle {
  font-size: 13px;
  opacity: 0.85;
}

.banner__bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.banner__timer,
.banner__cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.14);
}

.banner__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.banner__chip {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.16);
}

.banner__chip--all {
  background: rgba(255, 255, 255, 0.05);
}

.accent--gold .banner { background: linear-gradient(135deg, rgba(245, 158, 11, 0.28), rgba(180, 83, 9, 0.42)); border-color: rgba(245, 158, 11, 0.5); }
.accent--cyan .banner { background: linear-gradient(135deg, rgba(34, 211, 238, 0.26), rgba(14, 116, 144, 0.42)); border-color: rgba(34, 211, 238, 0.5); }
.accent--violet .banner { background: linear-gradient(135deg, rgba(139, 92, 246, 0.28), rgba(76, 29, 149, 0.45)); border-color: rgba(139, 92, 246, 0.5); }
.accent--blue .banner { background: linear-gradient(135deg, rgba(59, 130, 246, 0.28), rgba(29, 78, 216, 0.45)); border-color: rgba(59, 130, 246, 0.5); }
.accent--green .banner { background: linear-gradient(135deg, rgba(16, 185, 129, 0.26), rgba(6, 95, 70, 0.45)); border-color: rgba(16, 185, 129, 0.5); }

.accent--gold.campaign-card--selected { border-color: rgba(245, 158, 11, 0.7); }
.accent--cyan.campaign-card--selected { border-color: rgba(34, 211, 238, 0.7); }
.accent--violet.campaign-card--selected { border-color: rgba(139, 92, 246, 0.7); }
.accent--blue.campaign-card--selected { border-color: rgba(59, 130, 246, 0.7); }
.accent--green.campaign-card--selected { border-color: rgba(16, 185, 129, 0.7); }

.flow {
  display: grid;
  gap: 10px;
}

.flow__step {
  display: grid;
  gap: 10px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(2, 8, 23, 0.34);
}

.flow__step-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
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

.segmented--vertical {
  flex-direction: column;
  align-items: stretch;
}

.segmented__btn {
  border: none;
  outline: none;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  display: grid;
  gap: 2px;
}

.segmented__btn:hover {
  background: rgba(255, 255, 255, 0.06);
}

.segmented__btn--active {
  background: linear-gradient(120deg, rgba(245, 158, 11, 0.32), rgba(139, 92, 246, 0.22));
}

.segmented__btn--block {
  width: 100%;
}

.segmented__btn-title {
  font-weight: 700;
  font-size: 13px;
}

.segmented__btn-hint {
  font-size: 11px;
  opacity: 0.75;
}

.slider-row {
  display: grid;
  grid-template-columns: 1fr 84px;
  gap: 8px;
  align-items: center;
}

.slider {
  width: 100%;
  accent-color: rgb(245, 158, 11);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.05);
  color: inherit;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  cursor: pointer;
}

.chip:hover { background: rgba(255, 255, 255, 0.1); }

.chip--active {
  background: linear-gradient(120deg, rgba(245, 158, 11, 0.4), rgba(180, 83, 9, 0.42));
  border-color: rgba(245, 158, 11, 0.7);
  color: #fff7ed;
  font-weight: 700;
}

.chip--ghost {
  background: rgba(255, 255, 255, 0.02);
  border-style: dashed;
}

.hint {
  font-size: 11px;
  opacity: 0.66;
}

.swatches {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.swatch {
  border: 2px solid rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.05);
  border-radius: 10px;
  padding: 8px 12px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.swatch::before {
  content: "";
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.swatch--gold::before { background: linear-gradient(135deg, #f59e0b, #b45309); }
.swatch--cyan::before { background: linear-gradient(135deg, #22d3ee, #0e7490); }
.swatch--violet::before { background: linear-gradient(135deg, #8b5cf6, #4c1d95); }
.swatch--blue::before { background: linear-gradient(135deg, #3b82f6, #1d4ed8); }
.swatch--green::before { background: linear-gradient(135deg, #10b981, #065f46); }

.swatch--active {
  border-color: rgba(255, 255, 255, 0.6);
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.4);
}

.schedule-helpers {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.validation {
  display: grid;
  gap: 6px;
}

.validation__item {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 8px;
  align-items: start;
  border-radius: 10px;
  padding: 9px 10px;
  font-size: 13px;
  border: 1px solid rgba(72, 79, 88, 0.85);
  background: rgba(13, 17, 23, 0.42);
}

.validation__item--error {
  color: #ffb4b4;
  border-color: rgba(239, 68, 68, 0.46);
  background: rgba(239, 68, 68, 0.10);
}

.notice {
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 12px;
}

.notice--ok {
  background: rgba(16, 185, 129, 0.14);
  border: 1px solid rgba(16, 185, 129, 0.42);
  color: #7bf1ca;
}

.notice--error {
  background: rgba(239, 68, 68, 0.13);
  border: 1px solid rgba(239, 68, 68, 0.36);
  color: #ffb4b4;
}

.editor__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.06);
}

.badge--segment { background: rgba(139, 92, 246, 0.16); border-color: rgba(139, 92, 246, 0.42); color: #d8b4fe; }
.badge--discount { background: rgba(245, 158, 11, 0.18); border-color: rgba(245, 158, 11, 0.45); color: #fbd38d; font-weight: 700; }
.badge--months { background: rgba(34, 211, 238, 0.14); border-color: rgba(34, 211, 238, 0.4); color: #67e8f9; }

.status {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 11px;
  border: 1px solid transparent;
  font-weight: 700;
}

.status--live { background: rgba(16, 185, 129, 0.16); color: #7bf1ca; border-color: rgba(16, 185, 129, 0.45); }
.status--scheduled { background: rgba(59, 130, 246, 0.18); color: #bfdbfe; border-color: rgba(59, 130, 246, 0.42); }
.status--expired { background: rgba(148, 163, 184, 0.18); color: #dbe8ff; border-color: rgba(148, 163, 184, 0.4); }
.status--disabled { background: rgba(245, 158, 11, 0.16); color: #fdd280; border-color: rgba(245, 158, 11, 0.4); }
.status--draft { background: rgba(255, 255, 255, 0.08); color: #e7edf8; border-color: rgba(255, 255, 255, 0.18); }

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
.nav__item--active { background: rgba(245, 158, 11, 0.14); }
.nav__item-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(245, 158, 11, 0.18);
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
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(139, 92, 246, 0.10));
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
  background: linear-gradient(120deg, rgba(252, 211, 77, 0.95), rgba(196, 181, 253, 0.95));
}

.nav__brand-title { font-size: 13px; font-weight: 700; }
.nav__brand-subtitle { font-size: 11px; opacity: 0.7; }

@media (max-width: 1280px) {
  .layout { grid-template-columns: 1fr; }
}

@media (max-width: 980px) {
  .hero { grid-template-columns: 1fr; }
  .fields { grid-template-columns: 1fr; }
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
  .editor, .flow__step, .campaign-card { padding: 10px; border-radius: 12px; }
  .hero { padding: 12px; }
  .hero__title { font-size: 18px; line-height: 1.25; }
  .hero__subtitle { font-size: 13px; }
  .nav--premium { flex-direction: column; overflow: visible; }
  .nav--premium .nav__brand,
  .nav--premium .nav__section { min-width: 0; width: 100%; }
  .nav--premium .nav__item { min-height: 44px; width: 100%; }
  input, select, textarea { min-height: 44px; font-size: 16px; }
  :deep(.v-button) { min-height: 44px; }
  .slider-row { grid-template-columns: 1fr; }
}
</style>
