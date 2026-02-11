<template>
	<private-view title="Главная">
		<template #navigation>
			<div class="nav">
				<div class="nav__section">
					<div class="nav__section-title">Обзор</div>
					<router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-home' }">
						<v-icon name="space_dashboard" />
						<span>Главная</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/insights' }">
						<v-icon name="timeline" />
						<span>Аналитика</span>
					</router-link>
				</div>

				<div class="nav__section">
					<div class="nav__section-title">Управление</div>
					<router-link class="nav__item" :to="{ path: '/content/users' }">
						<v-icon name="people" />
						<span>Пользователи</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/active_tariffs' }">
						<v-icon name="subscriptions" />
						<span>Активные тарифы</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/tariffs' }">
						<v-icon name="sell" />
						<span>Тарифы</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/processed_payments' }">
						<v-icon name="payments" />
						<span>Платежи</span>
					</router-link>
				</div>

				<div class="nav__section">
					<div class="nav__section-title">Промо и призы</div>
					<router-link class="nav__item" :to="{ path: '/content/promo_codes' }">
						<v-icon name="confirmation_number" />
						<span>Промокоды</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/promo_batches' }">
						<v-icon name="inventory_2" />
						<span>Партии промокодов</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/prize_wheel_config' }">
						<v-icon name="casino" />
						<span>Настройки призов</span>
					</router-link>
					<router-link class="nav__item" :to="{ path: '/content/prize_wheel_history' }">
						<v-icon name="history_toggle_off" />
						<span>История призов</span>
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
			<div class="hero">
				<div class="hero__left">
					<div class="hero__title">TVPN Admin</div>
					<div class="hero__subtitle">
						Быстрый обзор состояния проекта, метрики и быстрые переходы по ключевым разделам.
					</div>
				</div>
				<div class="hero__right">
					<div class="hero__meta">
						<div class="hero__meta-label">Последнее обновление</div>
						<div class="hero__meta-value">{{ lastUpdatedLabel }}</div>
					</div>
				</div>
			</div>

			<v-notice v-if="error" type="danger">
				{{ error }}
			</v-notice>

			<v-notice v-if="alerts.length" type="warning">
				<div class="alerts">
					<div v-for="a in alerts" :key="a.key" class="alerts__item">
						<v-icon :name="a.icon" />
						<span>{{ a.text }}</span>
					</div>
				</div>
			</v-notice>

			<div class="grid">
				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--blue">
							<v-icon name="people" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Всего пользователей</div>
							<div class="kpi__value">{{ fmt(stats.totalUsers) }}</div>
						</div>
					</div>
				</v-card>

				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--green">
							<v-icon name="subscriptions" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Активных тарифов</div>
							<div class="kpi__value">{{ fmt(stats.activeTariffs) }}</div>
						</div>
					</div>
				</v-card>

				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--amber">
							<v-icon name="block" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Заблокировано</div>
							<div class="kpi__value">{{ fmt(stats.blockedUsers) }}</div>
						</div>
					</div>
				</v-card>

				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--purple">
							<v-icon name="payments" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Платежей (всего)</div>
							<div class="kpi__value">{{ fmt(stats.processedPayments) }}</div>
						</div>
					</div>
				</v-card>
			</div>

			<div class="grid grid--secondary">
				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--green">
							<v-icon name="wifi" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Подключения за 7 дней</div>
							<div class="kpi__value">{{ fmt(stats.connections7d) }}</div>
						</div>
					</div>
				</v-card>

				<v-card class="kpi">
					<div class="kpi__row">
						<div class="kpi__icon kpi__icon--blue">
							<v-icon name="person_add" />
						</div>
						<div class="kpi__content">
							<div class="kpi__label">Регистрации за 7 дней</div>
							<div class="kpi__value">{{ fmt(stats.registrations7d) }}</div>
						</div>
					</div>
				</v-card>
			</div>

			<v-card class="panel panel--spaced">
				<div class="panel__title">Динамика (30 дней)</div>
				<div class="panel__subtitle">Спарклайны и быстрые суммы по ключевым событиям.</div>

				<div class="trends">
					<div class="trend">
						<div class="trend__head">
							<div class="trend__label">
								<v-icon name="wifi" />
								<span>Подключения</span>
							</div>
							<div class="trend__meta">
								<span class="trend__meta-label">7д:</span>
								<span class="trend__meta-value">{{ fmt(stats.connections7d) }}</span>
								<span class="trend__meta-label">Сегодня:</span>
								<span class="trend__meta-value">{{ fmt(trends.connectionsToday) }}</span>
							</div>
						</div>
						<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
							<polyline class="spark__line" :points="sparkPoints(trends.connections30d)" />
						</svg>
					</div>

					<div class="trend">
						<div class="trend__head">
							<div class="trend__label">
								<v-icon name="person_add" />
								<span>Регистрации</span>
							</div>
							<div class="trend__meta">
								<span class="trend__meta-label">7д:</span>
								<span class="trend__meta-value">{{ fmt(stats.registrations7d) }}</span>
								<span class="trend__meta-label">Сегодня:</span>
								<span class="trend__meta-value">{{ fmt(trends.registrationsToday) }}</span>
							</div>
						</div>
						<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
							<polyline class="spark__line spark__line--blue" :points="sparkPoints(trends.registrations30d)" />
						</svg>
					</div>

					<div class="trend">
						<div class="trend__head">
							<div class="trend__label">
								<v-icon name="person" />
								<span>Активные пользователи</span>
							</div>
							<div class="trend__meta">
								<span class="trend__meta-label">Сегодня:</span>
								<span class="trend__meta-value">{{ fmt(trends.activeUsersToday) }}</span>
							</div>
						</div>
						<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
							<polyline class="spark__line spark__line--green" :points="sparkPoints(trends.activeUsers30d)" />
						</svg>
					</div>

					<div class="trend">
						<div class="trend__head">
							<div class="trend__label">
								<v-icon name="group" />
								<span>Всего пользователей</span>
							</div>
							<div class="trend__meta">
								<span class="trend__meta-label">Сегодня:</span>
								<span class="trend__meta-value">{{ fmt(trends.totalUsersToday) }}</span>
							</div>
						</div>
						<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
							<polyline class="spark__line spark__line--purple" :points="sparkPoints(trends.totalUsers30d)" />
						</svg>
					</div>
				</div>
			</v-card>

			<v-card class="panel panel--spaced">
				<div class="panel__title">События</div>
				<div class="panel__subtitle">Последние изменения, чтобы видеть жизнь проекта.</div>

				<div class="events">
					<div class="events__col">
						<div class="events__title">
							<v-icon name="people" />
							<span>Новые пользователи</span>
							<router-link class="events__all" :to="{ path: '/content/users' }">все</router-link>
						</div>
						<div v-if="events.users.length" class="events__list">
							<router-link v-for="u in events.users" :key="u.id" class="event" :to="{ path: `/content/users/${u.id}` }">
								<div class="event__main">
									<div class="event__title">{{ u.username || u.full_name || u.id }}</div>
									<div class="event__meta">
										<span v-if="u.is_blocked" class="pill pill--bad">blocked</span>
										<span v-if="u.expired_at" class="pill">exp {{ shortDate(u.expired_at) }}</span>
									</div>
								</div>
								<div class="event__time">{{ fromNow(u.registration_date) }}</div>
							</router-link>
						</div>
						<div v-else class="events__empty">Нет данных</div>
					</div>

					<div class="events__col">
						<div class="events__title">
							<v-icon name="payments" />
							<span>Платежи</span>
							<router-link class="events__all" :to="{ path: '/content/processed_payments' }">все</router-link>
						</div>
						<div v-if="events.payments.length" class="events__list">
							<router-link
								v-for="p in events.payments"
								:key="p.id"
								class="event"
								:to="{ path: `/content/processed_payments/${p.id}` }"
							>
								<div class="event__main">
									<div class="event__title">#{{ p.payment_id || p.id }}</div>
									<div class="event__meta">
										<span class="pill">{{ fmtMoney(p.amount) }}</span>
										<span v-if="p.status" class="pill">{{ String(p.status) }}</span>
									</div>
								</div>
								<div class="event__time">{{ fromNow(p.processed_at) }}</div>
							</router-link>
						</div>
						<div v-else class="events__empty">Нет данных</div>
					</div>

					<div class="events__col">
						<div class="events__title">
							<v-icon name="confirmation_number" />
							<span>Промо-активность</span>
							<router-link class="events__all" :to="{ path: '/content/promo_usages' }">все</router-link>
						</div>
						<div v-if="events.promo.length" class="events__list">
							<router-link v-for="p in events.promo" :key="p.id" class="event" :to="{ path: `/content/promo_usages/${p.id}` }">
								<div class="event__main">
									<div class="event__title">Promo usage</div>
									<div class="event__meta">
										<span v-if="p.user_id" class="pill">user {{ p.user_id }}</span>
										<span v-if="p.promo_code_id" class="pill">code {{ p.promo_code_id }}</span>
									</div>
								</div>
								<div class="event__time">{{ fromNow(p.used_at) }}</div>
							</router-link>
						</div>
						<div v-else class="events__empty">Нет данных</div>
					</div>
				</div>
			</v-card>

			<div class="layout">
				<v-card class="panel">
					<div class="panel__title">Быстрые действия</div>
					<div class="panel__subtitle">Частые операции, чтобы не “копать” меню.</div>

					<div class="actions">
						<router-link class="action" :to="{ path: '/content/users' }">
							<v-icon name="manage_accounts" />
							<div>
								<div class="action__title">Найти пользователя</div>
								<div class="action__desc">Поиск, лимиты, подписка, блокировки</div>
							</div>
						</router-link>

						<router-link class="action" :to="{ path: '/content/promo_codes' }">
							<v-icon name="confirmation_number" />
							<div>
								<div class="action__title">Создать промокод</div>
								<div class="action__desc">Код будет преобразован в HMAC хуком</div>
							</div>
						</router-link>

						<router-link class="action" :to="{ path: '/content/prize_wheel_config' }">
							<v-icon name="casino" />
							<div>
								<div class="action__title">Настроить колесо призов</div>
								<div class="action__desc">Валидации вероятностей включены</div>
							</div>
						</router-link>

						<router-link class="action" :to="{ path: '/insights' }">
							<v-icon name="timeline" />
							<div>
								<div class="action__title">Открыть дашборд</div>
								<div class="action__desc">Графики регистраций и подключений</div>
							</div>
						</router-link>
					</div>
				</v-card>

				<v-card class="panel">
					<div class="panel__title">Здоровье админки</div>
					<div class="panel__subtitle">Проверки, чтобы не словить “пустую админку”.</div>

					<div class="health">
						<div class="health__row">
							<div class="health__label">Доступ к данным</div>
							<div class="health__value">
								<v-chip :class="statsOk ? 'chip--ok' : 'chip--bad'">
									{{ statsOk ? 'OK' : 'Проблема' }}
								</v-chip>
							</div>
						</div>
						<div class="health__row">
							<div class="health__label">Extensions (admin-widgets)</div>
							<div class="health__value">
								<v-chip :class="widgetsOk ? 'chip--ok' : 'chip--bad'">
									{{ widgetsOk ? 'OK' : 'Нет данных' }}
								</v-chip>
							</div>
						</div>
						<div class="health__hint">
							Если внезапно пропали разделы/меню — запусти `scripts/directus_super_setup.py`.
						</div>
					</div>
				</v-card>
			</div>
		</div>
	</private-view>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useApi } from '@directus/extensions-sdk';

const api = useApi();

const loading = ref(false);
const error = ref('');
const lastUpdated = ref(null);

const trends = ref({
	connections30d: [],
	registrations30d: [],
	activeUsers30d: [],
	totalUsers30d: [],
	connectionsToday: null,
	registrationsToday: null,
	activeUsersToday: null,
	totalUsersToday: null,
});

const events = ref({
	users: [],
	payments: [],
	promo: [],
});

const stats = ref({
	totalUsers: null,
	activeTariffs: null,
	blockedUsers: null,
	processedPayments: null,
	connections7d: null,
	registrations7d: null,
});

const statsOk = computed(() => Object.values(stats.value).some((v) => typeof v === 'number'));
const widgetsOk = ref(false);

const alerts = computed(() => {
	const out = [];

	if (!widgetsOk.value) {
		out.push({
			key: 'widgets',
			icon: 'warning',
			text: 'Виджеты /admin-widgets не отдают данные. Проверь extensions и права.',
		});
	}

	const c = trends.value.connections30d || [];
	if (c.length >= 2 && Number(c[c.length - 1]) === 0 && Number(c[c.length - 2]) === 0) {
		out.push({
			key: 'connections-zero',
			icon: 'wifi_off',
			text: 'Подключений нет 2 дня подряд (возможно, данные не пишутся или упал сбор статистики).',
		});
	}

	const reg = trends.value.registrations30d || [];
	if (reg.length >= 8) {
		const today = Number(reg[reg.length - 1]);
		const prev7 = reg.slice(reg.length - 8, reg.length - 1).map((x) => Number(x)).filter((x) => Number.isFinite(x));
		const avg = prev7.length ? prev7.reduce((a, b) => a + b, 0) / prev7.length : 0;
		if (Number.isFinite(today) && avg > 0 && today >= avg * 3) {
			out.push({
				key: 'registrations-spike',
				icon: 'trending_up',
				text: `Всплеск регистраций: сегодня ${today}, среднее за прошлые 7 дней ${avg.toFixed(1)}.`,
			});
		}
	}

	return out;
});

const lastUpdatedLabel = computed(() => {
	if (!lastUpdated.value) return '—';
	try {
		return new Date(lastUpdated.value).toLocaleString('ru-RU');
	} catch {
		return String(lastUpdated.value);
	}
});

function fmt(value) {
	if (value === null || value === undefined) return '—';
	if (typeof value !== 'number') return '—';
	return value.toLocaleString('ru-RU');
}

function fmtMoney(value) {
	const n = Number(value);
	if (!Number.isFinite(n)) return '—';
	// Currency may differ by provider; keep it generic.
	return `${n.toLocaleString('ru-RU')} ₽`;
}

function shortDate(value) {
	if (!value) return '';
	try {
		const d = new Date(value);
		return d.toLocaleDateString('ru-RU');
	} catch {
		return String(value);
	}
}

function fromNow(value) {
	if (!value) return '—';
	let d;
	try {
		d = new Date(value);
	} catch {
		return '—';
	}
	const diff = Date.now() - d.getTime();
	if (!Number.isFinite(diff)) return '—';
	const sec = Math.floor(diff / 1000);
	if (sec < 60) return `${sec}s`;
	const min = Math.floor(sec / 60);
	if (min < 60) return `${min}m`;
	const hr = Math.floor(min / 60);
	if (hr < 48) return `${hr}h`;
	const day = Math.floor(hr / 24);
	return `${day}d`;
}

function sparkPoints(values) {
	if (!Array.isArray(values) || values.length < 2) return '';
	const nums = values.map((v) => Number(v)).filter((v) => Number.isFinite(v));
	if (nums.length < 2) return '';
	const min = Math.min(...nums);
	const max = Math.max(...nums);
	const dx = 100 / (nums.length - 1);
	const denom = max - min || 1;
	const points = nums.map((v, i) => {
		const x = i * dx;
		const y = 28 - ((v - min) / denom) * 26;
		return `${x.toFixed(2)},${y.toFixed(2)}`;
	});
	return points.join(' ');
}

async function fetchCount(collection, params = {}) {
	const res = await api.get(`/items/${collection}`, {
		params: { 'aggregate[count]': 'id', ...params },
	});
	const row = Array.isArray(res?.data?.data) ? res.data.data[0] : null;
	const raw = row?.count?.id ?? row?.count;
	const n = Number(raw);
	return Number.isFinite(n) ? n : null;
}

async function checkWidgets() {
	try {
		await api.get('/admin-widgets/total-users', { params: { period: 'day' } });
		widgetsOk.value = true;
	} catch {
		widgetsOk.value = false;
	}
}

async function fetchWidgetSeries(endpoint, n = 30) {
	const res = await api.get(`/admin-widgets/${endpoint}`, { params: { period: 'day' } });
	const results = Array.isArray(res?.data?.results) ? res.data.results : [];
	const tail = results.slice(Math.max(0, results.length - n));
	const series = tail.map((row) => {
		const v = Number(row?.count);
		return Number.isFinite(v) ? v : 0;
	});
	const today = series.length ? series[series.length - 1] : null;
	return { series, today };
}

async function fetchWidgetSumLastN(endpoint, n = 7) {
	const res = await api.get(`/admin-widgets/${endpoint}`, { params: { period: 'day' } });
	const results = Array.isArray(res?.data?.results) ? res.data.results : [];
	const tail = results.slice(Math.max(0, results.length - n));
	let sum = 0;
	let any = false;
	for (const row of tail) {
		const v = Number(row?.count);
		if (!Number.isFinite(v)) continue;
		sum += v;
		any = true;
	}
	return any ? sum : null;
}

async function fetchItems(collection, params = {}) {
	const res = await api.get(`/items/${collection}`, { params });
	return Array.isArray(res?.data?.data) ? res.data.data : [];
}

async function refresh() {
	if (loading.value) return;
	loading.value = true;
	error.value = '';
	try {
		const [
			totalUsers,
			activeTariffs,
			blockedUsers,
			processedPayments,
			connections7d,
			registrations7d,
			connections30,
			registrations30,
			activeUsers30,
			totalUsers30,
			recentUsers,
			recentPayments,
			recentPromo,
		] = await Promise.all([
			fetchCount('users'),
			fetchCount('active_tariffs'),
			fetchCount('users', { 'filter[is_blocked][_eq]': 'true' }),
			fetchCount('processed_payments'),
			fetchWidgetSumLastN('connections', 7),
			fetchWidgetSumLastN('registered-users', 7),
			fetchWidgetSeries('connections', 30),
			fetchWidgetSeries('registered-users', 30),
			fetchWidgetSeries('active-users', 30),
			fetchWidgetSeries('total-users', 30),
			fetchItems('users', {
				fields: 'id,username,full_name,registration_date,is_blocked,expired_at',
				sort: '-registration_date',
				limit: 8,
			}),
			fetchItems('processed_payments', {
				fields: 'id,payment_id,user_id,processed_at,amount,status',
				sort: '-processed_at',
				limit: 8,
			}),
			fetchItems('promo_usages', {
				fields: 'id,promo_code_id,user_id,used_at,context',
				sort: '-used_at',
				limit: 8,
			}),
		]);

		stats.value = { totalUsers, activeTariffs, blockedUsers, processedPayments, connections7d, registrations7d };
		trends.value = {
			connections30d: connections30.series,
			registrations30d: registrations30.series,
			activeUsers30d: activeUsers30.series,
			totalUsers30d: totalUsers30.series,
			connectionsToday: connections30.today,
			registrationsToday: registrations30.today,
			activeUsersToday: activeUsers30.today,
			totalUsersToday: totalUsers30.today,
		};
		events.value = { users: recentUsers, payments: recentPayments, promo: recentPromo };
		lastUpdated.value = new Date().toISOString();
		await checkWidgets();
	} catch (e) {
		error.value = 'Не удалось загрузить данные. Проверь права роли и доступ к коллекциям.';
	}
	loading.value = false;
}

onMounted(() => {
	refresh();
});
</script>

<style scoped>
.page {
	padding: 16px;
	max-width: 1400px;
}

.hero {
	display: flex;
	align-items: flex-start;
	justify-content: space-between;
	gap: 16px;
	padding: 16px;
	border-radius: 12px;
	background: linear-gradient(135deg, rgba(59, 130, 246, 0.12), rgba(16, 185, 129, 0.06));
	border: 1px solid rgba(255, 255, 255, 0.06);
	margin-bottom: 16px;
}

.hero__title {
	font-size: 18px;
	font-weight: 700;
	line-height: 1.2;
}

.hero__subtitle {
	margin-top: 6px;
	opacity: 0.8;
	max-width: 720px;
}

.hero__meta-label {
	font-size: 12px;
	opacity: 0.7;
}

.hero__meta-value {
	margin-top: 2px;
	font-weight: 600;
}

.grid {
	display: grid;
	grid-template-columns: repeat(4, minmax(0, 1fr));
	gap: 12px;
	margin-bottom: 12px;
}

.grid--secondary {
	grid-template-columns: repeat(2, minmax(0, 1fr));
}

.panel--spaced {
	margin-bottom: 12px;
}

.alerts {
	display: grid;
	gap: 8px;
}

.alerts__item {
	display: flex;
	gap: 8px;
	align-items: center;
}

.trends {
	display: grid;
	grid-template-columns: repeat(2, minmax(0, 1fr));
	gap: 12px;
}

.trend {
	padding: 10px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
}

.trend__head {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 12px;
	margin-bottom: 8px;
}

.trend__label {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 650;
}

.trend__meta {
	display: flex;
	gap: 8px;
	align-items: baseline;
	font-size: 12px;
	opacity: 0.85;
}

.trend__meta-label {
	opacity: 0.7;
}

.trend__meta-value {
	font-weight: 700;
}

.spark {
	width: 100%;
	height: 34px;
}

.spark__line {
	fill: none;
	stroke: rgba(16, 185, 129, 0.9);
	stroke-width: 2;
	stroke-linecap: round;
	stroke-linejoin: round;
}

.spark__line--blue {
	stroke: rgba(59, 130, 246, 0.9);
}

.spark__line--purple {
	stroke: rgba(139, 92, 246, 0.9);
}

.events {
	display: grid;
	grid-template-columns: repeat(3, minmax(0, 1fr));
	gap: 12px;
}

.events__title {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 700;
	margin-bottom: 10px;
}

.events__all {
	margin-left: auto;
	font-size: 12px;
	opacity: 0.8;
	text-decoration: none;
}

.events__list {
	display: grid;
	gap: 8px;
}

.events__empty {
	opacity: 0.7;
	font-size: 12px;
}

.event {
	display: flex;
	justify-content: space-between;
	gap: 12px;
	padding: 10px;
	border-radius: 12px;
	text-decoration: none;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
}

.event:hover {
	background: rgba(255, 255, 255, 0.05);
}

.event__title {
	font-weight: 650;
}

.event__meta {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	margin-top: 4px;
}

.event__time {
	font-size: 12px;
	opacity: 0.75;
	white-space: nowrap;
}

.pill {
	font-size: 11px;
	padding: 2px 6px;
	border-radius: 999px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	opacity: 0.9;
}

.pill--bad {
	background: rgba(239, 68, 68, 0.16);
}

.kpi {
	padding: 14px;
	border-radius: 12px;
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
	background: rgba(245, 158, 11, 0.18);
}

.kpi__icon--purple {
	background: rgba(139, 92, 246, 0.18);
}

.kpi__label {
	font-size: 12px;
	opacity: 0.8;
}

.kpi__value {
	font-size: 20px;
	font-weight: 800;
	margin-top: 2px;
}

.layout {
	display: grid;
	grid-template-columns: 2fr 1fr;
	gap: 12px;
}

.panel {
	padding: 14px;
	border-radius: 12px;
}

.panel__title {
	font-weight: 700;
}

.panel__subtitle {
	opacity: 0.75;
	font-size: 12px;
	margin-top: 4px;
	margin-bottom: 10px;
}

.actions {
	display: grid;
	grid-template-columns: repeat(2, minmax(0, 1fr));
	gap: 10px;
}

.action {
	display: flex;
	gap: 10px;
	align-items: flex-start;
	padding: 10px;
	border-radius: 10px;
	text-decoration: none;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
}

.action:hover {
	background: rgba(255, 255, 255, 0.05);
}

.action__title {
	font-weight: 650;
}

.action__desc {
	font-size: 12px;
	opacity: 0.75;
	margin-top: 2px;
}

.health__row {
	display: flex;
	justify-content: space-between;
	align-items: center;
	padding: 8px 0;
	border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.health__row:last-child {
	border-bottom: none;
}

.health__label {
	opacity: 0.85;
}

.health__hint {
	margin-top: 10px;
	font-size: 12px;
	opacity: 0.75;
}

.chip--ok {
	background: rgba(16, 185, 129, 0.16);
}

.chip--bad {
	background: rgba(239, 68, 68, 0.16);
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

@media (max-width: 1200px) {
	.grid {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	.trends {
		grid-template-columns: 1fr;
	}

	.events {
		grid-template-columns: 1fr;
	}

	.layout {
		grid-template-columns: 1fr;
	}
}
</style>

