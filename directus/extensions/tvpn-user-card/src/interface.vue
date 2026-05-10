<template>
  <div class="tvpn-user-card" :data-loading="loading || null" :data-error="error ? '1' : null">
    <div v-if="!resolvedUserId" class="tvpn-user-card__placeholder">
      <span class="tvpn-user-card__placeholder-icon">⚙</span>
      Карточка появится после сохранения пользователя — нужен ID.
    </div>

    <div v-else-if="loading && !data" class="tvpn-user-card__placeholder">
      <span class="tvpn-user-card__spinner" />
      Загружаем карточку пользователя…
    </div>

    <div v-else-if="error" class="tvpn-user-card__error">
      <strong>Не удалось загрузить карточку.</strong>
      <span class="tvpn-user-card__error-detail">{{ error }}</span>
      <button class="tvpn-user-card__retry" type="button" @click="reload">Повторить</button>
    </div>

    <template v-else-if="data">
      <header class="tvpn-user-card__header">
        <div class="tvpn-user-card__avatar" :style="avatarStyle">
          {{ initials }}
        </div>
        <div class="tvpn-user-card__title-block">
          <div class="tvpn-user-card__title">
            {{ data.user.full_name || data.user.username || `User ${data.user.id}` }}
          </div>
          <div class="tvpn-user-card__subtitle">
            <button class="tvpn-user-card__chip tvpn-user-card__chip--copy" type="button" @click="copy(data.user.id)" :title="`Скопировать ID ${data.user.id}`">
              ID {{ data.user.id }} <span class="tvpn-user-card__copy-hint">⧉</span>
            </button>
            <span v-if="data.user.username" class="tvpn-user-card__chip">@{{ data.user.username }}</span>
            <span v-if="data.user.email" class="tvpn-user-card__chip">{{ data.user.email }}</span>
            <span v-if="data.user.language_code" class="tvpn-user-card__chip tvpn-user-card__chip--muted">{{ data.user.language_code }}</span>
            <span v-if="data.user.is_web_user" class="tvpn-user-card__chip tvpn-user-card__chip--muted">web user</span>
          </div>
          <div class="tvpn-user-card__badges">
            <span v-for="b in statusBadges" :key="b.label" class="tvpn-user-card__badge" :class="`tvpn-user-card__badge--${b.tone}`">
              {{ b.label }}
            </span>
          </div>
        </div>
        <button class="tvpn-user-card__refresh" type="button" :disabled="loading" @click="reload" title="Обновить">
          <span v-if="!loading">↻</span>
          <span v-else class="tvpn-user-card__spinner tvpn-user-card__spinner--small" />
        </button>
      </header>

      <section class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Способы входа</h4>
        <div v-if="data.providers.length === 0" class="tvpn-user-card__empty">
          Связанных способов входа не найдено.
        </div>
        <div v-else class="tvpn-user-card__providers">
          <div v-for="(p, idx) in data.providers" :key="`${p.provider}-${idx}`" class="tvpn-user-card__provider">
            <div class="tvpn-user-card__provider-head">
              <span class="tvpn-user-card__provider-icon" :class="`tvpn-user-card__provider-icon--${p.provider}`">
                {{ providerEmoji(p.provider) }}
              </span>
              <span class="tvpn-user-card__provider-name">{{ providerLabel(p.provider) }}</span>
              <span v-if="p.email && p.email_verified" class="tvpn-user-card__badge tvpn-user-card__badge--ok">verified</span>
              <span v-else-if="p.email && p.email_verified === false" class="tvpn-user-card__badge tvpn-user-card__badge--warn">unverified</span>
            </div>
            <div class="tvpn-user-card__provider-body">
              <div v-if="p.external_id" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">External ID</span>
                <button class="tvpn-user-card__chip tvpn-user-card__chip--copy tvpn-user-card__chip--mono" type="button" @click="copy(p.external_id)" :title="`Скопировать ${p.external_id}`">
                  {{ truncateMid(p.external_id, 28) }} <span class="tvpn-user-card__copy-hint">⧉</span>
                </button>
              </div>
              <div v-if="p.email" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">Email</span>
                <span class="tvpn-user-card__chip tvpn-user-card__chip--mono">{{ p.email }}</span>
              </div>
              <div v-if="p.username" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">Username</span>
                <span class="tvpn-user-card__chip">@{{ p.username }}</span>
              </div>
              <div v-if="p.display_name" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">Display name</span>
                <span class="tvpn-user-card__chip">{{ p.display_name }}</span>
              </div>
              <div v-if="p.linked_at" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">Привязан</span>
                <span class="tvpn-user-card__provider-value">{{ formatDateTime(p.linked_at) }} <span class="tvpn-user-card__muted">({{ relative(p.linked_at) }})</span></span>
              </div>
              <div v-if="p.last_login_at" class="tvpn-user-card__provider-row">
                <span class="tvpn-user-card__provider-label">Последний вход</span>
                <span class="tvpn-user-card__provider-value">{{ formatDateTime(p.last_login_at) }} <span class="tvpn-user-card__muted">({{ relative(p.last_login_at) }})</span></span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Подписка и трафик</h4>
        <div class="tvpn-user-card__kpis">
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Истекает</div>
            <div class="tvpn-user-card__kpi-value">{{ data.subscription.expired_at ? formatDate(data.subscription.expired_at) : '—' }}</div>
            <div v-if="data.subscription.days_left !== null" class="tvpn-user-card__kpi-meta" :class="{
              'tvpn-user-card__kpi-meta--bad': data.subscription.is_expired,
              'tvpn-user-card__kpi-meta--ok': !data.subscription.is_expired && data.subscription.days_left > 7,
            }">
              {{ data.subscription.is_expired ? `истекла ${Math.abs(data.subscription.days_left)} д. назад` : `осталось ${data.subscription.days_left} д.` }}
            </div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Тариф</div>
            <div class="tvpn-user-card__kpi-value">{{ data.subscription.active_tariff?.name || '—' }}</div>
            <div v-if="data.subscription.active_tariff" class="tvpn-user-card__kpi-meta">
              {{ data.subscription.active_tariff.months }} мес. · {{ formatRub(data.subscription.active_tariff.price) }}
            </div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Лимит устройств</div>
            <div class="tvpn-user-card__kpi-value">{{ data.subscription.hwid_limit_effective ?? '—' }}</div>
            <div v-if="data.subscription.hwid_limit_user !== null" class="tvpn-user-card__kpi-meta">
              персональный override: {{ data.subscription.hwid_limit_user }}
            </div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">LTE использовано</div>
            <div class="tvpn-user-card__kpi-value">{{ formatGb(data.subscription.lte_used_gb) }} <span class="tvpn-user-card__kpi-of">/ {{ formatGb(data.subscription.lte_total_gb) }}</span></div>
            <div class="tvpn-user-card__progress">
              <div class="tvpn-user-card__progress-bar" :style="{ width: `${Math.min(100, data.subscription.lte_used_percent || 0)}%` }" :class="{
                'tvpn-user-card__progress-bar--warn': (data.subscription.lte_used_percent || 0) >= 80,
              }" />
            </div>
            <div class="tvpn-user-card__kpi-meta">
              осталось {{ formatGb(data.subscription.lte_remaining_gb) }} ({{ (data.subscription.lte_used_percent || 0).toFixed(0) }}%)
              <span v-if="data.subscription.lte_personal_override" class="tvpn-user-card__muted">· override</span>
            </div>
          </div>
        </div>
      </section>

      <section class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Финансы</h4>
        <div class="tvpn-user-card__kpis">
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Всего оплачено</div>
            <div class="tvpn-user-card__kpi-value">{{ formatRub(data.finance.total_succeeded_amount) }}</div>
            <div class="tvpn-user-card__kpi-meta">{{ data.finance.total_succeeded_count }} успешных платежей</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">За 30 дней</div>
            <div class="tvpn-user-card__kpi-value">{{ formatRub(data.finance.amount_30d) }}</div>
            <div class="tvpn-user-card__kpi-meta">за 7 дней: {{ formatRub(data.finance.amount_7d) }}</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Баланс</div>
            <div class="tvpn-user-card__kpi-value">{{ formatRub(data.finance.balance) }}</div>
            <div class="tvpn-user-card__kpi-meta">с баланса использовано: {{ formatRub(data.finance.total_from_balance_amount) }}</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Возвраты / Отмены</div>
            <div class="tvpn-user-card__kpi-value">
              {{ data.finance.refunded_count }}<span class="tvpn-user-card__kpi-of"> / {{ data.finance.canceled_count }}</span>
            </div>
            <div class="tvpn-user-card__kpi-meta" :class="{ 'tvpn-user-card__kpi-meta--bad': data.finance.refund_ratio >= 0.34 }">
              refund ratio: {{ (data.finance.refund_ratio * 100).toFixed(0) }}%
            </div>
          </div>
        </div>
        <div v-if="data.finance.last_payment" class="tvpn-user-card__last-payment">
          Последний платёж: <strong>{{ formatRub(data.finance.last_payment.amount) }}</strong>
          · {{ data.finance.last_payment.provider }}
          · <span :class="`tvpn-user-card__status tvpn-user-card__status--${data.finance.last_payment.status}`">{{ data.finance.last_payment.status }}</span>
          · {{ formatDateTime(data.finance.last_payment.processed_at) }}
          <span class="tvpn-user-card__muted">({{ relative(data.finance.last_payment.processed_at) }})</span>
        </div>
        <details v-if="data.finance.recent && data.finance.recent.length" class="tvpn-user-card__details">
          <summary>Последние {{ data.finance.recent.length }} платежей</summary>
          <table class="tvpn-user-card__table">
            <thead>
              <tr><th>Когда</th><th>Сумма</th><th>Провайдер</th><th>Статус</th><th>Payment ID</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in data.finance.recent" :key="p.id">
                <td>{{ formatDateTime(p.processed_at) }}</td>
                <td>{{ formatRub(p.amount) }}</td>
                <td>{{ p.provider }}</td>
                <td><span :class="`tvpn-user-card__status tvpn-user-card__status--${p.status}`">{{ p.status }}</span></td>
                <td class="tvpn-user-card__mono">{{ truncateMid(p.payment_id, 24) }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </section>

      <section class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Активность и устройства</h4>
        <div class="tvpn-user-card__kpis">
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Регистрация</div>
            <div class="tvpn-user-card__kpi-value">{{ formatDate(registrationDate) }}</div>
            <div v-if="registrationDate" class="tvpn-user-card__kpi-meta">{{ relative(registrationDate) }}</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Первый коннект</div>
            <div class="tvpn-user-card__kpi-value">{{ data.connections.first_day ? formatDate(data.connections.first_day) : '—' }}</div>
            <div v-if="data.connections.first_day" class="tvpn-user-card__kpi-meta">{{ relative(data.connections.first_day) }}</div>
            <div v-if="lastOnlineAt" class="tvpn-user-card__kpi-meta">последний онлайн: {{ relative(lastOnlineAt) }}</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Дни с коннектом</div>
            <div class="tvpn-user-card__kpi-value">{{ data.connections.days_total }}</div>
            <div class="tvpn-user-card__kpi-meta">30д: {{ data.connections.days_30d }} · 7д: {{ data.connections.days_7d }}</div>
          </div>
          <div class="tvpn-user-card__kpi">
            <div class="tvpn-user-card__kpi-label">Устройства</div>
            <div class="tvpn-user-card__kpi-value">{{ data.devices.total }}</div>
            <div class="tvpn-user-card__kpi-meta">активных за 7д: {{ data.devices.active_7d }}</div>
          </div>
        </div>
        <div v-if="data.user.last_hwid_reset || data.user.last_failed_message_at || data.user.prize_wheel_attempts" class="tvpn-user-card__inline-meta">
          <span v-if="data.user.last_hwid_reset">HWID reset: {{ formatDateTime(data.user.last_hwid_reset) }}</span>
          <span v-if="data.user.last_failed_message_at">last delivery fail: {{ formatDateTime(data.user.last_failed_message_at) }} ({{ data.user.failed_message_count }} подряд)</span>
          <span v-if="data.user.prize_wheel_attempts">попыток на колесе: {{ data.user.prize_wheel_attempts }}</span>
        </div>
      </section>

      <section class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Рефералы и атрибуция</h4>
        <div class="tvpn-user-card__grid-two">
          <div class="tvpn-user-card__panel">
            <div class="tvpn-user-card__panel-label">Откуда пришёл</div>
            <div v-if="data.referrals.referrer" class="tvpn-user-card__panel-body">
              <button class="tvpn-user-card__chip tvpn-user-card__chip--link" type="button" @click="openUser(data.referrals.referrer.id)">
                {{ data.referrals.referrer.full_name || data.referrals.referrer.username || data.referrals.referrer.id }}
                <span v-if="data.referrals.referrer.is_partner" class="tvpn-user-card__badge tvpn-user-card__badge--info">partner</span>
              </button>
              <span class="tvpn-user-card__muted">ID {{ data.referrals.referrer.id }}</span>
            </div>
            <div v-else class="tvpn-user-card__empty">прямой заход</div>
            <div v-if="data.user.utm" class="tvpn-user-card__panel-row">
              <span class="tvpn-user-card__provider-label">UTM</span>
              <span class="tvpn-user-card__chip tvpn-user-card__chip--mono">{{ data.user.utm }}</span>
              <span v-if="attributionBadge" class="tvpn-user-card__badge" :class="attributionBadge.class">{{ attributionBadge.label }}</span>
            </div>
            <div v-if="attributionInheritedFrom" class="tvpn-user-card__panel-row tvpn-user-card__muted">
              унаследован от
              <button class="tvpn-user-card__chip tvpn-user-card__chip--link" type="button" @click="openUser(attributionInheritedFrom.id)">
                {{ attributionInheritedFrom.full_name || attributionInheritedFrom.username || attributionInheritedFrom.id }}
              </button>
              <span class="tvpn-user-card__muted">depth {{ attributionInheritedFrom.depth }}</span>
            </div>
            <details v-if="attributionChain.length" class="tvpn-user-card__details">
              <summary>Цепочка атрибуции ({{ attributionChain.length }}{{ data.referrals.attribution.chain_truncated ? '+' : '' }})</summary>
              <ol class="tvpn-user-card__chain">
                <li v-for="item in attributionChain" :key="item.id" class="tvpn-user-card__chain-item">
                  <span class="tvpn-user-card__muted">depth {{ item.depth }}</span>
                  <button class="tvpn-user-card__chip tvpn-user-card__chip--link" type="button" @click="openUser(item.id)">
                    {{ item.full_name || item.username || item.id }}
                  </button>
                  <span v-if="item.is_partner" class="tvpn-user-card__badge tvpn-user-card__badge--info">partner</span>
                  <span v-if="item.utm" class="tvpn-user-card__chip tvpn-user-card__chip--mono">{{ item.utm }}</span>
                  <span v-if="item.utm_is_campaign" class="tvpn-user-card__badge tvpn-user-card__badge--ok">campaign</span>
                </li>
              </ol>
            </details>
          </div>
          <div class="tvpn-user-card__panel">
            <div class="tvpn-user-card__panel-label">Кого привёл</div>
            <div class="tvpn-user-card__panel-body">
              <span class="tvpn-user-card__chip">{{ data.referrals.referrals_count }} прямых</span>
              <span v-if="downstreamExtra > 0" class="tvpn-user-card__chip">+ {{ downstreamExtra }} вниз по цепочке</span>
              <span class="tvpn-user-card__chip">всего: {{ data.referrals.downstream_count ?? data.referrals.referrals_count }}</span>
              <span v-if="data.referrals.referral_bonus_days_total" class="tvpn-user-card__chip">бонус: {{ data.referrals.referral_bonus_days_total }} д.</span>
              <span v-if="data.referrals.is_partner" class="tvpn-user-card__badge tvpn-user-card__badge--info">partner</span>
              <span v-if="data.referrals.custom_referral_percent" class="tvpn-user-card__chip">кастом %: {{ data.referrals.custom_referral_percent }}</span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="data.risk.indicators.length || data.risk.recent_audit.length" class="tvpn-user-card__section">
        <h4 class="tvpn-user-card__section-title">Риск-индикаторы</h4>
        <div v-if="data.risk.indicators.length" class="tvpn-user-card__indicators">
          <div v-for="ind in data.risk.indicators" :key="ind.code" class="tvpn-user-card__indicator" :class="`tvpn-user-card__indicator--${ind.level}`">
            <span class="tvpn-user-card__indicator-dot" />
            <span class="tvpn-user-card__indicator-label">{{ ind.label }}</span>
            <span v-if="ind.detail" class="tvpn-user-card__muted">— {{ ind.detail }}</span>
          </div>
        </div>
        <details v-if="data.risk.recent_audit.length" class="tvpn-user-card__details">
          <summary>Последние auth-события ({{ data.risk.recent_audit.length }})</summary>
          <table class="tvpn-user-card__table">
            <thead>
              <tr><th>Когда</th><th>Провайдер</th><th>Действие</th><th>Результат</th><th>Причина</th></tr>
            </thead>
            <tbody>
              <tr v-for="ev in data.risk.recent_audit" :key="ev.id">
                <td>{{ formatDateTime(ev.created_at) }}</td>
                <td>{{ ev.provider || '—' }}</td>
                <td>{{ ev.action }}</td>
                <td><span :class="`tvpn-user-card__status tvpn-user-card__status--${ev.result}`">{{ ev.result }}</span></td>
                <td>{{ ev.reason || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </section>

      <details v-if="options.showRawJson" class="tvpn-user-card__details tvpn-user-card__raw">
        <summary>Raw JSON</summary>
        <pre>{{ rawJson }}</pre>
      </details>
    </template>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const props = defineProps({
  value: {
    type: [String, Number, null],
    default: null,
  },
  primaryKey: {
    type: [String, Number, null],
    default: null,
  },
  collection: {
    type: String,
    default: "users",
  },
  options: {
    type: Object,
    default: () => ({}),
  },
});

const formValues = inject("values", ref({}));

const resolvedUserId = computed(() => {
  const candidates = [
    props.value,
    props.primaryKey,
    formValues?.value?.id,
  ].filter((v) => v !== null && v !== undefined && v !== "" && v !== "+");
  for (const c of candidates) {
    const s = String(c).trim();
    if (/^-?\d+$/.test(s)) return s;
  }
  return null;
});

const endpointBase = computed(() => {
  const raw = String(props.options?.endpoint || "/admin-widgets/user-card").trim();
  return raw.replace(/\/+$/, "");
});

const data = ref(null);
const loading = ref(false);
const error = ref(null);

const api = useApi();

async function reload() {
  if (!resolvedUserId.value) return;
  loading.value = true;
  error.value = null;
  try {
    const path = `${endpointBase.value}/${encodeURIComponent(resolvedUserId.value)}`;
    const res = await api.get(path);
    data.value = res?.data ?? null;
  } catch (err) {
    error.value = err?.response?.data?.error || err?.message || String(err);
    data.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  if (resolvedUserId.value) reload();
});

watch(resolvedUserId, (next, prev) => {
  if (next && next !== prev) reload();
});

const initials = computed(() => {
  if (!data.value) return "?";
  const name = data.value.user.full_name || data.value.user.username || String(data.value.user.id);
  const parts = String(name).trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("") || "?";
});

const avatarStyle = computed(() => {
  const id = data.value ? String(data.value.user.id) : "0";
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) % 360;
  return {
    background: `linear-gradient(135deg, hsl(${h} 70% 50%), hsl(${(h + 60) % 360} 70% 40%))`,
  };
});

const statusBadges = computed(() => {
  if (!data.value) return [];
  const u = data.value.user;
  const s = data.value.subscription;
  const out = [];
  if (s.is_active) out.push({ label: "Подписка активна", tone: "ok" });
  if (s.is_expired) out.push({ label: "Подписка истекла", tone: "bad" });
  if (u.is_trial) out.push({ label: "Триал", tone: "info" });
  if (u.is_blocked) out.push({ label: "Заблокирован", tone: "bad" });
  if (u.is_admin) out.push({ label: "Admin", tone: "warn" });
  if (u.is_partner) out.push({ label: "Partner", tone: "info" });
  if (!u.is_registered) out.push({ label: "Не зарегистрирован", tone: "muted" });
  if (u.key_activated) out.push({ label: "Key activated", tone: "muted" });
  return out;
});

const rawJson = computed(() => (data.value ? JSON.stringify(data.value, null, 2) : ""));

// Registration: prefer the explicit bot-side first-start date; fall back to the
// Tortoise auto-stamped created_at so web-only users (no registration_date) still
// render a meaningful date instead of an em-dash.
const registrationDate = computed(() => data.value?.user?.registration_date || data.value?.user?.created_at || null);

// "Last online" prefers user_devices.last_online_at (per-device telemetry); if
// device-per-user is off, fall back to users.connected_at which is overwritten
// by the catcher to the most recent online timestamp.
const lastOnlineAt = computed(() => data.value?.devices?.last_online_at || data.value?.user?.connected_at || null);

const attributionChain = computed(() => data.value?.referrals?.attribution?.chain ?? []);
const attributionInheritedFrom = computed(() => data.value?.referrals?.attribution?.inherited_from ?? null);
const attributionBadge = computed(() => {
  const src = data.value?.referrals?.attribution?.source;
  if (!src) return null;
  const map = {
    inherited: { label: "inherited", class: "tvpn-user-card__badge--ok" },
    direct: { label: "direct", class: "tvpn-user-card__badge--info" },
    "partner-default": { label: "partner-default", class: "tvpn-user-card__badge--muted" },
    "referral-no-utm": { label: "no-utm", class: "tvpn-user-card__badge--muted" },
    organic: { label: "organic", class: "tvpn-user-card__badge--muted" },
  };
  return map[src] || null;
});
const downstreamExtra = computed(() => {
  const total = Number(data.value?.referrals?.downstream_count ?? 0);
  const direct = Number(data.value?.referrals?.referrals_count ?? 0);
  return Math.max(0, total - direct);
});

function providerEmoji(p) {
  const m = { telegram: "✈", google: "G", yandex: "Я", apple: "", password: "✉" };
  return m[p] || "•";
}
function providerLabel(p) {
  const m = {
    telegram: "Telegram",
    google: "Google",
    yandex: "Yandex",
    apple: "Apple",
    password: "Email + пароль",
  };
  return m[p] || p;
}

function formatDate(v) {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleDateString("ru-RU");
  } catch (_e) {
    return String(v);
  }
}
function formatDateTime(v) {
  if (!v) return "—";
  try {
    const d = new Date(v);
    return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  } catch (_e) {
    return String(v);
  }
}
function relative(v) {
  if (!v) return "";
  const d = new Date(v).getTime();
  if (Number.isNaN(d)) return "";
  const diffSec = Math.round((Date.now() - d) / 1000);
  const abs = Math.abs(diffSec);
  const past = diffSec >= 0;
  if (abs < 60) return past ? "только что" : "сейчас";
  if (abs < 3600) return `${Math.round(abs / 60)} мин ${past ? "назад" : ""}`.trim();
  if (abs < 86400) return `${Math.round(abs / 3600)} ч ${past ? "назад" : ""}`.trim();
  if (abs < 86400 * 30) return `${Math.round(abs / 86400)} д ${past ? "назад" : ""}`.trim();
  if (abs < 86400 * 365) return `${Math.round(abs / 86400 / 30)} мес ${past ? "назад" : ""}`.trim();
  return `${Math.round(abs / 86400 / 365)} лет ${past ? "назад" : ""}`.trim();
}
function formatRub(v) {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return "0 ₽";
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}
function formatGb(v) {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return "0 ГБ";
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ГБ`;
}
function truncateMid(value, max = 28) {
  const s = String(value ?? "");
  if (s.length <= max) return s;
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}
async function copy(value) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(String(value));
    }
  } catch (_e) {
    /* noop */
  }
}
function openUser(id) {
  if (!id) return;
  const path = `/admin/content/users/${encodeURIComponent(id)}`;
  window.open(path, "_blank", "noopener");
}
</script>

<style scoped>
.tvpn-user-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  border-radius: 12px;
  background: var(--theme--background-subdued, #0f1622);
  color: var(--theme--foreground, #e7eaf2);
  border: 1px solid var(--theme--border-color, #1f2735);
  font-size: 13px;
  line-height: 1.45;
}

.tvpn-user-card__placeholder,
.tvpn-user-card__empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px;
  border-radius: 10px;
  background: var(--theme--background, #0a0f17);
  color: var(--theme--foreground-subdued, #8893a7);
  font-style: italic;
}

.tvpn-user-card__placeholder-icon {
  font-size: 18px;
}

.tvpn-user-card__error {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
  background: rgba(220, 60, 60, 0.12);
  border: 1px solid rgba(220, 60, 60, 0.4);
  color: #ffb3b3;
  flex-wrap: wrap;
}
.tvpn-user-card__error-detail {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.tvpn-user-card__retry {
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
}

.tvpn-user-card__spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-right-color: transparent;
  animation: tvpn-user-card-spin 0.85s linear infinite;
  display: inline-block;
}
.tvpn-user-card__spinner--small {
  width: 10px;
  height: 10px;
  border-width: 2px;
}
@keyframes tvpn-user-card-spin {
  to { transform: rotate(360deg); }
}

.tvpn-user-card__header {
  display: grid;
  grid-template-columns: 56px 1fr auto;
  gap: 14px;
  align-items: center;
}
.tvpn-user-card__avatar {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 20px;
  color: #fff;
  letter-spacing: 0.04em;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}
.tvpn-user-card__title {
  font-weight: 700;
  font-size: 18px;
  color: var(--theme--foreground-accent, #fff);
}
.tvpn-user-card__subtitle {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.tvpn-user-card__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.tvpn-user-card__refresh {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: 1px solid var(--theme--border-color, #2a2f3a);
  background: var(--theme--background, #0a0f17);
  color: var(--theme--foreground, #fff);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}
.tvpn-user-card__refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tvpn-user-card__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--theme--background, #0a0f17);
  border: 1px solid var(--theme--border-color, #2a2f3a);
  color: var(--theme--foreground, #e7eaf2);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  cursor: default;
}
.tvpn-user-card__chip--copy { cursor: copy; }
.tvpn-user-card__chip--copy:hover { border-color: var(--theme--primary, #6b7cff); color: var(--theme--primary, #6b7cff); }
.tvpn-user-card__chip--mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.tvpn-user-card__chip--muted { opacity: 0.7; }
.tvpn-user-card__chip--link { cursor: pointer; }
.tvpn-user-card__chip--link:hover { border-color: var(--theme--primary, #6b7cff); }
.tvpn-user-card__copy-hint { opacity: 0.55; font-size: 10px; }

.tvpn-user-card__badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tvpn-user-card__badge--ok { background: rgba(50, 180, 100, 0.18); color: #6dd49a; }
.tvpn-user-card__badge--bad { background: rgba(220, 60, 60, 0.18); color: #ff8b8b; }
.tvpn-user-card__badge--warn { background: rgba(245, 180, 60, 0.18); color: #ffd06b; }
.tvpn-user-card__badge--info { background: rgba(80, 130, 240, 0.18); color: #8ab2ff; }
.tvpn-user-card__badge--muted { background: rgba(140, 145, 160, 0.18); color: #aab1c0; }

.tvpn-user-card__section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--theme--background, #0a0f17);
  padding: 14px;
  border-radius: 10px;
  border: 1px solid var(--theme--border-color, #1f2735);
}
.tvpn-user-card__section-title {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--theme--foreground-subdued, #8893a7);
}

.tvpn-user-card__providers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}
.tvpn-user-card__provider {
  background: var(--theme--background-subdued, #0f1622);
  border: 1px solid var(--theme--border-color, #1f2735);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tvpn-user-card__provider-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tvpn-user-card__provider-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  background: var(--theme--background, #0a0f17);
  border: 1px solid var(--theme--border-color, #2a2f3a);
}
.tvpn-user-card__provider-icon--telegram { color: #29a7e6; }
.tvpn-user-card__provider-icon--google { color: #ea4335; }
.tvpn-user-card__provider-icon--yandex { color: #fc3f1d; }
.tvpn-user-card__provider-icon--apple { color: #fff; }
.tvpn-user-card__provider-icon--password { color: var(--theme--primary, #6b7cff); }

.tvpn-user-card__provider-name {
  font-weight: 600;
}
.tvpn-user-card__provider-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.tvpn-user-card__provider-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 8px;
  align-items: center;
}
.tvpn-user-card__provider-label {
  color: var(--theme--foreground-subdued, #8893a7);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tvpn-user-card__provider-value {
  font-size: 12px;
}

.tvpn-user-card__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}
.tvpn-user-card__kpi {
  background: var(--theme--background-subdued, #0f1622);
  border: 1px solid var(--theme--border-color, #1f2735);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tvpn-user-card__kpi-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__kpi-value {
  font-size: 17px;
  font-weight: 700;
  color: var(--theme--foreground-accent, #fff);
}
.tvpn-user-card__kpi-of {
  font-weight: 400;
  color: var(--theme--foreground-subdued, #8893a7);
  font-size: 13px;
}
.tvpn-user-card__kpi-meta {
  font-size: 11px;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__kpi-meta--ok { color: #6dd49a; }
.tvpn-user-card__kpi-meta--bad { color: #ff8b8b; }

.tvpn-user-card__progress {
  height: 6px;
  background: var(--theme--background, #0a0f17);
  border-radius: 999px;
  overflow: hidden;
  margin-top: 4px;
}
.tvpn-user-card__progress-bar {
  height: 100%;
  background: linear-gradient(90deg, #6b7cff, #29a7e6);
  border-radius: 999px;
  transition: width 0.3s ease;
}
.tvpn-user-card__progress-bar--warn {
  background: linear-gradient(90deg, #ff8b3d, #ff5a5a);
}

.tvpn-user-card__last-payment {
  font-size: 12px;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__inline-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11px;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__muted {
  color: var(--theme--foreground-subdued, #8893a7);
}

.tvpn-user-card__details {
  background: var(--theme--background-subdued, #0f1622);
  border: 1px solid var(--theme--border-color, #1f2735);
  border-radius: 10px;
  padding: 8px 12px;
}
.tvpn-user-card__details summary {
  cursor: pointer;
  font-weight: 600;
  font-size: 12px;
  color: var(--theme--foreground-subdued, #8893a7);
  user-select: none;
}
.tvpn-user-card__details[open] summary {
  margin-bottom: 8px;
}

.tvpn-user-card__chain {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0 0 0 4px;
  list-style: none;
  font-size: 12px;
}
.tvpn-user-card__chain-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tvpn-user-card__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.tvpn-user-card__table th,
.tvpn-user-card__table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--theme--border-color, #1f2735);
  text-align: left;
}
.tvpn-user-card__table th {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.tvpn-user-card__status {
  display: inline-flex;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tvpn-user-card__status--succeeded,
.tvpn-user-card__status--success { background: rgba(50, 180, 100, 0.18); color: #6dd49a; }
.tvpn-user-card__status--refunded,
.tvpn-user-card__status--canceled,
.tvpn-user-card__status--cancelled,
.tvpn-user-card__status--chargebacked,
.tvpn-user-card__status--chargeback,
.tvpn-user-card__status--failed,
.tvpn-user-card__status--failure { background: rgba(220, 60, 60, 0.18); color: #ff8b8b; }
.tvpn-user-card__status--pending,
.tvpn-user-card__status--processing { background: rgba(245, 180, 60, 0.18); color: #ffd06b; }

.tvpn-user-card__indicators {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tvpn-user-card__indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--theme--background-subdued, #0f1622);
  border: 1px solid var(--theme--border-color, #1f2735);
}
.tvpn-user-card__indicator-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__indicator--warn .tvpn-user-card__indicator-dot { background: #ff8b3d; }
.tvpn-user-card__indicator--bad .tvpn-user-card__indicator-dot { background: #ff5a5a; }
.tvpn-user-card__indicator--ok .tvpn-user-card__indicator-dot { background: #6dd49a; }
.tvpn-user-card__indicator--info .tvpn-user-card__indicator-dot { background: #6b7cff; }

.tvpn-user-card__grid-two {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
}
.tvpn-user-card__panel {
  background: var(--theme--background-subdued, #0f1622);
  border: 1px solid var(--theme--border-color, #1f2735);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tvpn-user-card__panel-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--theme--foreground-subdued, #8893a7);
}
.tvpn-user-card__panel-body {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.tvpn-user-card__panel-row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}

.tvpn-user-card__raw pre {
  margin: 0;
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--theme--foreground-subdued, #8893a7);
  max-height: 360px;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
