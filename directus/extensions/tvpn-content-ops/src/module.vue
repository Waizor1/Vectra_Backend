<template>
	<private-view title="Контент Ops">
		<template #navigation>
			<div class="nav">
				<div class="nav__section-title">Контент Ops</div>
				<router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-content-ops' }">
					<v-icon name="hub" />
					<span>Операционный центр</span>
				</router-link>
				<router-link class="nav__item" :to="{ path: '/tvpn-home' }">
					<v-icon name="home" />
					<span>Главная</span>
				</router-link>
				<router-link class="nav__item" :to="{ path: '/content/users' }">
					<v-icon name="people" />
					<span>Пользователи</span>
				</router-link>
				<router-link class="nav__item" :to="{ path: '/insights' }">
					<v-icon name="timeline" />
					<span>Аналитика</span>
				</router-link>
			</div>
		</template>

		<template #actions>
			<v-button secondary :loading="loading" @click="refresh">
				<v-icon name="refresh" left />
				Обновить
			</v-button>
		</template>

		<div class="ops-page">
			<div class="hero panel-base">
				<div class="hero__title">Контент Ops</div>
				<div class="hero__subtitle">
					Операционный слой для быстрых решений: KPI, очереди, быстрый поиск и переходы в карточки.
				</div>
				<div class="hero__toolbar">
					<div class="input-wrap">
						<input v-model.trim="search" class="input" placeholder="Поиск: user id / username / payment id / promo" />
					</div>
					<div class="toolbar-numbers">
						<label>
							<span>Истекает, дн</span>
							<input v-model.number="filters.expiring_days" class="input input--num" type="number" min="1" max="90" />
						</label>
						<label>
							<span>Блок, дн</span>
							<input v-model.number="filters.blocked_days" class="input input--num" type="number" min="1" max="30" />
						</label>
						<label>
							<span>Лимит</span>
							<input v-model.number="filters.limit" class="input input--num" type="number" min="1" max="30" />
						</label>
					</div>
				</div>
				<div v-if="searchLoading" class="quick-search__state">Ищем по users / payments / promo…</div>
				<div v-else-if="quickMatches.length" class="quick-search">
					<router-link v-for="item in quickMatches" :key="item.key" class="quick-search__item" :to="item.path">
						<div class="quick-search__main">{{ item.title }}</div>
						<div class="quick-search__meta">{{ item.meta }}</div>
					</router-link>
				</div>
				<div v-else-if="hasSearchQuery" class="quick-search__state">Совпадений не найдено</div>
				<div class="hero__meta">Последнее обновление: {{ lastUpdatedLabel }}</div>
			</div>

			<v-notice v-if="error" type="danger">{{ error }}</v-notice>

			<div class="kpi-grid">
				<v-card class="kpi panel-base">
					<div class="kpi__label">Users</div>
					<div class="kpi__value">{{ fmt(summary.users.total) }}</div>
					<div class="kpi__meta">blocked {{ fmt(summary.users.blocked) }} · recent {{ fmt(summary.users.blocked_recent) }} · exp {{ fmt(summary.users.expiring_soon) }}</div>
				</v-card>
				<v-card class="kpi panel-base">
					<div class="kpi__label">Payments</div>
					<div class="kpi__value">{{ fmt(summary.payments.total) }}</div>
					<div class="kpi__meta">failed {{ fmt(summary.payments.failed) }} · sum {{ fmtMoney(summary.payments.total_amount) }}</div>
				</v-card>
				<v-card class="kpi panel-base">
					<div class="kpi__label">Promo</div>
					<div class="kpi__value">{{ fmt(summary.promo.active_codes) }}</div>
					<div class="kpi__meta">usages 7d {{ fmt(summary.promo.usages_7d) }}</div>
				</v-card>
				<v-card class="kpi panel-base">
					<div class="kpi__label">Family</div>
					<div class="kpi__value">{{ fmt(summary.family.members) }}</div>
					<div class="kpi__meta">active invites {{ fmt(summary.family.active_invites) }}</div>
				</v-card>
				<v-card class="kpi panel-base">
					<div class="kpi__label">Errors</div>
					<div class="kpi__value">{{ fmt(summary.errors.new) }}</div>
					<div class="kpi__meta">in progress {{ fmt(summary.errors.in_progress) }}</div>
				</v-card>
				<v-card class="kpi panel-base">
					<div class="kpi__label">Partners</div>
					<div class="kpi__value">{{ fmt(summary.partners.pending_withdrawals) }}</div>
					<div class="kpi__meta">pending withdrawals</div>
				</v-card>
			</div>

			<div class="queues">
				<v-card class="panel panel-base">
					<div class="panel__title">Users: expiring / blocked / balance</div>
					<div class="rows">
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
							<span class="row__meta">{{ fmtMoney(row.balance) }}</span>
						</router-link>
						<div v-if="!queues.users_expiring.length && !queues.users_blocked_recent.length && !queues.users_top_balance.length" class="rows__empty">
							Нет данных
						</div>
					</div>
				</v-card>

				<v-card class="panel panel-base">
					<div class="panel__title">Payments / Promo / Errors / Partners</div>
					<div class="rows">
						<router-link v-for="row in queues.payments_recent" :key="`pay-${row.id}`" class="row" :to="paymentPath(row.id)">
							<span class="row__title">payment {{ row.payment_id || row.id }}</span>
							<span class="row__meta">{{ fmtMoney(row.amount) }} · {{ row.status }} · {{ formatDateTime(row.processed_at) }}</span>
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
							<span class="row__meta">owner {{ row.owner_id }} · {{ fmtMoney(row.amount_rub) }}</span>
						</router-link>
						<div v-if="!queues.payments_recent.length && !queues.promo_usages_recent.length && !queues.errors_new.length && !queues.partner_withdrawals_pending.length" class="rows__empty">
							Нет данных
						</div>
					</div>
				</v-card>
			</div>

			<v-card class="launch panel-base">
				<div class="panel__title">Launch Grid</div>
				<div class="launch__grid">
					<router-link v-for="item in launchGrid" :key="item.path" class="launch__item" :to="{ path: item.path }">
						<div class="launch__title">{{ item.title }}</div>
						<div class="launch__desc">{{ item.desc }}</div>
					</router-link>
				</div>
			</v-card>
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
	{ path: '/content/users', title: 'Пользователи', desc: 'Профили, лимиты, блокировки, быстрые решения' },
	{ path: '/content/active_tariffs', title: 'Активные тарифы', desc: 'LTE usage, устройства и стоимость периода' },
	{ path: '/content/tariffs', title: 'Тарифы', desc: 'Цены, family-варианты, логику витрины' },
	{ path: '/content/processed_payments', title: 'Платежи', desc: 'Крупные/проблемные операции и статусы' },
	{ path: '/content/promo_codes', title: 'Промокоды', desc: 'Активные, истекающие и отключенные коды' },
	{ path: '/content/promo_usages', title: 'Promo usage', desc: 'Последние применения с пользователями' },
	{ path: '/content/error_reports', title: 'Ошибки', desc: 'Новые кейсы и triage-очередь' },
	{ path: '/content/partner_withdrawals', title: 'Партнерка', desc: 'Заявки на вывод и pending queue' },
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

const lastUpdatedLabel = computed(() => {
	if (!lastUpdated.value) return '—';
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
.ops-page {
	--ops-bg-start: rgba(15, 23, 42, 0.78);
	--ops-bg-end: rgba(3, 7, 18, 0.9);
	--ops-border: rgba(148, 163, 184, 0.22);
	--ops-cyan: #06b6d4;
	--ops-amber: #f59e0b;
	--ops-red: #ef4444;
	--ops-green: #10b981;
	display: grid;
	gap: 12px;
	padding: 14px 18px;
}

.panel-base {
	background: linear-gradient(165deg, var(--ops-bg-start), var(--ops-bg-end));
	border: 1px solid var(--ops-border);
	border-radius: 14px;
	animation: panel-enter 0.34s ease both;
}

@keyframes panel-enter {
	from {
		opacity: 0;
		transform: translateY(6px);
	}
	to {
		opacity: 1;
		transform: translateY(0);
	}
}

.hero {
	padding: 16px;
}

.hero__title {
	font-size: 22px;
	font-weight: 800;
}

.hero__subtitle {
	margin-top: 6px;
	opacity: 0.82;
	max-width: 920px;
}

.hero__toolbar {
	margin-top: 12px;
	display: grid;
	grid-template-columns: minmax(280px, 1fr) auto;
	gap: 12px;
	align-items: end;
}

.input-wrap {
	width: 100%;
}

.toolbar-numbers {
	display: flex;
	gap: 10px;
	flex-wrap: wrap;
}

.toolbar-numbers label {
	display: grid;
	gap: 4px;
	font-size: 12px;
	opacity: 0.85;
}

.input {
	width: 100%;
	padding: 9px 12px;
	border-radius: 10px;
	border: 1px solid rgba(148, 163, 184, 0.35);
	background: rgba(15, 23, 42, 0.65);
	color: inherit;
}

.input--num {
	width: 110px;
}

.quick-search {
	margin-top: 10px;
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
	gap: 8px;
}

.quick-search__state {
	margin-top: 10px;
	padding: 9px 10px;
	border-radius: 10px;
	border: 1px dashed rgba(148, 163, 184, 0.35);
	font-size: 12px;
	opacity: 0.85;
}

.quick-search__item {
	padding: 9px 10px;
	border-radius: 10px;
	border: 1px solid rgba(6, 182, 212, 0.26);
	background: rgba(8, 47, 73, 0.34);
	text-decoration: none;
	color: inherit;
}

.quick-search__main {
	font-weight: 700;
}

.quick-search__meta {
	margin-top: 2px;
	font-size: 12px;
	opacity: 0.75;
}

.hero__meta {
	margin-top: 10px;
	font-size: 12px;
	opacity: 0.76;
}

.kpi-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
	gap: 10px;
}

.kpi {
	padding: 12px;
}

.kpi__label {
	font-size: 12px;
	opacity: 0.7;
	text-transform: uppercase;
	letter-spacing: 0.04em;
}

.kpi__value {
	margin-top: 4px;
	font-size: 24px;
	font-weight: 850;
	color: var(--ops-cyan);
}

.kpi__meta {
	margin-top: 4px;
	font-size: 12px;
	opacity: 0.82;
}

.queues {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
	gap: 10px;
}

.panel {
	padding: 12px;
}

.panel__title {
	font-weight: 760;
	margin-bottom: 10px;
}

.rows {
	display: grid;
	gap: 7px;
}

.row {
	display: grid;
	gap: 2px;
	padding: 9px 10px;
	border-radius: 10px;
	border: 1px solid rgba(148, 163, 184, 0.2);
	background: rgba(15, 23, 42, 0.62);
	text-decoration: none;
	color: inherit;
}

.row--warn {
	border-color: rgba(245, 158, 11, 0.35);
}

.row--good {
	border-color: rgba(16, 185, 129, 0.35);
}

.row--bad {
	border-color: rgba(239, 68, 68, 0.35);
}

.row__title {
	font-weight: 700;
}

.row__meta {
	font-size: 12px;
	opacity: 0.76;
}

.rows__empty {
	padding: 8px 10px;
	font-size: 12px;
	opacity: 0.7;
}

.launch {
	padding: 12px;
}

.launch__grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
	gap: 9px;
}

.launch__item {
	display: grid;
	gap: 3px;
	padding: 10px;
	border-radius: 10px;
	border: 1px solid rgba(6, 182, 212, 0.25);
	background: rgba(8, 47, 73, 0.3);
	text-decoration: none;
	color: inherit;
}

.launch__title {
	font-weight: 720;
}

.launch__desc {
	font-size: 12px;
	opacity: 0.76;
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
	background: rgba(6, 182, 212, 0.1);
}

.nav__item--active {
	background: rgba(6, 182, 212, 0.18);
}

@media (max-width: 1100px) {
	.hero__toolbar {
		grid-template-columns: 1fr;
	}
}

@media (max-width: 720px) {
	.ops-page {
		padding: 12px;
	}

	.kpi-grid,
	.queues,
	.launch__grid,
	.quick-search {
		grid-template-columns: 1fr;
	}

	.toolbar-numbers {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	.input--num {
		width: 100%;
	}
}
</style>
