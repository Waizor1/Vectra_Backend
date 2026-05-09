<template>
  <private-view title="Контент Ops">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">OPS</div>
          <div>
            <div class="nav__brand-title">Контент Ops</div>
            <div class="nav__brand-subtitle">KPI, очереди, быстрый поиск</div>
          </div>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-content-ops' }">
            <span class="nav__item-icon"><v-icon name="hub" /></span>
            <span class="nav__item-label">Operations</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-segment-studio' }">
            <span class="nav__item-icon"><v-icon name="campaign" /></span>
            <span class="nav__item-label">Сегментные акции</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Промокоды</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-tariff-studio' }">
            <span class="nav__item-icon"><v-icon name="tune" /></span>
            <span class="nav__item-label">Тарифы</span>
          </router-link>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Быстрые ссылки</div>
          <router-link class="nav__item" :to="{ path: '/content/users' }">
            <span class="nav__item-icon"><v-icon name="people" /></span>
            <span class="nav__item-label">Пользователи</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/insights' }">
            <span class="nav__item-icon"><v-icon name="timeline" /></span>
            <span class="nav__item-label">Аналитика</span>
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
            <h1 class="hero__title">Контент Ops</h1>
            <p class="hero__subtitle">
              Операционный слой для быстрых решений: KPI, очереди риска, поиск по users / payments / promo и
              one-click переходы в карточки коллекций.
            </p>
            <div class="hero__chips">
              <span class="hero__chip"><v-icon name="search" small /> Поиск по 3 коллекциям</span>
              <span class="hero__chip"><v-icon name="warning" small /> Истекающие, заблокированные, ошибки</span>
              <span class="hero__chip"><v-icon name="schedule" small /> Обновление: {{ lastUpdatedLabel }}</span>
            </div>
          </div>

          <div class="hero__right">
            <label class="field field--compact">
              <span>Поиск</span>
              <input
                v-model.trim="search"
                class="input"
                placeholder="user id / username / payment id / promo"
              />
            </label>
            <div class="hero__filters">
              <label class="field field--compact">
                <span>Истекает, дн</span>
                <input v-model.number="filters.expiring_days" class="input input--num" type="number" min="1" max="90" />
              </label>
              <label class="field field--compact">
                <span>Блок, дн</span>
                <input v-model.number="filters.blocked_days" class="input input--num" type="number" min="1" max="30" />
              </label>
              <label class="field field--compact">
                <span>Лимит</span>
                <input v-model.number="filters.limit" class="input input--num" type="number" min="1" max="30" />
              </label>
            </div>
          </div>
        </section>

        <div v-if="searchLoading || quickMatches.length || hasSearchQuery" class="search-results">
          <div class="search-results__head">
            <v-icon name="search" small />
            <span v-if="searchLoading">Ищем по users / payments / promo…</span>
            <span v-else-if="quickMatches.length">{{ hasSearchQuery ? 'Найдено' : 'Свежие сущности' }}: {{ quickMatches.length }}</span>
            <span v-else>Совпадений не найдено</span>
          </div>
          <div v-if="quickMatches.length" class="search-results__grid">
            <router-link
              v-for="item in quickMatches"
              :key="item.key"
              class="search-results__item"
              :to="item.path"
            >
              <div class="search-results__main">{{ item.title }}</div>
              <div class="search-results__meta">{{ item.meta }}</div>
            </router-link>
          </div>
        </div>

        <v-notice v-if="error" type="danger">{{ error }}</v-notice>

        <div class="kpi-grid">
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--blue"><v-icon name="people" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Users</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.users.total) }}</div>
                <div class="kpi__meta">
                  blocked {{ fmt(summary.users.blocked) }} · recent {{ fmt(summary.users.blocked_recent) }} · exp {{ fmt(summary.users.expiring_soon) }}
                </div>
              </div>
            </div>
          </v-card>
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--green"><v-icon name="payments" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Payments</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.payments.total) }}</div>
                <div class="kpi__meta">failed {{ fmt(summary.payments.failed) }} · sum {{ fmtMoney(summary.payments.total_amount) }}</div>
              </div>
            </div>
          </v-card>
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--amber"><v-icon name="confirmation_number" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Promo</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.promo.active_codes) }}</div>
                <div class="kpi__meta">usages 7d {{ fmt(summary.promo.usages_7d) }}</div>
              </div>
            </div>
          </v-card>
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--cyan"><v-icon name="family_restroom" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Family</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.family.members) }}</div>
                <div class="kpi__meta">active invites {{ fmt(summary.family.active_invites) }}</div>
              </div>
            </div>
          </v-card>
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--red"><v-icon name="error" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Errors</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.errors.new) }}</div>
                <div class="kpi__meta">in progress {{ fmt(summary.errors.in_progress) }}</div>
              </div>
            </div>
          </v-card>
          <v-card class="kpi">
            <div class="kpi__row">
              <div class="kpi__icon kpi__icon--purple"><v-icon name="account_balance_wallet" /></div>
              <div class="kpi__content">
                <div class="kpi__label">Partners</div>
                <div v-if="loading && !lastUpdated" class="kpi__skel"><span /></div>
                <div v-else class="kpi__value">{{ fmt(summary.partners.pending_withdrawals) }}</div>
                <div class="kpi__meta">pending withdrawals</div>
              </div>
            </div>
          </v-card>
        </div>

        <div class="queues">
          <v-card class="panel">
            <div class="panel__header">
              <div>
                <div class="panel__title">Users · expiring / blocked / balance</div>
                <div class="panel__subtitle">Сущности, к которым стоит зайти первым делом</div>
              </div>
              <span class="panel__count">
                {{ usersQueueCount }}
              </span>
            </div>
            <div v-if="loading && !lastUpdated" class="rows">
              <div v-for="i in 4" :key="`skel-u-${i}`" class="row-skel"><span /><span /></div>
            </div>
            <div v-else-if="usersQueueCount > 0" class="rows">
              <router-link v-for="row in queues.users_expiring" :key="`exp-${row.id}`" class="row" :to="userPath(row.id)">
                <span class="row__title">{{ userLabel(row) }}</span>
                <span class="row__meta">exp {{ formatDate(row.expired_at) }}</span>
              </router-link>
              <router-link v-for="row in queues.users_blocked_recent" :key="`blk-${row.id}`" class="row row--warn" :to="userPath(row.id)">
                <span class="row__title">{{ userLabel(row) }}</span>
                <span class="row__meta">blocked {{ formatDateTime(row.blocked_at) }}</span>
              </router-link>
              <router-link v-for="row in queues.users_top_balance" :key="`bal-${row.id}`" class="row row--good" :to="userPath(row.id)">
                <span class="row__title">{{ userLabel(row) }}</span>
                <span class="row__meta">{{ fmtMoney(row.balance) }} ₽</span>
              </router-link>
            </div>
            <div v-else class="rows-empty">
              <v-icon name="check_circle" />
              <span>Очередь пуста — никто не истекает, не заблокирован недавно, балансы в норме.</span>
            </div>
          </v-card>

          <v-card class="panel">
            <div class="panel__header">
              <div>
                <div class="panel__title">Payments · Promo · Errors · Partners</div>
                <div class="panel__subtitle">Свежие операции и обращения</div>
              </div>
              <span class="panel__count">{{ opsQueueCount }}</span>
            </div>
            <div v-if="loading && !lastUpdated" class="rows">
              <div v-for="i in 4" :key="`skel-o-${i}`" class="row-skel"><span /><span /></div>
            </div>
            <div v-else-if="opsQueueCount > 0" class="rows">
              <router-link v-for="row in queues.payments_recent" :key="`pay-${row.id}`" class="row" :to="paymentPath(row.id)">
                <span class="row__title">payment {{ row.payment_id || row.id }}</span>
                <span class="row__meta">{{ fmtMoney(row.amount) }} ₽ · {{ row.status }} · {{ formatDateTime(row.processed_at) }}</span>
              </router-link>
              <router-link v-for="row in queues.promo_usages_recent" :key="`prm-${row.id}`" class="row" :to="promoUsagePath(row.id)">
                <span class="row__title">promo usage {{ row.id }}</span>
                <span class="row__meta">user {{ row.user_id }} · {{ formatDateTime(row.used_at) }}</span>
              </router-link>
              <router-link v-for="row in queues.errors_new" :key="`err-${row.id}`" class="row row--bad" :to="errorPath(row.id)">
                <span class="row__title">{{ row.code || row.type || 'error' }}</span>
                <span class="row__meta">{{ row.triage_severity }} · {{ formatDateTime(row.created_at) }}</span>
              </router-link>
              <router-link v-for="row in queues.partner_withdrawals_pending" :key="`wd-${row.id}`" class="row row--warn" :to="withdrawalPath(row.id)">
                <span class="row__title">withdrawal {{ row.id }}</span>
                <span class="row__meta">owner {{ row.owner_id }} · {{ fmtMoney(row.amount_rub) }} ₽</span>
              </router-link>
            </div>
            <div v-else class="rows-empty">
              <v-icon name="check_circle" />
              <span>Свежих платежей, активаций, ошибок и заявок партнёров нет.</span>
            </div>
          </v-card>
        </div>

        <v-card class="launch">
          <div class="panel__header">
            <div>
              <div class="panel__title">Launch Grid</div>
              <div class="panel__subtitle">Прямые переходы в коллекции Directus</div>
            </div>
          </div>
          <div class="launch__grid">
            <router-link v-for="item in launchGrid" :key="item.path" class="launch__item" :to="{ path: item.path }">
              <div class="launch__head">
                <v-icon :name="item.icon" />
                <span>{{ item.title }}</span>
              </div>
              <div class="launch__desc">{{ item.desc }}</div>
              <div class="launch__path">{{ item.path }}</div>
            </router-link>
          </div>
        </v-card>
      </div>
    </div>
  </private-view>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useApi } from '@directus/extensions-sdk';

const api = useApi();

const loading = ref(false);
const searchLoading = ref(false);
const error = ref('');
const lastUpdated = ref(null);
const searchResults = ref({
	users: [],
	payments: [],
	promo_codes: [],
});
let searchTimer = null;
let searchRequestId = 0;

const filters = ref({
	expiring_days: 7,
	blocked_days: 3,
	limit: 8,
});

const search = ref('');

const summary = ref({
	users: { total: 0, blocked: 0, blocked_recent: 0, expiring_soon: 0 },
	payments: { total: 0, failed: 0, total_amount: 0 },
	promo: { active_codes: 0, usages_7d: 0 },
	family: { members: 0, active_invites: 0 },
	errors: { new: 0, in_progress: 0 },
	partners: { pending_withdrawals: 0 },
});

const queues = ref({
	users_expiring: [],
	users_blocked_recent: [],
	users_top_balance: [],
	payments_recent: [],
	promo_usages_recent: [],
	errors_new: [],
	partner_withdrawals_pending: [],
});

const launchGrid = [
	{ path: '/content/users', icon: 'people', title: 'Пользователи', desc: 'Профили, лимиты, блокировки' },
	{ path: '/content/active_tariffs', icon: 'card_membership', title: 'Активные тарифы', desc: 'LTE usage, устройства и периоды' },
	{ path: '/content/tariffs', icon: 'tune', title: 'Тарифы', desc: 'Цены, family-варианты, витрина' },
	{ path: '/content/processed_payments', icon: 'payments', title: 'Платежи', desc: 'Крупные / проблемные операции' },
	{ path: '/content/promo_codes', icon: 'confirmation_number', title: 'Промокоды', desc: 'Активные, истекающие, отключенные' },
	{ path: '/content/promo_usages', icon: 'history', title: 'Promo usage', desc: 'Последние применения' },
	{ path: '/content/segment_campaigns', icon: 'campaign', title: 'Сегментные акции', desc: 'Скидочные кампании по сегментам' },
	{ path: '/content/error_reports', icon: 'bug_report', title: 'Ошибки', desc: 'Triage-очередь и SLA' },
	{ path: '/content/partner_withdrawals', icon: 'account_balance_wallet', title: 'Партнёрка', desc: 'Заявки на вывод' },
];

function toNum(value) {
	const num = Number(value);
	return Number.isFinite(num) ? num : 0;
}

function fmt(value) {
	return new Intl.NumberFormat('ru-RU').format(toNum(value));
}

function fmtMoney(value) {
	return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(toNum(value));
}

function formatDate(value) {
	if (!value) return '—';
	const dt = new Date(value);
	if (Number.isNaN(dt.getTime())) return '—';
	return dt.toLocaleDateString('ru-RU');
}

function formatDateTime(value) {
	if (!value) return '—';
	const dt = new Date(value);
	if (Number.isNaN(dt.getTime())) return '—';
	return dt.toLocaleString('ru-RU', { hour12: false });
}

function userLabel(row) {
	return row?.username || row?.full_name || String(row?.id || 'user');
}

function userPath(id) {
	return { path: `/content/users/${id}` };
}

function paymentPath(id) {
	return { path: `/content/processed_payments/${id}` };
}

function promoUsagePath(id) {
	return { path: `/content/promo_usages/${id}` };
}

function errorPath(id) {
	return { path: `/content/error_reports/${id}` };
}

function withdrawalPath(id) {
	return { path: `/content/partner_withdrawals/${id}` };
}

const fallbackSearchPool = computed(() => {
	const out = [];
	for (const row of queues.value.users_expiring || []) {
		out.push({ key: `u-exp-${row.id}`, title: userLabel(row), meta: `user ${row.id} · exp ${formatDate(row.expired_at)}`, path: `/content/users/${row.id}` });
	}
	for (const row of queues.value.users_top_balance || []) {
		out.push({ key: `u-bal-${row.id}`, title: userLabel(row), meta: `user ${row.id} · balance ${fmtMoney(row.balance)}`, path: `/content/users/${row.id}` });
	}
	for (const row of queues.value.payments_recent || []) {
		out.push({ key: `pay-${row.id}`, title: `payment ${row.payment_id || row.id}`, meta: `user ${row.user_id} · ${fmtMoney(row.amount)}`, path: `/content/processed_payments/${row.id}` });
	}
	for (const row of queues.value.promo_usages_recent || []) {
		out.push({ key: `promo-${row.id}`, title: `promo usage ${row.id}`, meta: `user ${row.user_id}`, path: `/content/promo_usages/${row.id}` });
	}
	return out;
});

const globalSearchPool = computed(() => {
	const out = [];
	for (const row of searchResults.value.users || []) {
		out.push({
			key: `gs-user-${row.id}`,
			title: userLabel(row),
			meta: `user ${row.id} · balance ${fmtMoney(row.balance)}`,
			path: `/content/users/${row.id}`,
		});
	}
	for (const row of searchResults.value.payments || []) {
		out.push({
			key: `gs-pay-${row.id}`,
			title: `payment ${row.payment_id || row.id}`,
			meta: `user ${row.user_id} · ${fmtMoney(row.amount)} · ${row.status}`,
			path: `/content/processed_payments/${row.id}`,
		});
	}
	for (const row of searchResults.value.promo_codes || []) {
		out.push({
			key: `gs-promo-${row.id}`,
			title: row.name || `promo ${row.id}`,
			meta: `promo id ${row.id} · created ${formatDate(row.created_at)}`,
			path: `/content/promo_codes/${row.id}`,
		});
	}
	return out;
});

const hasSearchQuery = computed(() => String(search.value || '').trim().length > 0);

const quickMatches = computed(() => {
	const q = String(search.value || '').trim().toLowerCase();
	if (!q) return fallbackSearchPool.value.slice(0, 8);
	return globalSearchPool.value
		.filter((item) => `${item.title} ${item.meta}`.toLowerCase().includes(q))
		.slice(0, 12);
});

const usersQueueCount = computed(() =>
	(queues.value.users_expiring?.length || 0) +
	(queues.value.users_blocked_recent?.length || 0) +
	(queues.value.users_top_balance?.length || 0)
);

const opsQueueCount = computed(() =>
	(queues.value.payments_recent?.length || 0) +
	(queues.value.promo_usages_recent?.length || 0) +
	(queues.value.errors_new?.length || 0) +
	(queues.value.partner_withdrawals_pending?.length || 0)
);

const lastUpdatedLabel = computed(() => {
	if (!lastUpdated.value) return 'ещё нет данных';
	return formatDateTime(lastUpdated.value);
});

async function refresh() {
	loading.value = true;
	error.value = '';
	try {
		const params = {
			expiring_days: Math.max(1, Math.min(90, Number(filters.value.expiring_days) || 7)),
			blocked_days: Math.max(1, Math.min(30, Number(filters.value.blocked_days) || 3)),
			limit: Math.max(1, Math.min(30, Number(filters.value.limit) || 8)),
		};
		const [summaryResp, queuesResp] = await Promise.all([
			api.get('/admin-widgets/content-ops/summary', { params }),
			api.get('/admin-widgets/content-ops/queues', { params }),
		]);

		const counters = summaryResp?.data?.counters || {};
		summary.value = {
			users: counters.users || { total: 0, blocked: 0, blocked_recent: 0, expiring_soon: 0 },
			payments: counters.payments || { total: 0, failed: 0, total_amount: 0 },
			promo: counters.promo || { active_codes: 0, usages_7d: 0 },
			family: counters.family || { members: 0, active_invites: 0 },
			errors: counters.errors || { new: 0, in_progress: 0 },
			partners: counters.partners || { pending_withdrawals: 0 },
		};

		const queueData = queuesResp?.data?.queues || {};
		queues.value = {
			users_expiring: Array.isArray(queueData.users_expiring) ? queueData.users_expiring : [],
			users_blocked_recent: Array.isArray(queueData.users_blocked_recent) ? queueData.users_blocked_recent : [],
			users_top_balance: Array.isArray(queueData.users_top_balance) ? queueData.users_top_balance : [],
			payments_recent: Array.isArray(queueData.payments_recent) ? queueData.payments_recent : [],
			promo_usages_recent: Array.isArray(queueData.promo_usages_recent) ? queueData.promo_usages_recent : [],
			errors_new: Array.isArray(queueData.errors_new) ? queueData.errors_new : [],
			partner_withdrawals_pending: Array.isArray(queueData.partner_withdrawals_pending) ? queueData.partner_withdrawals_pending : [],
		};

		lastUpdated.value = new Date().toISOString();
	} catch (e) {
		error.value = 'Не удалось загрузить данные Контент Ops. Проверь /admin-widgets/content-ops/* и права роли.';
	} finally {
		loading.value = false;
	}
}

async function searchGlobal() {
	const query = String(search.value || '').trim();
	if (!query) {
		searchResults.value = { users: [], payments: [], promo_codes: [] };
		searchLoading.value = false;
		return;
	}
	const reqId = ++searchRequestId;
	searchLoading.value = true;
	try {
		const resp = await api.get('/admin-widgets/content-ops/search', {
			params: {
				q: query,
				limit: 8,
			},
		});
		if (reqId !== searchRequestId) return;
		const rows = resp?.data?.results || {};
		searchResults.value = {
			users: Array.isArray(rows.users) ? rows.users : [],
			payments: Array.isArray(rows.payments) ? rows.payments : [],
			promo_codes: Array.isArray(rows.promo_codes) ? rows.promo_codes : [],
		};
	} catch (_err) {
		if (reqId !== searchRequestId) return;
		searchResults.value = { users: [], payments: [], promo_codes: [] };
	} finally {
		if (reqId === searchRequestId) {
			searchLoading.value = false;
		}
	}
}

watch(
	() => search.value,
	() => {
		if (searchTimer) {
			clearTimeout(searchTimer);
			searchTimer = null;
		}
		searchTimer = setTimeout(() => {
			searchGlobal();
		}, 240);
	},
	{ immediate: true }
);

onBeforeUnmount(() => {
	if (searchTimer) {
		clearTimeout(searchTimer);
		searchTimer = null;
	}
	searchRequestId += 1;
});

onMounted(() => {
	refresh();
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
		radial-gradient(circle at 2% -10%, rgba(34, 211, 238, 0.22), transparent 36%),
		radial-gradient(circle at 105% 2%, rgba(16, 185, 129, 0.16), transparent 40%),
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
.nav__section { margin-bottom: 12px; display: grid; gap: 4px; }
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

.nav--premium { display: grid; gap: 10px; }

.nav__brand {
	display: grid;
	grid-template-columns: auto 1fr;
	gap: 10px;
	align-items: center;
	padding: 10px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: linear-gradient(135deg, rgba(34, 211, 238, 0.16), rgba(16, 185, 129, 0.08));
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

.nav__brand-title { font-size: 13px; font-weight: 700; }
.nav__brand-subtitle { font-size: 11px; opacity: 0.7; }

.hero {
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(320px, 420px);
	gap: 14px;
	padding: 16px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: linear-gradient(140deg, rgba(34, 211, 238, 0.14), rgba(16, 185, 129, 0.10));
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

.hero__filters {
	display: grid;
	grid-template-columns: repeat(3, minmax(0, 1fr));
	gap: 8px;
}

.field {
	display: grid;
	gap: 6px;
	font-size: 12px;
}
.field--compact { gap: 5px; }

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
	outline: 2px solid rgba(34, 211, 238, 0.46);
	outline-offset: 1px;
}

.input--num { padding: 9px 8px; }

.search-results {
	display: grid;
	gap: 8px;
	padding: 12px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: rgba(2, 8, 23, 0.34);
}

.search-results__head {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	font-size: 12px;
	opacity: 0.85;
}

.search-results__grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
	gap: 8px;
}

.search-results__item {
	display: grid;
	gap: 3px;
	padding: 10px;
	border-radius: 10px;
	border: 1px solid rgba(34, 211, 238, 0.28);
	background: linear-gradient(150deg, rgba(34, 211, 238, 0.10), rgba(255, 255, 255, 0.03));
	text-decoration: none;
	color: inherit;
}

.search-results__item:hover {
	background: linear-gradient(150deg, rgba(34, 211, 238, 0.16), rgba(255, 255, 255, 0.05));
}

.search-results__main { font-weight: 700; font-size: 13px; }
.search-results__meta { font-size: 12px; opacity: 0.78; }

.kpi-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
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
	flex-shrink: 0;
}

.kpi__icon--blue { background: rgba(59, 130, 246, 0.18); color: #93c5fd; }
.kpi__icon--green { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
.kpi__icon--amber { background: rgba(245, 158, 11, 0.20); color: #fbd38d; }
.kpi__icon--cyan { background: rgba(34, 211, 238, 0.20); color: #67e8f9; }
.kpi__icon--red { background: rgba(239, 68, 68, 0.18); color: #fca5a5; }
.kpi__icon--purple { background: rgba(139, 92, 246, 0.20); color: #c4b5fd; }

.kpi__content { min-width: 0; }
.kpi__label { font-size: 12px; opacity: 0.78; text-transform: uppercase; letter-spacing: 0.04em; }
.kpi__value { margin-top: 2px; font-size: 22px; font-weight: 800; line-height: 1.1; }
.kpi__meta { margin-top: 3px; font-size: 11px; opacity: 0.78; line-height: 1.4; }

.kpi__skel {
	margin-top: 4px;
	height: 22px;
	display: flex;
	align-items: center;
}

.kpi__skel span {
	display: block;
	width: 70%;
	height: 14px;
	border-radius: 6px;
	background: linear-gradient(90deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.18), rgba(255, 255, 255, 0.08));
	background-size: 200% 100%;
	animation: skel-shimmer 1.4s linear infinite;
}

@keyframes skel-shimmer {
	from { background-position: 200% 0; }
	to { background-position: -200% 0; }
}

.queues {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
	gap: 12px;
}

.panel {
	padding: 14px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: rgba(255, 255, 255, 0.03);
	display: grid;
	gap: 10px;
}

.panel__header {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 12px;
}

.panel__title {
	font-weight: 700;
	font-size: 15px;
}

.panel__subtitle {
	margin-top: 4px;
	font-size: 12px;
	opacity: 0.74;
}

.panel__count {
	display: inline-flex;
	align-items: center;
	border-radius: 999px;
	padding: 3px 9px;
	font-size: 11px;
	font-weight: 700;
	border: 1px solid rgba(34, 211, 238, 0.4);
	background: rgba(34, 211, 238, 0.14);
	color: #67e8f9;
}

.rows { display: grid; gap: 6px; }

.row {
	display: grid;
	gap: 2px;
	padding: 9px 10px;
	border-radius: 10px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: rgba(2, 8, 23, 0.28);
	text-decoration: none;
	color: inherit;
	min-height: 44px;
}
.row:hover { background: rgba(34, 211, 238, 0.07); border-color: rgba(34, 211, 238, 0.24); }

.row--warn { border-color: rgba(245, 158, 11, 0.35); background: rgba(245, 158, 11, 0.06); }
.row--good { border-color: rgba(16, 185, 129, 0.35); background: rgba(16, 185, 129, 0.06); }
.row--bad { border-color: rgba(239, 68, 68, 0.4); background: rgba(239, 68, 68, 0.07); }

.row__title { font-weight: 700; font-size: 13px; }
.row__meta { font-size: 12px; opacity: 0.78; }

.row-skel {
	display: grid;
	gap: 4px;
	padding: 9px 10px;
	border-radius: 10px;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(2, 8, 23, 0.28);
}

.row-skel span {
	display: block;
	height: 10px;
	border-radius: 6px;
	background: linear-gradient(90deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.06));
	background-size: 200% 100%;
	animation: skel-shimmer 1.4s linear infinite;
}

.row-skel span:first-child { width: 62%; }
.row-skel span:nth-child(2) { width: 38%; }

.rows-empty {
	display: flex;
	gap: 10px;
	align-items: center;
	padding: 14px;
	border-radius: 10px;
	border: 1px dashed rgba(16, 185, 129, 0.32);
	background: rgba(16, 185, 129, 0.06);
	font-size: 13px;
	color: #a7f3d0;
}

.launch {
	padding: 14px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: rgba(255, 255, 255, 0.03);
	display: grid;
	gap: 10px;
}

.launch__grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
	gap: 10px;
}

.launch__item {
	display: grid;
	gap: 6px;
	padding: 12px;
	border-radius: 12px;
	border: 1px solid rgba(34, 211, 238, 0.24);
	background: linear-gradient(150deg, rgba(34, 211, 238, 0.08), rgba(255, 255, 255, 0.02));
	text-decoration: none;
	color: inherit;
	min-height: 96px;
}

.launch__item:hover {
	background: linear-gradient(150deg, rgba(34, 211, 238, 0.16), rgba(255, 255, 255, 0.05));
	border-color: rgba(34, 211, 238, 0.45);
}

.launch__head {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	font-weight: 700;
	font-size: 14px;
}

.launch__desc {
	font-size: 12px;
	opacity: 0.82;
	line-height: 1.4;
}

.launch__path {
	font-size: 11px;
	opacity: 0.62;
	font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

@media (max-width: 1100px) {
	.hero { grid-template-columns: 1fr; }
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

	.hero__filters { grid-template-columns: 1fr 1fr 1fr; }
	.kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	.queues { grid-template-columns: 1fr; }
	.launch__grid { grid-template-columns: 1fr; }
}

@media (max-width: 480px) {
	.page { padding: 8px; gap: 8px; overflow-x: hidden; }
	.hero { padding: 12px; }
	.hero__title { font-size: 18px; line-height: 1.25; }
	.hero__filters { grid-template-columns: 1fr 1fr; }
	.kpi-grid { grid-template-columns: 1fr; }
	.search-results__grid { grid-template-columns: 1fr; }

	.nav--premium { flex-direction: column; overflow: visible; }
	.nav--premium .nav__brand,
	.nav--premium .nav__section { min-width: 0; width: 100%; }
	.nav--premium .nav__item { min-height: 44px; width: 100%; }

	input, select, textarea { min-height: 44px; font-size: 16px; }
	:deep(.v-button) { min-height: 44px; }
}

@media (max-width: 360px) {
	.page { padding: 6px; }
	.hero__filters { grid-template-columns: 1fr; }
}
</style>
