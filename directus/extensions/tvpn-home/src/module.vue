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

async function refresh() {
	if (loading.value) return;
	loading.value = true;
	error.value = '';
	try {
		const [totalUsers, activeTariffs, blockedUsers, processedPayments, connections7d, registrations7d] = await Promise.all([
			fetchCount('users'),
			fetchCount('active_tariffs'),
			fetchCount('users', { 'filter[is_blocked][_eq]': 'true' }),
			fetchCount('processed_payments'),
			fetchWidgetSumLastN('connections', 7),
			fetchWidgetSumLastN('registered-users', 7),
		]);

		stats.value = { totalUsers, activeTariffs, blockedUsers, processedPayments, connections7d, registrations7d };
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

	.layout {
		grid-template-columns: 1fr;
	}
}
</style>

