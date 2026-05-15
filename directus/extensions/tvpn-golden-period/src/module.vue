<template>
  <div class="gp">
    <!-- Header -->
    <div class="gp-header">
      <div class="gp-header__title">
        <span class="material-icons">local_fire_department</span>
        <h1>Golden Period</h1>
      </div>
      <div class="gp-header__actions">
        <button
          class="btn btn--secondary"
          :disabled="loadingDashboard"
          @click="loadDashboard"
        >
          <span class="material-icons" :class="{ spinning: loadingDashboard }">refresh</span>
          Обновить
        </button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="gp-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="gp-tab"
        :class="{ 'gp-tab--active': activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        <span class="material-icons">{{ tab.icon }}</span>
        {{ tab.label }}
      </button>
    </div>

    <!-- Banner -->
    <div v-if="errorMessage" class="error-banner">
      <span class="material-icons">error_outline</span>
      {{ errorMessage }}
    </div>
    <div v-if="successMessage" class="success-banner">
      <span class="material-icons">check_circle</span>
      {{ successMessage }}
    </div>

    <!-- Tab: Config -->
    <div v-if="activeTab === 'config'" class="gp-panel">
      <h2>Конфиг кампании</h2>
      <p class="gp-hint">
        Главный feature-flag. Пока выключено — Golden Period полностью dark
        (планировщики работают, но никаких активаций не происходит).
      </p>
      <div class="gp-form">
        <label class="gp-toggle">
          <input v-model="config.is_enabled" type="checkbox" />
          <span>Включён</span>
        </label>
        <label>
          <span>Лимит начислений (cap)</span>
          <input v-model.number="config.default_cap" type="number" min="1" max="10000" />
        </label>
        <label>
          <span>Сумма начисления (₽)</span>
          <input v-model.number="config.payout_amount_rub" type="number" min="1" max="100000" />
        </label>
        <label>
          <span>Минимум активных дней</span>
          <input
            v-model.number="config.eligibility_min_active_days"
            type="number"
            min="1"
            max="365"
          />
        </label>
        <label>
          <span>Длительность окна (часы)</span>
          <input v-model.number="config.window_hours" type="number" min="1" max="720" />
        </label>
        <label>
          <span>Окно clawback (дни)</span>
          <input
            v-model.number="config.clawback_window_days"
            type="number"
            min="1"
            max="365"
          />
        </label>
        <label class="gp-form__textarea">
          <span>signal_thresholds (JSON)</span>
          <textarea v-model="signalThresholdsRaw" rows="4"></textarea>
        </label>
        <label class="gp-form__textarea">
          <span>message_templates (JSON)</span>
          <textarea v-model="messageTemplatesRaw" rows="10"></textarea>
        </label>
      </div>
      <div class="gp-actions">
        <button class="btn btn--primary" :disabled="savingConfig" @click="saveConfig">
          <span class="material-icons" :class="{ spinning: savingConfig }">save</span>
          Сохранить
        </button>
      </div>
    </div>

    <!-- Tab: Dashboard -->
    <div v-if="activeTab === 'dashboard'" class="gp-panel">
      <h2>Дашборд (последние 7 дней)</h2>
      <div class="gp-stats">
        <div class="gp-stat">
          <span class="gp-stat__label">Активных периодов</span>
          <span class="gp-stat__value">{{ fmt(dashboard?.active_periods_count) }}</span>
        </div>
        <div class="gp-stat">
          <span class="gp-stat__label">Всего выплачено за окно</span>
          <span class="gp-stat__value">{{ fmt(dashboard?.total_paid_rub_period) }} ₽</span>
        </div>
        <div class="gp-stat">
          <span class="gp-stat__label">Всего начислений</span>
          <span class="gp-stat__value">{{ fmt(dashboard?.payouts_count_period) }}</span>
        </div>
        <div class="gp-stat">
          <span class="gp-stat__label">Clawback rate</span>
          <span class="gp-stat__value">
            {{ ((dashboard?.clawback_rate || 0) * 100).toFixed(1) }} %
          </span>
        </div>
      </div>
      <h3>Топ рефереры</h3>
      <table class="gp-table">
        <thead>
          <tr>
            <th>user_id</th>
            <th>total_paid_rub</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in dashboard?.top_referrers || []" :key="row.user_id">
            <td>{{ row.user_id }}</td>
            <td>{{ row.total_paid_rub }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Tab: Audit -->
    <div v-if="activeTab === 'audit'" class="gp-panel">
      <h2>Аудит clawback</h2>
      <p class="gp-hint">
        Последние возвраты. Reinstate откатывает clawback и возвращает деньги
        на баланс реферера.
      </p>
      <div class="gp-stats">
        <div class="gp-stat">
          <span class="gp-stat__label">Clawback за окно</span>
          <span class="gp-stat__value">
            {{ fmt(dashboard?.payouts_count_period) }} payouts
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { defineComponent } from "vue";

const API_BASE = (() => {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem("vectra_api_base_url") || "/api";
})();

const ADMIN_TOKEN_HEADER = "X-Admin-Integration-Token";

function tokenFromStorage() {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem("vectra_admin_integration_token") || "";
}

async function apiFetch(path, options = {}) {
  const token = tokenFromStorage();
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    options.headers || {},
  );
  if (token) headers[ADMIN_TOKEN_HEADER] = token;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch (_) {
    body = text;
  }
  if (!res.ok) {
    throw new Error(
      typeof body === "string"
        ? body
        : body?.detail || `HTTP ${res.status}`,
    );
  }
  return body;
}

export default defineComponent({
  name: "ModuleGoldenPeriod",
  data() {
    return {
      activeTab: "config",
      tabs: [
        { id: "config", icon: "tune", label: "Конфиг" },
        { id: "dashboard", icon: "insights", label: "Дашборд" },
        { id: "audit", icon: "policy", label: "Аудит clawback" },
      ],
      config: {
        is_enabled: false,
        default_cap: 15,
        payout_amount_rub: 100,
        eligibility_min_active_days: 3,
        window_hours: 24,
        clawback_window_days: 30,
      },
      signalThresholdsRaw: "{}",
      messageTemplatesRaw: "{}",
      dashboard: null,
      loadingConfig: false,
      savingConfig: false,
      loadingDashboard: false,
      errorMessage: "",
      successMessage: "",
      dashboardTimer: null,
    };
  },
  mounted() {
    this.loadConfig();
    this.loadDashboard();
    this.dashboardTimer = setInterval(this.loadDashboard, 30 * 1000);
  },
  beforeUnmount() {
    if (this.dashboardTimer) clearInterval(this.dashboardTimer);
  },
  methods: {
    fmt(value) {
      if (value === null || value === undefined) return "—";
      return Number(value).toLocaleString("ru-RU");
    },
    setError(text) {
      this.errorMessage = text || "";
      this.successMessage = "";
    },
    setSuccess(text) {
      this.successMessage = text || "";
      this.errorMessage = "";
    },
    async loadConfig() {
      this.loadingConfig = true;
      try {
        const data = await apiFetch("/admin/golden-period/config", {
          method: "GET",
        });
        this.config = {
          is_enabled: !!data.is_enabled,
          default_cap: data.default_cap,
          payout_amount_rub: data.payout_amount_rub,
          eligibility_min_active_days: data.eligibility_min_active_days,
          window_hours: data.window_hours,
          clawback_window_days: data.clawback_window_days,
        };
        this.signalThresholdsRaw = JSON.stringify(
          data.signal_thresholds || {},
          null,
          2,
        );
        this.messageTemplatesRaw = JSON.stringify(
          data.message_templates || {},
          null,
          2,
        );
        this.setError("");
      } catch (err) {
        this.setError(`Не удалось загрузить конфиг: ${err.message}`);
      } finally {
        this.loadingConfig = false;
      }
    },
    async saveConfig() {
      this.savingConfig = true;
      try {
        let signalThresholds;
        let messageTemplates;
        try {
          signalThresholds = JSON.parse(this.signalThresholdsRaw || "{}");
        } catch (e) {
          throw new Error("signal_thresholds: невалидный JSON");
        }
        try {
          messageTemplates = JSON.parse(this.messageTemplatesRaw || "{}");
        } catch (e) {
          throw new Error("message_templates: невалидный JSON");
        }
        const payload = {
          ...this.config,
          signal_thresholds: signalThresholds,
          message_templates: messageTemplates,
        };
        await apiFetch("/admin/golden-period/config", {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        this.setSuccess("Конфиг сохранён");
      } catch (err) {
        this.setError(`Сохранение: ${err.message}`);
      } finally {
        this.savingConfig = false;
      }
    },
    async loadDashboard() {
      this.loadingDashboard = true;
      try {
        const data = await apiFetch(
          "/admin/golden-period/dashboard?range=7d",
          { method: "GET" },
        );
        this.dashboard = data;
        this.setError("");
      } catch (err) {
        this.setError(`Дашборд: ${err.message}`);
      } finally {
        this.loadingDashboard = false;
      }
    },
    async reinstatePayout(payoutId) {
      try {
        await apiFetch(
          `/admin/golden-period/payouts/${payoutId}/reinstate`,
          { method: "POST" },
        );
        this.setSuccess(`Payout #${payoutId} восстановлен`);
        await this.loadDashboard();
      } catch (err) {
        this.setError(`Reinstate: ${err.message}`);
      }
    },
  },
});
</script>

<style scoped>
.gp {
  padding: 16px 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.gp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.gp-header__title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gp-header__title h1 {
  margin: 0;
  font-size: 20px;
}
.gp-tabs {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid #e5e7eb;
  margin-bottom: 16px;
}
.gp-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 0;
  padding: 8px 14px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  color: #6b7280;
}
.gp-tab--active {
  color: #2563eb;
  border-bottom-color: #2563eb;
}
.gp-panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.gp-form {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px 16px;
  margin-bottom: 16px;
}
.gp-form label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: #374151;
}
.gp-form input,
.gp-form textarea {
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font: inherit;
}
.gp-form__textarea {
  grid-column: 1 / -1;
}
.gp-form__textarea textarea {
  font-family: monospace;
  font-size: 12px;
}
.gp-toggle {
  display: flex;
  flex-direction: row !important;
  gap: 8px;
  align-items: center;
}
.gp-actions {
  display: flex;
  gap: 8px;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 4px;
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 13px;
}
.btn--primary {
  background: #2563eb;
  color: #fff;
}
.btn--secondary {
  background: #f3f4f6;
  color: #374151;
  border-color: #d1d5db;
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.gp-hint {
  color: #6b7280;
  margin-bottom: 12px;
  font-size: 13px;
}
.gp-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}
.gp-stat {
  background: #f9fafb;
  padding: 12px;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.gp-stat__label {
  font-size: 12px;
  color: #6b7280;
}
.gp-stat__value {
  font-size: 18px;
  font-weight: 600;
}
.gp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.gp-table th,
.gp-table td {
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
}
.error-banner,
.success-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}
.error-banner {
  background: #fee2e2;
  color: #b91c1c;
}
.success-banner {
  background: #dcfce7;
  color: #166534;
}
.spinning {
  animation: gpspin 1s linear infinite;
}
@keyframes gpspin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
