<template>
  <div class="pwa-studio">
    <!-- Header -->
    <div class="pwa-header">
      <div class="pwa-header__title">
        <span class="material-icons">install_mobile</span>
        <h1>PWA Studio</h1>
      </div>
      <button class="btn btn--secondary" :disabled="loading" @click="loadStats">
        <span class="material-icons" :class="{ spinning: loading }">refresh</span>
        Обновить
      </button>
    </div>

    <!-- Stats error -->
    <div v-if="statsError" class="error-banner">
      <span class="material-icons">error_outline</span>
      {{ statsError }}
    </div>

    <!-- Stat cards -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--blue">
          <span class="material-icons">people</span>
        </div>
        <div class="stat-card__body">
          <div class="stat-card__value">{{ loading ? '—' : fmt(stats?.active_users) }}</div>
          <div class="stat-card__label">Активных пользователей</div>
          <div class="stat-card__sub">с активной push-подпиской</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--purple">
          <span class="material-icons">history</span>
        </div>
        <div class="stat-card__body">
          <div class="stat-card__value">{{ loading ? '—' : fmt(stats?.total_users) }}</div>
          <div class="stat-card__label">Всего установок</div>
          <div class="stat-card__sub">уникальных пользователей за всё время</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--green">
          <span class="material-icons">today</span>
        </div>
        <div class="stat-card__body">
          <div class="stat-card__value">{{ loading ? '—' : fmt(stats?.new_today) }}</div>
          <div class="stat-card__label">Сегодня</div>
          <div class="stat-card__sub">новых подписчиков за сегодня</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--orange">
          <span class="material-icons">date_range</span>
        </div>
        <div class="stat-card__body">
          <div class="stat-card__value">{{ loading ? '—' : fmt(stats?.new_week) }}</div>
          <div class="stat-card__label">За 7 дней</div>
          <div class="stat-card__sub">новых подписчиков за неделю</div>
        </div>
      </div>
    </div>

    <!-- Chart + Broadcast row -->
    <div class="content-row">
      <!-- 30-day chart -->
      <div class="panel panel--chart">
        <div class="panel__header">
          <span class="material-icons">bar_chart</span>
          Рост за 30 дней
        </div>
        <div class="panel__body">
          <div v-if="loading || !stats" class="chart-placeholder">
            <span class="material-icons">hourglass_empty</span>
          </div>
          <div v-else-if="chartBars.length === 0" class="chart-placeholder chart-placeholder--empty">
            <span class="material-icons">show_chart</span>
            <span>Нет данных за последние 30 дней</span>
          </div>
          <div v-else class="chart-wrap">
            <div class="chart-bars">
              <div
                v-for="bar in chartBars"
                :key="bar.date"
                class="chart-bar-col"
                :title="`${bar.date}: +${bar.count} пользователей`"
              >
                <div class="chart-bar" :style="{ height: bar.pct + '%' }"></div>
              </div>
            </div>
            <div class="chart-labels">
              <span>{{ chartBars[0]?.date }}</span>
              <span>{{ chartBars[Math.floor(chartBars.length / 2)]?.date }}</span>
              <span>{{ chartBars[chartBars.length - 1]?.date }}</span>
            </div>
            <div class="chart-legend">
              Пик: +{{ chartPeak }} пользователей за день
            </div>
          </div>
        </div>
      </div>

      <!-- Broadcast form -->
      <div class="panel panel--broadcast">
        <div class="panel__header">
          <span class="material-icons">campaign</span>
          Рассылка PWA-пользователям
        </div>
        <div class="panel__body">
          <div class="broadcast-audience">
            <span class="material-icons">groups</span>
            Аудитория: <strong>{{ loading ? '—' : fmt(stats?.active_users) }}</strong> пользователей
            ({{ loading ? '—' : fmt(stats?.active_subs) }} устройств)
          </div>

          <div class="form-field">
            <label class="form-label">
              Заголовок <span class="required">*</span>
            </label>
            <input
              v-model="form.title"
              class="form-input"
              type="text"
              placeholder="Заголовок уведомления"
              maxlength="200"
              :disabled="broadcasting"
            />
            <div class="form-hint">{{ form.title.length }}/200</div>
          </div>

          <div class="form-field">
            <label class="form-label">
              Текст <span class="required">*</span>
            </label>
            <textarea
              v-model="form.body"
              class="form-input form-input--textarea"
              placeholder="Текст push-уведомления"
              maxlength="1000"
              rows="3"
              :disabled="broadcasting"
            ></textarea>
            <div class="form-hint">{{ form.body.length }}/1000</div>
          </div>

          <div class="form-field">
            <label class="form-label">URL (необязательно)</label>
            <input
              v-model="form.url"
              class="form-input"
              type="url"
              placeholder="https://app.vectra-pro.net/..."
              :disabled="broadcasting"
            />
          </div>

          <div class="form-field">
            <label class="form-label">Тег (необязательно)</label>
            <input
              v-model="form.tag"
              class="form-input"
              type="text"
              placeholder="pwa-broadcast"
              maxlength="100"
              :disabled="broadcasting"
            />
            <div class="form-hint">Одинаковый тег заменяет предыдущее уведомление на устройстве</div>
          </div>

          <!-- Confirm step -->
          <div v-if="showConfirm" class="confirm-box">
            <span class="material-icons">warning</span>
            <div>
              <strong>Отправить уведомление {{ fmt(stats?.active_users) }} пользователям?</strong>
              <div class="confirm-preview">
                «{{ form.title }}» — {{ form.body }}
              </div>
            </div>
          </div>

          <!-- Result -->
          <div v-if="broadcastResult" class="result-box result-box--success">
            <span class="material-icons">check_circle</span>
            <div>
              <strong>Рассылка завершена</strong>
              <div class="result-details">
                Пользователей: {{ broadcastResult.users_total }} •
                С подпиской: {{ broadcastResult.users_with_subs }} •
                Успешно: {{ broadcastResult.success_subs }} •
                Ошибок: {{ broadcastResult.failure_subs }}
              </div>
            </div>
          </div>

          <div v-if="broadcastError" class="result-box result-box--error">
            <span class="material-icons">error</span>
            {{ broadcastError }}
          </div>

          <div class="form-actions">
            <button
              v-if="showConfirm"
              class="btn btn--secondary"
              :disabled="broadcasting"
              @click="showConfirm = false"
            >
              Отмена
            </button>
            <button
              class="btn"
              :class="showConfirm ? 'btn--danger' : 'btn--primary'"
              :disabled="!canSend || broadcasting"
              @click="handleSend"
            >
              <span v-if="broadcasting" class="material-icons spinning">hourglass_empty</span>
              <span class="material-icons" v-else>{{ showConfirm ? 'send' : 'notifications' }}</span>
              {{ broadcasting ? 'Отправляем...' : showConfirm ? 'Подтвердить отправку' : 'Отправить рассылку' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { useApi } from "@directus/extensions-sdk";

const api = useApi();

const loading = ref(false);
const stats = ref(null);
const statsError = ref(null);

const broadcasting = ref(false);
const broadcastResult = ref(null);
const broadcastError = ref(null);
const showConfirm = ref(false);

const form = ref({
  title: "",
  body: "",
  url: "",
  tag: "pwa-broadcast",
});

const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));

const canSend = computed(
  () => form.value.title.trim().length > 0 && form.value.body.trim().length > 0
);

const chartBars = computed(() => {
  const ts = stats.value?.timeseries;
  if (!ts?.length) return [];
  const max = Math.max(...ts.map((r) => r.new_users), 1);
  return ts.map((r) => ({
    date: r.date,
    pct: Math.max(2, Math.round((r.new_users / max) * 100)),
    count: r.new_users,
  }));
});

const chartPeak = computed(() => {
  const ts = stats.value?.timeseries;
  if (!ts?.length) return 0;
  return Math.max(...ts.map((r) => r.new_users));
});

async function loadStats() {
  loading.value = true;
  statsError.value = null;
  try {
    const resp = await api.get("/admin-widgets/pwa-stats");
    stats.value = resp.data;
  } catch (e) {
    statsError.value = e?.response?.data?.error ?? e?.message ?? "Ошибка загрузки";
  } finally {
    loading.value = false;
  }
}

async function handleSend() {
  broadcastResult.value = null;
  broadcastError.value = null;

  if (!showConfirm.value) {
    showConfirm.value = true;
    return;
  }

  broadcasting.value = true;
  showConfirm.value = false;

  try {
    const resp = await api.post("/admin-widgets/push-broadcast", {
      title: form.value.title.trim(),
      body: form.value.body.trim(),
      url: form.value.url.trim() || null,
      tag: form.value.tag.trim() || null,
    });
    broadcastResult.value = resp.data?.result ?? resp.data;
  } catch (e) {
    broadcastError.value = e?.response?.data?.error ?? e?.message ?? "Ошибка отправки";
  } finally {
    broadcasting.value = false;
  }
}

onMounted(loadStats);
</script>

<style scoped>
.pwa-studio {
  padding: 24px 28px;
  min-height: 100%;
  background: var(--theme--background, #0d0f12);
  color: var(--theme--foreground, #eceff4);
  font-family: var(--theme--fonts--sans--font-family, system-ui, sans-serif);
  box-sizing: border-box;
}

/* ── Header ── */
.pwa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.pwa-header__title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pwa-header__title h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.pwa-header__title .material-icons {
  font-size: 28px;
  color: var(--theme--primary, #6644ff);
}

/* ── Error banner ── */
.error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #f87171;
  font-size: 13px;
  margin-bottom: 20px;
}

/* ── Stat cards ── */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  border-radius: 12px;
  background: var(--theme--background-normal, #161a20);
  border: 1px solid var(--theme--border-color, rgba(255,255,255,0.07));
}

.stat-card__icon {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.stat-card__icon .material-icons { font-size: 22px; color: #fff; }

.stat-card__icon--blue   { background: rgba(59,130,246,0.25); }
.stat-card__icon--purple { background: rgba(139,92,246,0.25); }
.stat-card__icon--green  { background: rgba(34,197,94,0.25); }
.stat-card__icon--orange { background: rgba(249,115,22,0.25); }

.stat-card__value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.5px;
}

.stat-card__label {
  font-size: 13px;
  font-weight: 600;
  margin-top: 4px;
  color: var(--theme--foreground, #eceff4);
}

.stat-card__sub {
  font-size: 11px;
  color: var(--theme--foreground-subdued, #6b7280);
  margin-top: 2px;
}

/* ── Content row ── */
.content-row {
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 20px;
  align-items: start;
}

@media (max-width: 1200px) {
  .content-row { grid-template-columns: 1fr; }
}

/* ── Panel ── */
.panel {
  border-radius: 12px;
  background: var(--theme--background-normal, #161a20);
  border: 1px solid var(--theme--border-color, rgba(255,255,255,0.07));
  overflow: hidden;
}

.panel__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--theme--border-color, rgba(255,255,255,0.07));
  color: var(--theme--foreground-subdued, #9ca3af);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.panel__header .material-icons { font-size: 18px; }

.panel__body { padding: 20px; }

/* ── Chart ── */
.chart-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 160px;
  color: var(--theme--foreground-subdued, #6b7280);
  font-size: 13px;
}

.chart-placeholder .material-icons { font-size: 36px; opacity: 0.4; }

.chart-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chart-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 120px;
}

.chart-bar-col {
  flex: 1;
  display: flex;
  align-items: flex-end;
  height: 100%;
  cursor: default;
}

.chart-bar {
  width: 100%;
  background: var(--theme--primary, #6644ff);
  border-radius: 2px 2px 0 0;
  opacity: 0.75;
  transition: opacity 0.15s;
  min-height: 2px;
}

.chart-bar-col:hover .chart-bar { opacity: 1; }

.chart-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--theme--foreground-subdued, #6b7280);
}

.chart-legend {
  font-size: 11px;
  color: var(--theme--foreground-subdued, #6b7280);
  text-align: right;
}

/* ── Broadcast form ── */
.broadcast-audience {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(102,68,255,0.1);
  border: 1px solid rgba(102,68,255,0.2);
  font-size: 13px;
  color: var(--theme--foreground-subdued, #9ca3af);
  margin-bottom: 20px;
}

.broadcast-audience .material-icons { font-size: 18px; color: var(--theme--primary, #6644ff); }
.broadcast-audience strong { color: var(--theme--foreground, #eceff4); }

.form-field {
  margin-bottom: 16px;
}

.form-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--theme--foreground-subdued, #9ca3af);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.required { color: #f87171; margin-left: 2px; }

.form-input {
  width: 100%;
  padding: 9px 12px;
  border-radius: 6px;
  border: 1px solid var(--theme--border-color, rgba(255,255,255,0.1));
  background: var(--theme--background, #0d0f12);
  color: var(--theme--foreground, #eceff4);
  font-size: 13px;
  font-family: inherit;
  box-sizing: border-box;
  transition: border-color 0.15s;
  outline: none;
}

.form-input:focus { border-color: var(--theme--primary, #6644ff); }
.form-input:disabled { opacity: 0.5; cursor: not-allowed; }
.form-input--textarea { resize: vertical; min-height: 72px; }

.form-hint {
  font-size: 11px;
  color: var(--theme--foreground-subdued, #6b7280);
  margin-top: 4px;
  text-align: right;
}

/* ── Confirm box ── */
.confirm-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(249,115,22,0.1);
  border: 1px solid rgba(249,115,22,0.3);
  font-size: 13px;
  margin-bottom: 16px;
}

.confirm-box .material-icons { color: #fb923c; font-size: 20px; flex-shrink: 0; }

.confirm-preview {
  font-size: 12px;
  color: var(--theme--foreground-subdued, #9ca3af);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}

/* ── Result boxes ── */
.result-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 16px;
}

.result-box--success {
  background: rgba(34,197,94,0.1);
  border: 1px solid rgba(34,197,94,0.3);
}

.result-box--success .material-icons { color: #4ade80; font-size: 20px; flex-shrink: 0; }

.result-box--error {
  background: rgba(239,68,68,0.1);
  border: 1px solid rgba(239,68,68,0.3);
  color: #f87171;
}

.result-box--error .material-icons { color: #f87171; font-size: 20px; flex-shrink: 0; }

.result-details {
  font-size: 12px;
  color: var(--theme--foreground-subdued, #9ca3af);
  margin-top: 3px;
}

/* ── Form actions ── */
.form-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 4px;
}

/* ── Buttons ── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  border: none;
  transition: opacity 0.15s, background 0.15s;
}

.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn .material-icons { font-size: 17px; }

.btn--primary {
  background: var(--theme--primary, #6644ff);
  color: #fff;
}

.btn--primary:not(:disabled):hover { opacity: 0.88; }

.btn--secondary {
  background: var(--theme--background-accent, rgba(255,255,255,0.07));
  color: var(--theme--foreground, #eceff4);
  border: 1px solid var(--theme--border-color, rgba(255,255,255,0.1));
}

.btn--secondary:not(:disabled):hover {
  background: var(--theme--background-accent-hover, rgba(255,255,255,0.12));
}

.btn--danger {
  background: #dc2626;
  color: #fff;
}

.btn--danger:not(:disabled):hover { opacity: 0.88; }

/* ── Spinner ── */
@keyframes spin { to { transform: rotate(360deg); } }
.spinning { animation: spin 0.9s linear infinite; display: inline-block; }
</style>
