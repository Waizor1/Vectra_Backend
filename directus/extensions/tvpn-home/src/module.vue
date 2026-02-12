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
					<router-link class="nav__item" :to="{ path: '/content/error_reports' }">
						<v-icon name="bug_report" />
						<span>Логи ошибок</span>
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
			<div class="page__main">
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

				<div class="kpi-grid">
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

				<v-card class="panel">
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
						<div
							class="spark-wrap"
							@pointerdown="(e) => onChartDown(e, 'connections30', chartModels.connections30)"
							@pointermove="(e) => onChartMove(e, 'connections30', chartModels.connections30)"
							@pointerleave="() => onChartLeave('connections30')"
						>
							<svg class="spark premium" :viewBox="`0 0 ${chartModels.connections30.w} ${chartModels.connections30.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-connections30" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(16, 185, 129, 0.35)" />
										<stop offset="100%" stop-color="rgba(16, 185, 129, 0.00)" />
									</linearGradient>
								</defs>
								<path class="spark__area" :d="chartModels.connections30.areaD" fill="url(#grad-connections30)" />
								<path class="spark__path spark__path--green" :d="chartModels.connections30.lineD" />
							</svg>
							<div v-if="hover.key === 'connections30' && chartModels.connections30.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--green" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'connections30' && chartModels.connections30.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.connections30.points[hover.idx].rawLabel, 'day') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.connections30.points[hover.idx].v) }}</div>
							</div>
						</div>
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
						<div
							class="spark-wrap"
							@pointerdown="(e) => onChartDown(e, 'registrations30', chartModels.registrations30)"
							@pointermove="(e) => onChartMove(e, 'registrations30', chartModels.registrations30)"
							@pointerleave="() => onChartLeave('registrations30')"
						>
							<svg class="spark premium" :viewBox="`0 0 ${chartModels.registrations30.w} ${chartModels.registrations30.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-registrations30" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(59, 130, 246, 0.35)" />
										<stop offset="100%" stop-color="rgba(59, 130, 246, 0.00)" />
									</linearGradient>
								</defs>
								<path class="spark__area" :d="chartModels.registrations30.areaD" fill="url(#grad-registrations30)" />
								<path class="spark__path spark__path--blue" :d="chartModels.registrations30.lineD" />
							</svg>
							<div v-if="hover.key === 'registrations30' && chartModels.registrations30.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--blue" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'registrations30' && chartModels.registrations30.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.registrations30.points[hover.idx].rawLabel, 'day') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.registrations30.points[hover.idx].v) }}</div>
							</div>
						</div>
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
						<div class="spark-wrap" @pointerdown="(e) => onChartDown(e, 'activeUsers30', chartModels.activeUsers30)" @pointermove="(e) => onChartMove(e, 'activeUsers30', chartModels.activeUsers30)" @pointerleave="() => onChartLeave('activeUsers30')">
							<svg class="spark premium" :viewBox="`0 0 ${chartModels.activeUsers30.w} ${chartModels.activeUsers30.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-activeUsers30" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(16, 185, 129, 0.35)" />
										<stop offset="100%" stop-color="rgba(16, 185, 129, 0.00)" />
									</linearGradient>
								</defs>
								<path class="spark__area" :d="chartModels.activeUsers30.areaD" fill="url(#grad-activeUsers30)" />
								<path class="spark__path spark__path--green" :d="chartModels.activeUsers30.lineD" />
							</svg>
							<div v-if="hover.key === 'activeUsers30' && chartModels.activeUsers30.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--green" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'activeUsers30' && chartModels.activeUsers30.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.activeUsers30.points[hover.idx].rawLabel, 'day') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.activeUsers30.points[hover.idx].v) }}</div>
							</div>
						</div>
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
						<div class="spark-wrap" @pointerdown="(e) => onChartDown(e, 'totalUsers30', chartModels.totalUsers30)" @pointermove="(e) => onChartMove(e, 'totalUsers30', chartModels.totalUsers30)" @pointerleave="() => onChartLeave('totalUsers30')">
							<svg class="spark premium" :viewBox="`0 0 ${chartModels.totalUsers30.w} ${chartModels.totalUsers30.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-totalUsers30" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(139, 92, 246, 0.35)" />
										<stop offset="100%" stop-color="rgba(139, 92, 246, 0.00)" />
									</linearGradient>
								</defs>
								<path class="spark__area" :d="chartModels.totalUsers30.areaD" fill="url(#grad-totalUsers30)" />
								<path class="spark__path spark__path--purple" :d="chartModels.totalUsers30.lineD" />
							</svg>
							<div v-if="hover.key === 'totalUsers30' && chartModels.totalUsers30.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--purple" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'totalUsers30' && chartModels.totalUsers30.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.totalUsers30.points[hover.idx].rawLabel, 'day') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.totalUsers30.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>

					<div class="trend">
						<div class="trend__head">
							<div class="trend__label">
								<v-icon name="payments" />
								<span>Платежи (сумма)</span>
							</div>
							<div class="trend__meta">
								<span class="trend__meta-label">Сегодня:</span>
								<span class="trend__meta-value">{{ fmtMoney(trends.paymentsSumToday) }}</span>
							</div>
						</div>
						<div class="spark-wrap" @pointerdown="(e) => onChartDown(e, 'paymentsSum30', chartModels.paymentsSum30)" @pointermove="(e) => onChartMove(e, 'paymentsSum30', chartModels.paymentsSum30)" @pointerleave="() => onChartLeave('paymentsSum30')">
							<svg class="spark premium" :viewBox="`0 0 ${chartModels.paymentsSum30.w} ${chartModels.paymentsSum30.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-paymentsSum30" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(16, 185, 129, 0.35)" />
										<stop offset="100%" stop-color="rgba(16, 185, 129, 0.00)" />
									</linearGradient>
								</defs>
								<path class="spark__area" :d="chartModels.paymentsSum30.areaD" fill="url(#grad-paymentsSum30)" />
								<path class="spark__path spark__path--green" :d="chartModels.paymentsSum30.lineD" />
							</svg>
							<div v-if="hover.key === 'paymentsSum30' && chartModels.paymentsSum30.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--green" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'paymentsSum30' && chartModels.paymentsSum30.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.paymentsSum30.points[hover.idx].rawLabel, 'day') }}</div>
								<div class="spark__tooltip-value">{{ fmtMoney(chartModels.paymentsSum30.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>
				</div>
			</v-card>

			<v-card class="panel">
				<div class="panel__title">Тренды (12 месяцев)</div>
				<div class="panel__subtitle">Годовой срез, чтобы видеть сезонность, рост и “волну” без шума.</div>

				<div class="big">
					<div class="big__item big__item--blue">
						<div class="big__top">
							<div class="big__label">
								<v-icon name="person_add" />
								<span>Регистрации</span>
							</div>
							<div class="big__range">12м</div>
						</div>

						<div class="big__metrics">
							<div class="big__total">
								<div class="big__total-label">Итого</div>
								<div class="big__total-value">{{ fmt(Math.round(bigStats.registrations12.sum)) }}</div>
							</div>
							<div class="big__mini">
								<div class="big__mini-row">
									<span class="big__mini-k">avg /мес</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.registrations12.avg)) }}</span>
								</div>
								<div class="big__mini-row">
									<span class="big__mini-k">пик</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.registrations12.max)) }}</span>
								</div>
							</div>
						</div>

						<div class="spark-wrap spark-wrap--big" @pointerdown="(e) => onChartDown(e, 'registrations12', chartModels.registrations12)" @pointermove="(e) => onChartMove(e, 'registrations12', chartModels.registrations12)" @pointerleave="() => onChartLeave('registrations12')">
							<svg class="spark spark--big premium" :viewBox="`0 0 ${chartModels.registrations12.w} ${chartModels.registrations12.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-registrations12" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(59, 130, 246, 0.34)" />
										<stop offset="100%" stop-color="rgba(59, 130, 246, 0.00)" />
									</linearGradient>
									<clipPath id="clip-registrations12">
										<rect :width="chartModels.registrations12.w" :height="chartModels.registrations12.h" rx="10" ry="10" />
									</clipPath>
								</defs>
								<g clip-path="url(#clip-registrations12)">
									<path class="spark__area" :d="chartModels.registrations12.areaD" fill="url(#grad-registrations12)" />
									<path class="spark__glow spark__glow--blue" :d="chartModels.registrations12.lineD" />
									<path class="spark__path spark__path--blue" :d="chartModels.registrations12.lineD" />
								</g>
							</svg>
							<div v-if="hover.key === 'registrations12' && chartModels.registrations12.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--blue" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'registrations12' && chartModels.registrations12.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.registrations12.points[hover.idx].rawLabel, 'month') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.registrations12.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>

					<div class="big__item big__item--green">
						<div class="big__top">
							<div class="big__label">
								<v-icon name="wifi" />
								<span>Подключения</span>
							</div>
							<div class="big__range">12м</div>
						</div>

						<div class="big__metrics">
							<div class="big__total">
								<div class="big__total-label">Итого</div>
								<div class="big__total-value">{{ fmt(Math.round(bigStats.connections12.sum)) }}</div>
							</div>
							<div class="big__mini">
								<div class="big__mini-row">
									<span class="big__mini-k">avg /мес</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.connections12.avg)) }}</span>
								</div>
								<div class="big__mini-row">
									<span class="big__mini-k">пик</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.connections12.max)) }}</span>
								</div>
							</div>
						</div>

						<div class="spark-wrap spark-wrap--big" @pointerdown="(e) => onChartDown(e, 'connections12', chartModels.connections12)" @pointermove="(e) => onChartMove(e, 'connections12', chartModels.connections12)" @pointerleave="() => onChartLeave('connections12')">
							<svg class="spark spark--big premium" :viewBox="`0 0 ${chartModels.connections12.w} ${chartModels.connections12.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-connections12" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(16, 185, 129, 0.32)" />
										<stop offset="100%" stop-color="rgba(16, 185, 129, 0.00)" />
									</linearGradient>
									<clipPath id="clip-connections12">
										<rect :width="chartModels.connections12.w" :height="chartModels.connections12.h" rx="10" ry="10" />
									</clipPath>
								</defs>
								<g clip-path="url(#clip-connections12)">
									<path class="spark__area" :d="chartModels.connections12.areaD" fill="url(#grad-connections12)" />
									<path class="spark__glow spark__glow--green" :d="chartModels.connections12.lineD" />
									<path class="spark__path spark__path--green" :d="chartModels.connections12.lineD" />
								</g>
							</svg>
							<div v-if="hover.key === 'connections12' && chartModels.connections12.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--green" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'connections12' && chartModels.connections12.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.connections12.points[hover.idx].rawLabel, 'month') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.connections12.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>

					<div class="big__item big__item--purple">
						<div class="big__top">
							<div class="big__label">
								<v-icon name="payments" />
								<span>Платежи (сумма)</span>
							</div>
							<div class="big__range">12м</div>
						</div>

						<div class="big__metrics">
							<div class="big__total">
								<div class="big__total-label">Итого</div>
								<div class="big__total-value">{{ fmtMoney(Math.round(bigStats.paymentsSum12.sum)) }}</div>
							</div>
							<div class="big__mini">
								<div class="big__mini-row">
									<span class="big__mini-k">avg /мес</span>
									<span class="big__mini-v">{{ fmtMoney(Math.round(bigStats.paymentsSum12.avg)) }}</span>
								</div>
								<div class="big__mini-row">
									<span class="big__mini-k">пик</span>
									<span class="big__mini-v">{{ fmtMoney(Math.round(bigStats.paymentsSum12.max)) }}</span>
								</div>
							</div>
						</div>

						<div class="spark-wrap spark-wrap--big" @pointerdown="(e) => onChartDown(e, 'paymentsSum12', chartModels.paymentsSum12)" @pointermove="(e) => onChartMove(e, 'paymentsSum12', chartModels.paymentsSum12)" @pointerleave="() => onChartLeave('paymentsSum12')">
							<svg class="spark spark--big premium" :viewBox="`0 0 ${chartModels.paymentsSum12.w} ${chartModels.paymentsSum12.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-paymentsSum12" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(139, 92, 246, 0.32)" />
										<stop offset="100%" stop-color="rgba(139, 92, 246, 0.00)" />
									</linearGradient>
									<clipPath id="clip-paymentsSum12">
										<rect :width="chartModels.paymentsSum12.w" :height="chartModels.paymentsSum12.h" rx="10" ry="10" />
									</clipPath>
								</defs>
								<g clip-path="url(#clip-paymentsSum12)">
									<path class="spark__area" :d="chartModels.paymentsSum12.areaD" fill="url(#grad-paymentsSum12)" />
									<path class="spark__glow spark__glow--purple" :d="chartModels.paymentsSum12.lineD" />
									<path class="spark__path spark__path--purple" :d="chartModels.paymentsSum12.lineD" />
								</g>
							</svg>
							<div v-if="hover.key === 'paymentsSum12' && chartModels.paymentsSum12.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--purple" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'paymentsSum12' && chartModels.paymentsSum12.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.paymentsSum12.points[hover.idx].rawLabel, 'month') }}</div>
								<div class="spark__tooltip-value">{{ fmtMoney(chartModels.paymentsSum12.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>

					<div class="big__item big__item--cyan">
						<div class="big__top">
							<div class="big__label">
								<v-icon name="person" />
								<span>Активные пользователи</span>
							</div>
							<div class="big__range">12м</div>
						</div>

						<div class="big__metrics">
							<div class="big__total">
								<div class="big__total-label">Сейчас</div>
								<div class="big__total-value">{{ fmt(Math.round(bigStats.activeUsers12.last)) }}</div>
							</div>
							<div class="big__mini">
								<div class="big__mini-row">
									<span class="big__mini-k">avg /мес</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.activeUsers12.avg)) }}</span>
								</div>
								<div class="big__mini-row">
									<span class="big__mini-k">пик</span>
									<span class="big__mini-v">{{ fmt(Math.round(bigStats.activeUsers12.max)) }}</span>
								</div>
							</div>
						</div>

						<div class="spark-wrap spark-wrap--big" @pointerdown="(e) => onChartDown(e, 'activeUsers12', chartModels.activeUsers12)" @pointermove="(e) => onChartMove(e, 'activeUsers12', chartModels.activeUsers12)" @pointerleave="() => onChartLeave('activeUsers12')">
							<svg class="spark spark--big premium" :viewBox="`0 0 ${chartModels.activeUsers12.w} ${chartModels.activeUsers12.h}`" preserveAspectRatio="none" aria-hidden="true">
								<defs>
									<linearGradient id="grad-activeUsers12" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stop-color="rgba(34, 211, 238, 0.30)" />
										<stop offset="100%" stop-color="rgba(34, 211, 238, 0.00)" />
									</linearGradient>
									<clipPath id="clip-activeUsers12">
										<rect :width="chartModels.activeUsers12.w" :height="chartModels.activeUsers12.h" rx="10" ry="10" />
									</clipPath>
								</defs>
								<g clip-path="url(#clip-activeUsers12)">
									<path class="spark__area" :d="chartModels.activeUsers12.areaD" fill="url(#grad-activeUsers12)" />
									<path class="spark__glow spark__glow--cyan" :d="chartModels.activeUsers12.lineD" />
									<path class="spark__path spark__path--cyan" :d="chartModels.activeUsers12.lineD" />
								</g>
							</svg>
							<div v-if="hover.key === 'activeUsers12' && chartModels.activeUsers12.points.length" class="spark__overlay">
								<div class="spark__vline-overlay" :style="{ left: `${hover.px}px` }" />
								<div class="spark__dot-overlay spark__dot-overlay--cyan" :style="{ left: `${hover.px}px`, top: `${hover.py}px` }">
									<div class="spark__dot-overlay-inner" />
								</div>
							</div>
							<div v-if="hover.key === 'activeUsers12' && chartModels.activeUsers12.points.length" class="spark__tooltip" :style="{ left: `${hover.x}px` }">
								<div class="spark__tooltip-title">{{ formatLabel(chartModels.activeUsers12.points[hover.idx].rawLabel, 'month') }}</div>
								<div class="spark__tooltip-value">{{ fmt(chartModels.activeUsers12.points[hover.idx].v) }}</div>
							</div>
						</div>
					</div>
				</div>
			</v-card>

			<v-card class="panel">
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
			</div>

			<div class="page__side">
				<v-card class="panel">
					<div class="panel__title">Быстрые виджеты</div>
					<div class="panel__subtitle">Оперативные списки без лишней навигации.</div>

					<div class="widgets">
						<div class="widgets__block">
							<div class="widgets__title">
								<v-icon name="event" />
								<span>Истекает в {{ settings.expiring_days }} дн.</span>
							</div>
							<div v-if="quick.expiring.length" class="widgets__list">
								<router-link
									v-for="u in quick.expiring"
									:key="u.id"
									class="widgets__row"
									:to="{ path: `/content/users/${u.id}` }"
								>
									<span class="widgets__name">{{ u.username || u.full_name || u.id }}</span>
									<span class="widgets__meta">{{ shortDate(u.expired_at) }}</span>
								</router-link>
							</div>
							<div v-else class="widgets__empty">Нет данных</div>
						</div>

						<div class="widgets__block">
							<div class="widgets__title">
								<v-icon name="leaderboard" />
								<span>Топ по балансу</span>
							</div>
							<div v-if="quick.topBalance.length" class="widgets__list">
								<router-link
									v-for="u in quick.topBalance"
									:key="u.id"
									class="widgets__row"
									:to="{ path: `/content/users/${u.id}` }"
								>
									<span class="widgets__name">{{ u.username || u.full_name || u.id }}</span>
									<span class="widgets__meta">{{ fmtMoney(u.balance) }}</span>
								</router-link>
							</div>
							<div v-else class="widgets__empty">Нет данных</div>
						</div>

						<div class="widgets__block">
							<div class="widgets__title">
								<v-icon name="report" />
								<span>Подозрительные блокировки</span>
							</div>
							<div v-if="quick.blockedRecent.length" class="widgets__list">
								<router-link
									v-for="u in quick.blockedRecent"
									:key="u.id"
									class="widgets__row"
									:to="{ path: `/content/users/${u.id}` }"
								>
									<span class="widgets__name">{{ u.username || u.full_name || u.id }}</span>
									<span class="widgets__meta">{{ fromNow(u.blocked_at || u.registration_date) }}</span>
								</router-link>
							</div>
							<div v-else class="widgets__empty">Нет данных</div>
							<div class="widgets__hint">
								Показываем блокировки за последние {{ settings.suspicious_block_days }} дня.
							</div>
						</div>
					</div>
				</v-card>

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

				<v-card class="panel">
					<div class="panel__title">Технические работы</div>
					<div class="panel__subtitle">При включении фронтенд-пользователи увидят экран техработ вместо основного приложения.</div>

					<v-notice v-if="settingsSaveError" type="danger">
						{{ settingsSaveError }}
					</v-notice>

					<div class="settings">
						<label class="settings__row">
							<span>Режим техработ включён</span>
							<input v-model="settings.maintenance_mode" class="settings__input" type="checkbox" />
						</label>

						<label class="settings__row settings__row--textarea">
							<span>Текст для пользователей</span>
							<textarea
								v-model="settings.maintenance_message"
								class="settings__input settings__input--textarea"
								rows="3"
								placeholder="Например: обновляем серверы, ориентировочно до 15:00 МСК"
							/>
						</label>

						<v-button small :loading="settingsSaving" :disabled="settingsSaving" @click="saveSettings">
							Сохранить
						</v-button>
					</div>
				</v-card>

				<v-card class="panel">
					<div class="panel__title">Пороги алертов</div>
					<div class="panel__subtitle">Настраивается через `tvpn_admin_settings` (сохраняется в базе).</div>

					<v-notice v-if="settingsSaveError" type="danger">
						{{ settingsSaveError }}
					</v-notice>

					<div class="settings">
						<label class="settings__row">
							<span>Алерты включены</span>
							<input v-model="settings.alerts_enabled" class="settings__input" type="checkbox" />
						</label>

						<label class="settings__row">
							<span>Всплеск регистраций (x)</span>
							<input v-model.number="settings.reg_spike_factor" class="settings__input settings__input--num" type="number" step="0.1" min="1" />
						</label>

						<label class="settings__row">
							<span>Мин. регистраций</span>
							<input v-model.number="settings.reg_spike_min" class="settings__input settings__input--num" type="number" step="1" min="0" />
						</label>

						<label class="settings__row">
							<span>Падение подключений (≤ %)</span>
							<input v-model.number="settings.conn_drop_factor" class="settings__input settings__input--num" type="number" step="0.05" min="0" max="1" />
						</label>

						<label class="settings__row">
							<span>Мин. среднее (7д)</span>
							<input v-model.number="settings.conn_drop_min_avg" class="settings__input settings__input--num" type="number" step="1" min="0" />
						</label>

						<label class="settings__row">
							<span>Аномалия платежей (x)</span>
							<input v-model.number="settings.pay_spike_factor" class="settings__input settings__input--num" type="number" step="0.1" min="1" />
						</label>

						<label class="settings__row">
							<span>Мин. сумма (день)</span>
							<input v-model.number="settings.pay_spike_min_sum" class="settings__input settings__input--num" type="number" step="100" min="0" />
						</label>

						<label class="settings__row">
							<span>Истекает (дни)</span>
							<input v-model.number="settings.expiring_days" class="settings__input settings__input--num" type="number" step="1" min="1" max="90" />
						</label>

						<label class="settings__row">
							<span>Блокировки (дни)</span>
							<input v-model.number="settings.suspicious_block_days" class="settings__input settings__input--num" type="number" step="1" min="1" max="30" />
						</label>

						<v-button small :loading="settingsSaving" :disabled="settingsSaving" @click="saveSettings">
							Сохранить
						</v-button>
					</div>
				</v-card>

				<v-card class="panel">
					<div class="panel__title">Что где находится</div>
					<div class="panel__subtitle">Короткие пояснения по разделам и параметрам.</div>

					<div class="help">
						<div class="help__row">
							<div class="help__k">Пользователи</div>
							<div class="help__v">Подписка/лимиты/баланс/блокировки, быстрый поиск и карточка.</div>
						</div>
						<div class="help__row">
							<div class="help__k">Активные тарифы</div>
							<div class="help__v">Ограничения, usage, мультипликатор, связанные статусы.</div>
						</div>
						<div class="help__row">
							<div class="help__k">Промо</div>
							<div class="help__v">Коды и использования; HMAC генерируется автоматически.</div>
						</div>
						<div class="help__row">
							<div class="help__k">Колесо призов</div>
							<div class="help__v">Конфиг и история; сумма вероятностей валидируется.</div>
						</div>
						<div class="help__row">
							<div class="help__k">Платежи</div>
							<div class="help__v">Processed payments + статусы, чтобы ловить аномалии.</div>
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

const settingsId = ref(null);
const settings = ref({
	maintenance_mode: false,
	maintenance_message: '',
	alerts_enabled: true,
	reg_spike_factor: 3.0,
	reg_spike_min: 10,
	conn_drop_factor: 0.4,
	conn_drop_min_avg: 20,
	pay_spike_factor: 2.5,
	pay_spike_min_sum: 5000,
	expiring_days: 7,
	suspicious_block_days: 3,
});
const settingsSaving = ref(false);
const settingsSaveError = ref('');

const trends = ref({
	connections30d: [],
	connections30d_labels: [],
	registrations30d: [],
	registrations30d_labels: [],
	activeUsers30d: [],
	activeUsers30d_labels: [],
	totalUsers30d: [],
	totalUsers30d_labels: [],
	paymentsSum30d: [],
	paymentsSum30d_labels: [],
	paymentsSumToday: null,
	connectionsToday: null,
	registrationsToday: null,
	activeUsersToday: null,
	totalUsersToday: null,
});

const year = ref({
	registrations12m: [],
	registrations12m_labels: [],
	connections12m: [],
	connections12m_labels: [],
	activeUsers12m: [],
	activeUsers12m_labels: [],
	paymentsSum12m: [],
	paymentsSum12m_labels: [],
});

const events = ref({
	users: [],
	payments: [],
	promo: [],
});

const quick = ref({
	expiring: [],
	topBalance: [],
	blockedRecent: [],
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

const chartModels = computed(() => {
	// Build once per render so hover logic stays consistent.
	return {
		connections30: buildChartModel(trends.value.connections30d, trends.value.connections30d_labels, 'day', 'compact'),
		registrations30: buildChartModel(trends.value.registrations30d, trends.value.registrations30d_labels, 'day', 'compact'),
		activeUsers30: buildChartModel(trends.value.activeUsers30d, trends.value.activeUsers30d_labels, 'day', 'compact'),
		totalUsers30: buildChartModel(trends.value.totalUsers30d, trends.value.totalUsers30d_labels, 'day', 'compact'),
		paymentsSum30: buildChartModel(trends.value.paymentsSum30d, trends.value.paymentsSum30d_labels, 'day', 'compact'),

		registrations12: buildChartModel(year.value.registrations12m, year.value.registrations12m_labels, 'month', 'big'),
		connections12: buildChartModel(year.value.connections12m, year.value.connections12m_labels, 'month', 'big'),
		activeUsers12: buildChartModel(year.value.activeUsers12m, year.value.activeUsers12m_labels, 'month', 'big'),
		paymentsSum12: buildChartModel(year.value.paymentsSum12m, year.value.paymentsSum12m_labels, 'month', 'big'),
	};
});

function statsFromSeriesSnapshot(series) {
	const nums = (Array.isArray(series) ? series : []).map((v) => Number(v)).filter((v) => Number.isFinite(v));
	if (!nums.length) return { last: 0, avg: 0, max: 0 };
	const last = nums[nums.length - 1];
	const sum = nums.reduce((a, b) => a + b, 0);
	const avg = sum / nums.length;
	const max = Math.max(...nums);
	return { last, avg, max };
}

const bigStats = computed(() => {
	return {
		registrations12: statsFromSeries(year.value.registrations12m),
		connections12: statsFromSeries(year.value.connections12m),
		activeUsers12: statsFromSeriesSnapshot(year.value.activeUsers12m),
		paymentsSum12: statsFromSeries(year.value.paymentsSum12m),
	};
});

const alerts = computed(() => {
	const out = [];

	if (!widgetsOk.value) {
		out.push({
			key: 'widgets',
			icon: 'warning',
			text: 'Виджеты /admin-widgets не отдают данные. Проверь extensions и права.',
		});
	}

	if (!settings.value.alerts_enabled) return out;

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
		const factor = Number(settings.value.reg_spike_factor) || 3;
		const minToday = Number(settings.value.reg_spike_min) || 0;
		if (Number.isFinite(today) && avg > 0 && today >= avg * factor && today >= minToday) {
			out.push({
				key: 'registrations-spike',
				icon: 'trending_up',
				text: `Всплеск регистраций: сегодня ${today}, среднее за 7 дней ${avg.toFixed(1)} (x${factor}).`,
			});
		}
	}

	if (c.length >= 8) {
		const today = Number(c[c.length - 1]);
		const prev7 = c.slice(c.length - 8, c.length - 1).map((x) => Number(x)).filter((x) => Number.isFinite(x));
		const avg = prev7.length ? prev7.reduce((a, b) => a + b, 0) / prev7.length : 0;
		const factor = Number(settings.value.conn_drop_factor) || 0.4;
		const minAvg = Number(settings.value.conn_drop_min_avg) || 0;
		if (Number.isFinite(today) && avg >= minAvg && today <= avg * factor) {
			out.push({
				key: 'connections-drop',
				icon: 'trending_down',
				text: `Падение подключений: сегодня ${today}, среднее за 7 дней ${avg.toFixed(1)} (≤ ${Math.round(factor * 100)}%).`,
			});
		}
	}

	const pay = trends.value.paymentsSum30d || [];
	if (pay.length >= 8) {
		const today = Number(pay[pay.length - 1]);
		const prev7 = pay.slice(pay.length - 8, pay.length - 1).map((x) => Number(x)).filter((x) => Number.isFinite(x));
		const avg = prev7.length ? prev7.reduce((a, b) => a + b, 0) / prev7.length : 0;
		const factor = Number(settings.value.pay_spike_factor) || 2.5;
		const minSum = Number(settings.value.pay_spike_min_sum) || 0;
		if (Number.isFinite(today) && avg > 0 && today >= avg * factor && today >= minSum) {
			out.push({
				key: 'payments-anomaly',
				icon: 'payments',
				text: `Аномалия платежей: сегодня ${fmtMoney(today)}, среднее за 7 дней ${fmtMoney(avg)} (x${factor}).`,
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
	if (!Array.isArray(values) || values.length < 1) return '';
	const nums = values.map((v) => Number(v)).filter((v) => Number.isFinite(v));
	if (nums.length < 1) return '';
	// If we only have a single value (e.g. brand-new project), draw a flat line.
	if (nums.length === 1) {
		const y = 15;
		return `0.00,${y.toFixed(2)} 100.00,${y.toFixed(2)}`;
	}
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

function isoFromNow(days, fallbackDays = 0) {
	const n = Number(days);
	const safe = Number.isFinite(n) ? n : Number(fallbackDays);
	const d = new Date(Date.now() + safe * 24 * 60 * 60 * 1000);
	// Invalid Date would throw on toISOString(); guard hard.
	if (Number.isNaN(d.getTime())) return new Date().toISOString();
	return d.toISOString();
}

function isoMonthStartFromNow(monthsBack = 11) {
	const n = Number(monthsBack);
	const safe = Number.isFinite(n) ? n : 11;
	const d = new Date();
	d.setUTCDate(1);
	d.setUTCHours(0, 0, 0, 0);
	d.setUTCMonth(d.getUTCMonth() - safe);
	if (Number.isNaN(d.getTime())) return new Date().toISOString();
	return d.toISOString();
}

function parseDdMmYyyy(value) {
	// Admin-widgets returns DD/MM/YYYY as a string.
	const s = String(value || '');
	const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s);
	if (!m) return null;
	const dd = Number(m[1]);
	const mm = Number(m[2]);
	const yyyy = Number(m[3]);
	if (!Number.isFinite(dd) || !Number.isFinite(mm) || !Number.isFinite(yyyy)) return null;
	const d = new Date(Date.UTC(yyyy, mm - 1, dd, 0, 0, 0));
	return Number.isNaN(d.getTime()) ? null : d;
}

function formatLabel(label, period) {
	const d = parseDdMmYyyy(label);
	if (!d) return String(label || '—');
	if (period === 'month') return d.toLocaleDateString('ru-RU', { month: 'short', year: 'numeric' });
	return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
}

function statsFromSeries(series) {
	const nums = (Array.isArray(series) ? series : []).map((v) => Number(v)).filter((v) => Number.isFinite(v));
	if (!nums.length) return { sum: 0, avg: 0, max: 0 };
	const sum = nums.reduce((a, b) => a + b, 0);
	const avg = sum / nums.length;
	const max = Math.max(...nums);
	return { sum, avg, max };
}

function monotoneCubicPath(points) {
	// D3-like monotone cubic interpolation (no overshoot).
	// Returns a cubic Bezier path through points (x must be non-decreasing).
	if (!Array.isArray(points) || points.length < 2) return '';
	if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;

	const n = points.length;
	const xs = points.map((p) => p.x);
	const ys = points.map((p) => p.y);

	// Slopes between points.
	const s = new Array(n - 1);
	for (let i = 0; i < n - 1; i++) {
		const dx = xs[i + 1] - xs[i];
		s[i] = dx ? (ys[i + 1] - ys[i]) / dx : 0;
	}

	// Initial tangents.
	const t = new Array(n);
	t[0] = s[0];
	t[n - 1] = s[n - 2];
	for (let i = 1; i < n - 1; i++) t[i] = (s[i - 1] + s[i]) / 2;

	// Fritsch-Carlson monotone adjustment.
	for (let i = 0; i < n - 1; i++) {
		if (s[i] === 0) {
			t[i] = 0;
			t[i + 1] = 0;
			continue;
		}
		const a = t[i] / s[i];
		const b = t[i + 1] / s[i];
		const h = Math.hypot(a, b);
		if (h > 3) {
			const scale = 3 / h;
			t[i] = scale * a * s[i];
			t[i + 1] = scale * b * s[i];
		}
	}

	let d = `M ${xs[0]} ${ys[0]}`;
	for (let i = 0; i < n - 1; i++) {
		const x0 = xs[i];
		const y0 = ys[i];
		const x1 = xs[i + 1];
		const y1 = ys[i + 1];
		const dx = x1 - x0;
		const cp1x = x0 + dx / 3;
		const cp1y = y0 + (t[i] * dx) / 3;
		const cp2x = x1 - dx / 3;
		const cp2y = y1 - (t[i + 1] * dx) / 3;
		d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${x1} ${y1}`;
	}
	return d;
}

function buildChartModel(series, labels, period, variant = 'compact') {
	const nums = (Array.isArray(series) ? series : []).map((v) => Number(v)).map((v) => (Number.isFinite(v) ? v : 0));
	const lbs = Array.isArray(labels) ? labels.map((x) => String(x ?? '')) : [];
	const n = nums.length;
	// IMPORTANT: match viewBox height to CSS height to avoid stroke "ballooning"
	// due to SVG scaling. (Big charts were previously scaled 64 -> 120px.)
	const h = variant === 'big' ? 120 : 64;
	const w = 100;
	const padX = 2;
	const padY = variant === 'big' ? 8 : 8;
	if (n === 0) {
		return { w, h, n, points: [], lineD: '', areaD: '' };
	}
	// For our metrics (counts / sums) negative values don't make sense; keeping a 0 baseline
	// makes charts more readable and prevents "floating" areas when all values are > 0.
	const min = 0;
	const rawMax = Math.max(...nums, 0);
	// Add a bit of headroom so peaks don't stick to the top and look like a thick bar.
	const max = rawMax === 0 ? 1 : rawMax * 1.12;
	const denom = max - min || 1;
	const dx = n === 1 ? 0 : (w - padX * 2) / (n - 1);
	const pts = nums.map((v, i) => {
		const x = padX + i * dx;
		const y = padY + (1 - (v - min) / denom) * (h - padY * 2);
		return { x: Number(x.toFixed(3)), y: Number(y.toFixed(3)), v, rawLabel: lbs[i] || '' };
	});
	const lineD = n === 1 ? `M ${padX} ${pts[0].y} L ${w - padX} ${pts[0].y}` : monotoneCubicPath(pts);
	const baseY = h - padY;
	const areaD = `${lineD} L ${w - padX} ${baseY} L ${padX} ${baseY} Z`;
	return { w, h, n, points: pts, lineD, areaD };
}

// Hover rendering is done via HTML overlay (not SVG circles) so it stays crisp
// even when the SVG is non-uniformly scaled (responsive width + fixed height).
const hover = ref({ key: null, idx: 0, x: 0, px: 0, py: 0 });
let touchHideTimer = null;
function onChartLeave(key) {
	if (hover.value.key === key) hover.value = { key: null, idx: 0, x: 0, px: 0, py: 0 };
}

function onChartDown(evt, key, model) {
	onChartMove(evt, key, model);
	// On touch devices, show tooltip on tap and auto-hide.
	if (evt?.pointerType === 'touch') {
		if (touchHideTimer) clearTimeout(touchHideTimer);
		touchHideTimer = setTimeout(() => onChartLeave(key), 1800);
	}
}

function onChartMove(evt, key, model) {
	if (!model || !model.n) return;
	const el = evt.currentTarget;
	const rect = el.getBoundingClientRect();
	const clientX = evt?.clientX;
	if (typeof clientX !== 'number') return;
	const rawX = Math.max(0, Math.min(rect.width, clientX - rect.left));
	const idx = model.n === 1 ? 0 : Math.round((rawX / rect.width) * (model.n - 1));
	const tooltipPad = 72;
	const x = Math.max(tooltipPad, Math.min(rect.width - tooltipPad, rawX));
	const p = model.points?.[idx];
	// Position the indicator exactly at the data point (not at cursor),
	// otherwise it looks "wobbly" and can drift from the line.
	const px = model.n === 1 ? rect.width / 2 : (Number(p?.x) / model.w) * rect.width;
	const py = (Number(p?.y) / model.h) * rect.height;
	hover.value = { key, idx, x, px, py };
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
		await api.get('/admin-widgets/total-users', { params: { period_x_field: 'day' } });
		await api.get('/admin-widgets/payments', { params: { period_x_field: 'day' } });
		widgetsOk.value = true;
	} catch {
		widgetsOk.value = false;
	}
}

async function fetchWidgetSeries(endpoint, n = 30, opts = {}) {
	const period = opts.period_x_field || 'day';
	const params = { period_x_field: period };
	if (opts.min_x_field) params.min_x_field = opts.min_x_field;
	if (opts.max_x_field) params.max_x_field = opts.max_x_field;
	if (opts.no_clamp_db_min) params.no_clamp_db_min = String(opts.no_clamp_db_min);
	const res = await api.get(`/admin-widgets/${endpoint}`, { params });
	const results = Array.isArray(res?.data?.results) ? res.data.results : [];
	const tail = results.slice(Math.max(0, results.length - n));
	const series = tail.map((row) => {
		const v = Number(row?.count);
		return Number.isFinite(v) ? v : 0;
	});
	const labels = tail.map((row) => String(row?.date ?? ''));
	const today = series.length ? series[series.length - 1] : null;
	return { series, labels, today, period };
}

async function fetchWidgetSumLastN(endpoint, n = 7) {
	const res = await api.get(`/admin-widgets/${endpoint}`, { params: { period_x_field: 'day' } });
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

async function fetchPaymentsSumSeries(n = 30, opts = {}) {
	const period = opts.period_x_field || 'day';
	const params = { period_x_field: period };
	if (opts.min_x_field) params.min_x_field = opts.min_x_field;
	if (opts.max_x_field) params.max_x_field = opts.max_x_field;
	if (opts.no_clamp_db_min) params.no_clamp_db_min = String(opts.no_clamp_db_min);
	const res = await api.get('/admin-widgets/payments', { params });
	const results = Array.isArray(res?.data?.results) ? res.data.results : [];
	const tail = results.slice(Math.max(0, results.length - n));
	const series = tail.map((row) => {
		const v = Number(row?.total_amount);
		return Number.isFinite(v) ? v : 0;
	});
	const labels = tail.map((row) => String(row?.date ?? ''));
	const today = series.length ? series[series.length - 1] : null;
	return { series, labels, today, period };
}

async function fetchItems(collection, params = {}) {
	const res = await api.get(`/items/${collection}`, { params });
	return Array.isArray(res?.data?.data) ? res.data.data : [];
}

async function loadSettings() {
	settingsSaveError.value = '';
	try {
		const res = await api.get('/items/tvpn_admin_settings', {
			params: { limit: 1 },
		});
		const rows = Array.isArray(res?.data?.data) ? res.data.data : [];
		const row = rows[0];
		if (!row) return;
		settingsId.value = row.id ?? null;
		settings.value = { ...settings.value, ...row };
	} catch {
		// It's optional; admins may choose to keep defaults.
	}
}

async function saveSettings() {
	if (settingsSaving.value) return;
	settingsSaving.value = true;
	settingsSaveError.value = '';
	try {
		if (!settingsId.value) {
			const created = await api.post('/items/tvpn_admin_settings', settings.value);
			const createdId = created?.data?.data?.id ?? null;
			settingsId.value = createdId;
		} else {
			await api.patch(`/items/tvpn_admin_settings/${settingsId.value}`, settings.value);
		}
	} catch {
		settingsSaveError.value = 'Не удалось сохранить настройки (нет прав или коллекция не создана).';
	}
	settingsSaving.value = false;
}

async function refresh() {
	if (loading.value) return;
	loading.value = true;
	error.value = '';
	try {
		await loadSettings();

		const expDays = Number(settings.value.expiring_days);
		const expDaysSafe = Number.isFinite(expDays) ? expDays : 7;
		const blockDays = Number(settings.value.suspicious_block_days);
		const blockDaysSafe = Number.isFinite(blockDays) ? blockDays : 3;

		const settled = await Promise.allSettled([
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
			fetchPaymentsSumSeries(30),
			fetchWidgetSeries('registered-users', 12, { period_x_field: 'month', min_x_field: isoMonthStartFromNow(11), no_clamp_db_min: '1' }),
			fetchWidgetSeries('connections', 12, { period_x_field: 'month', min_x_field: isoMonthStartFromNow(11), no_clamp_db_min: '1' }),
			fetchWidgetSeries('active-users', 12, { period_x_field: 'month', min_x_field: isoMonthStartFromNow(11), no_clamp_db_min: '1' }),
			fetchPaymentsSumSeries(12, { period_x_field: 'month', min_x_field: isoMonthStartFromNow(11), no_clamp_db_min: '1' }),
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
			fetchItems('users', {
				fields: 'id,username,full_name,expired_at,balance,is_blocked',
				sort: 'expired_at',
				limit: 6,
				'filter[expired_at][_gte]': new Date().toISOString(),
				'filter[expired_at][_lte]': isoFromNow(expDaysSafe, 7),
			}),
			fetchItems('users', {
				fields: 'id,username,full_name,balance,expired_at,is_blocked',
				sort: '-balance',
				limit: 6,
				'filter[balance][_gt]': '0',
			}),
			fetchItems('users', {
				fields: 'id,username,full_name,balance,blocked_at,registration_date',
				sort: '-blocked_at',
				limit: 6,
				'filter[is_blocked][_eq]': 'true',
				'filter[blocked_at][_gte]': isoFromNow(-blockDaysSafe, -3),
			}),
		]);

		const values = settled.map((r) => (r.status === 'fulfilled' ? r.value : null));
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
			payments30,
			reg12m,
			conn12m,
			active12m,
			pay12m,
			recentUsers,
			recentPayments,
			recentPromo,
			expiring,
			topBalance,
			blockedRecent,
		] = values;

		stats.value = { totalUsers, activeTariffs, blockedUsers, processedPayments, connections7d, registrations7d };
		trends.value = {
			connections30d: connections30?.series || [],
			connections30d_labels: connections30?.labels || [],
			registrations30d: registrations30?.series || [],
			registrations30d_labels: registrations30?.labels || [],
			activeUsers30d: activeUsers30?.series || [],
			activeUsers30d_labels: activeUsers30?.labels || [],
			totalUsers30d: totalUsers30?.series || [],
			totalUsers30d_labels: totalUsers30?.labels || [],
			paymentsSum30d: payments30?.series || [],
			paymentsSum30d_labels: payments30?.labels || [],
			paymentsSumToday: payments30?.today ?? null,
			connectionsToday: connections30?.today ?? null,
			registrationsToday: registrations30?.today ?? null,
			activeUsersToday: activeUsers30?.today ?? null,
			totalUsersToday: totalUsers30?.today ?? null,
		};
		year.value = {
			registrations12m: reg12m?.series || [],
			registrations12m_labels: reg12m?.labels || [],
			connections12m: conn12m?.series || [],
			connections12m_labels: conn12m?.labels || [],
			activeUsers12m: active12m?.series || [],
			activeUsers12m_labels: active12m?.labels || [],
			paymentsSum12m: pay12m?.series || [],
			paymentsSum12m_labels: pay12m?.labels || [],
		};
		events.value = { users: Array.isArray(recentUsers) ? recentUsers : [], payments: Array.isArray(recentPayments) ? recentPayments : [], promo: Array.isArray(recentPromo) ? recentPromo : [] };
		quick.value = {
			expiring: Array.isArray(expiring) ? expiring : [],
			topBalance: Array.isArray(topBalance) ? topBalance : [],
			blockedRecent: Array.isArray(blockedRecent) ? blockedRecent : [],
		};
		lastUpdated.value = new Date().toISOString();
		await checkWidgets();

		const anyCore = [totalUsers, activeTariffs, blockedUsers, processedPayments].some((v) => typeof v === 'number');
		if (!anyCore) {
			error.value = 'Часть данных недоступна. Проверь права роли и доступ к коллекциям/эндпоинтам.';
		}
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
:deep(.private-view) {
	width: 100%;
}

:deep(.private-view__main) {
	/* Directus иногда ограничивает ширину контента и визуально "пустит" справа. */
	max-width: none !important;
	width: 100% !important;
}

:deep(.private-view__content) {
	max-width: none !important;
	width: 100% !important;
	/* На некоторых брейкпоинтах Directus центрирует контент и не растягивает children */
	justify-content: stretch !important;
	justify-items: stretch !important;
	align-items: stretch !important;
}

:deep(.private-view__content > *) {
	max-width: none !important;
	width: 100% !important;
}

.page {
	padding: 16px 20px;
	max-width: 100%;
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(320px, 380px);
	gap: 12px;
	align-items: start;
	justify-items: stretch;
	width: 100%;
	min-width: 0;
}

.page__main {
	display: grid;
	gap: 12px;
	min-width: 0;
	align-items: stretch;
	justify-items: stretch;
}

.page__main > * {
	width: 100%;
	min-width: 0;
	justify-self: stretch;
}

.page__side {
	display: grid;
	gap: 12px;
	position: sticky;
	top: 12px;
	align-self: start;
	min-width: 0;
	align-items: stretch;
	justify-items: stretch;
}

.page__side > * {
	width: 100%;
	min-width: 0;
	justify-self: stretch;
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
	margin-bottom: 0;
}

.hero__title {
	font-size: 20px;
	font-weight: 700;
	line-height: 1.2;
}

.hero__subtitle {
	margin-top: 6px;
	opacity: 0.8;
	max-width: 720px;
	font-size: 13px;
}

.hero__meta-label {
	font-size: 12px;
	opacity: 0.7;
}

.hero__meta-value {
	margin-top: 2px;
	font-weight: 600;
}

.kpi-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
	gap: 12px;
	min-width: 0;
	justify-items: stretch;
}

.kpi-grid > * {
	width: 100%;
	min-width: 0;
	justify-self: stretch;
}

@media (min-width: 1600px) {
	.kpi-grid {
		grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
	}
}

.alerts {
	display: grid;
	gap: 8px;
}

.alerts__item {
	display: flex;
	gap: 8px;
	align-items: center;
	line-height: 1.25;
}

.trends {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
	gap: 12px;
	width: 100%;
	min-width: 0;
	/* Helps when the last row has only 1 card */
	grid-auto-flow: row dense;
}

/* If the last row has a single widget, stretch it full-width */
.trends > .trend:last-child {
	grid-column: 1 / -1;
}

.trend {
	padding: 10px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
	width: 100%;
	min-width: 0;
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
	flex-wrap: wrap;
	min-width: 0;
}

.trend__meta-label {
	opacity: 0.7;
}

.trend__meta-value {
	font-weight: 700;
}

.spark {
	width: 100%;
	height: 64px;
}

.spark--big {
	height: 120px;
}

.spark-wrap {
	position: relative;
	width: 100%;
	/* Allow vertical scroll on touch while still receiving pointer events */
	touch-action: pan-y;
}

.spark-wrap--big {
	margin-top: 6px;
}

.spark.premium {
	display: block;
	border-radius: 10px;
	/* A subtle chart "window" (as in premium dashboards). */
	background: linear-gradient(180deg, rgba(2, 6, 23, 0.28), rgba(2, 6, 23, 0.08));
	border: 1px solid rgba(255, 255, 255, 0.06);
}

.spark__area {
	/* Softer fill so it doesn't visually desync with the line. */
	opacity: 0.48;
}

.spark__path {
	fill: none;
	/* Keep it thin; avoid "fat" look on dark UI. */
	stroke-width: 1.85;
	stroke-linecap: round;
	stroke-linejoin: round;
	/* Drop-shadow makes the line look thicker than it is; glow path is enough. */
	filter: none;
	/* Prevent stroke from scaling if SVG ever gets resized. */
	vector-effect: non-scaling-stroke;
}

.spark--big .spark__path {
	stroke-width: 1.8;
}

.spark__path--green {
	stroke: rgba(16, 185, 129, 0.95);
}

.spark__path--blue {
	stroke: rgba(59, 130, 246, 0.95);
}

.spark__path--purple {
	stroke: rgba(139, 92, 246, 0.95);
}

.spark__path--cyan {
	stroke: rgba(34, 211, 238, 0.95);
}

.spark__glow {
	fill: none;
	stroke-width: 5;
	stroke-linecap: round;
	stroke-linejoin: round;
	opacity: 0.11;
	filter: blur(0.8px);
	vector-effect: non-scaling-stroke;
}

.spark--big .spark__glow {
	stroke-width: 4.5;
	opacity: 0.12;
}

.spark__glow--green {
	stroke: rgba(16, 185, 129, 0.9);
}

.spark__glow--blue {
	stroke: rgba(59, 130, 246, 0.9);
}

.spark__glow--purple {
	stroke: rgba(139, 92, 246, 0.9);
}

.spark__glow--cyan {
	stroke: rgba(34, 211, 238, 0.9);
}

.spark__vline {
	stroke: rgba(255, 255, 255, 0.14);
	stroke-width: 1;
	vector-effect: non-scaling-stroke;
}

.spark__dot-ring {
	fill: rgba(15, 23, 42, 0.65);
	stroke: rgba(255, 255, 255, 0.20);
	stroke-width: 1;
	vector-effect: non-scaling-stroke;
}

.spark__dot {
	stroke: rgba(255, 255, 255, 0.18);
	stroke-width: 0.8;
	vector-effect: non-scaling-stroke;
}

.spark__dot--green {
	fill: rgba(16, 185, 129, 1);
}

.spark__dot--blue {
	fill: rgba(59, 130, 246, 1);
}

.spark__dot--purple {
	fill: rgba(139, 92, 246, 1);
}

.spark__tooltip {
	position: absolute;
	top: -6px;
	transform: translate(-50%, -100%);
	padding: 8px 10px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.10);
	background: rgba(15, 23, 42, 0.92);
	backdrop-filter: blur(10px);
	box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
	pointer-events: none;
	white-space: nowrap;
	z-index: 5;
}

.spark__tooltip-title {
	font-size: 11px;
	opacity: 0.85;
}

.spark__tooltip-value {
	margin-top: 2px;
	font-weight: 800;
	font-size: 13px;
}

.spark__overlay {
	position: absolute;
	inset: 0;
	pointer-events: none;
	z-index: 4;
}

.spark__vline-overlay {
	position: absolute;
	top: 0;
	bottom: 0;
	width: 1px;
	background: rgba(255, 255, 255, 0.10);
	transform: translateX(-0.5px);
}

.spark__dot-overlay {
	position: absolute;
	border-radius: 999px;
	transform: translate(-50%, -50%);
	display: grid;
	place-items: center;
	background: transparent;
	border: 1.25px solid rgba(255, 255, 255, 0.18);
	/* Keep marker crisp; avoid "bloated" blob on dark cards. */
	box-shadow: none;
	filter: drop-shadow(0 8px 16px rgba(0, 0, 0, 0.25));
}

.spark-wrap:not(.spark-wrap--big) .spark__dot-overlay {
	width: 8px;
	height: 8px;
}

.spark-wrap--big .spark__dot-overlay {
	width: 10px;
	height: 10px;
}

.spark__dot-overlay-inner {
	border-radius: 999px;
	/* Clean inner dot with dark outline (reads better than white halo). */
	box-shadow: 0 0 0 1.25px rgba(2, 6, 23, 0.9);
}

.spark-wrap:not(.spark-wrap--big) .spark__dot-overlay-inner {
	width: 4px;
	height: 4px;
}

.spark-wrap--big .spark__dot-overlay-inner {
	width: 5px;
	height: 5px;
}

.spark__dot-overlay--green {
	border-color: rgba(16, 185, 129, 0.55);
	box-shadow: 0 0 16px rgba(16, 185, 129, 0.18);
}

.spark__dot-overlay--blue {
	border-color: rgba(59, 130, 246, 0.55);
	box-shadow: 0 0 16px rgba(59, 130, 246, 0.18);
}

.spark__dot-overlay--purple {
	border-color: rgba(139, 92, 246, 0.55);
	box-shadow: 0 0 16px rgba(139, 92, 246, 0.18);
}

.spark__dot-overlay--cyan {
	border-color: rgba(34, 211, 238, 0.55);
	box-shadow: 0 0 16px rgba(34, 211, 238, 0.18);
}

.spark__dot-overlay--green .spark__dot-overlay-inner {
	background: rgba(16, 185, 129, 1);
}

.spark__dot-overlay--blue .spark__dot-overlay-inner {
	background: rgba(59, 130, 246, 1);
}

.spark__dot-overlay--purple .spark__dot-overlay-inner {
	background: rgba(139, 92, 246, 1);
}

.spark__dot-overlay--cyan .spark__dot-overlay-inner {
	background: rgba(34, 211, 238, 1);
}

.big {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
	gap: 12px;
}

.big__item {
	padding: 12px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
	display: grid;
	gap: 10px;
	min-height: 188px;
}

.big__item--blue {
	background: linear-gradient(180deg, rgba(59, 130, 246, 0.06), rgba(255, 255, 255, 0.03));
}

.big__item--green {
	background: linear-gradient(180deg, rgba(16, 185, 129, 0.06), rgba(255, 255, 255, 0.03));
}

.big__item--purple {
	background: linear-gradient(180deg, rgba(139, 92, 246, 0.06), rgba(255, 255, 255, 0.03));
}

.big__item--cyan {
	background: linear-gradient(180deg, rgba(34, 211, 238, 0.06), rgba(255, 255, 255, 0.03));
}

.big__top {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 12px;
}

.big__label {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 650;
	flex-wrap: wrap;
}

.big__range {
	font-size: 12px;
	opacity: 0.7;
	padding: 2px 8px;
	border-radius: 999px;
	border: 1px solid rgba(255, 255, 255, 0.08);
	background: rgba(255, 255, 255, 0.02);
	white-space: nowrap;
}

.big__metrics {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	gap: 10px;
	align-items: end;
}

.big__total-label {
	font-size: 12px;
	opacity: 0.75;
}

.big__total-value {
	margin-top: 2px;
	font-size: 22px;
	font-weight: 850;
	letter-spacing: -0.01em;
	line-height: 1.05;
}

.big__mini {
	display: grid;
	gap: 4px;
	justify-items: end;
	font-size: 12px;
	opacity: 0.9;
}

.big__mini-row {
	display: flex;
	gap: 8px;
	align-items: baseline;
	white-space: nowrap;
}

.big__mini-k {
	opacity: 0.7;
}

.big__mini-v {
	font-weight: 750;
}

.spark__line {
	fill: none;
	stroke: rgba(16, 185, 129, 0.9);
	stroke-width: 3;
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
	grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
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
	overflow-wrap: anywhere;
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
	min-height: 76px;
	width: 100%;
	max-width: none !important;
	justify-self: stretch !important;
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

.kpi__icon :deep(i) {
	/* Чуть выразительнее на тёмной теме */
	transform: translateY(-0.5px);
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

.panel {
	display: grid;
	grid-template-columns: 1fr;
	padding: 14px;
	border-radius: 12px;
	overflow: hidden;
	width: 100%;
	max-width: none !important;
	box-sizing: border-box;
	justify-self: stretch !important;
}

.panel > * {
	width: 100%;
	min-width: 0;
	justify-self: stretch;
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
	grid-template-columns: 1fr;
	gap: 10px;
}

.widgets {
	display: grid;
	gap: 12px;
}

.widgets__block {
	padding: 10px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.03);
}

.widgets__title {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 650;
	margin-bottom: 8px;
}

.widgets__list {
	display: grid;
	gap: 6px;
}

.widgets__row {
	display: flex;
	justify-content: space-between;
	gap: 10px;
	padding: 8px 10px;
	border-radius: 10px;
	text-decoration: none;
	border: 1px solid rgba(255, 255, 255, 0.06);
	background: rgba(255, 255, 255, 0.02);
}

.widgets__row:hover {
	background: rgba(255, 255, 255, 0.04);
}

.widgets__name {
	font-weight: 600;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.widgets__meta {
	font-size: 12px;
	opacity: 0.75;
	white-space: nowrap;
}

.widgets__empty {
	opacity: 0.7;
	font-size: 12px;
}

.widgets__hint {
	margin-top: 8px;
	opacity: 0.75;
	font-size: 12px;
}

.settings {
	display: grid;
	gap: 10px;
}

.settings__row {
	display: grid;
	grid-template-columns: 1fr 120px;
	gap: 10px;
	align-items: center;
	font-size: 12px;
	opacity: 0.9;
}

.settings__row--textarea {
	grid-template-columns: 1fr;
}

.settings__input {
	width: 100%;
}

.settings__input--num {
	padding: 6px 8px;
	border-radius: 10px;
	border: 1px solid rgba(255, 255, 255, 0.10);
	background: rgba(255, 255, 255, 0.03);
	color: inherit;
}

.settings__input--textarea {
	min-height: 72px;
	padding: 8px 10px;
	border-radius: 10px;
	border: 1px solid rgba(255, 255, 255, 0.10);
	background: rgba(255, 255, 255, 0.03);
	color: inherit;
	resize: vertical;
}

.help__row {
	display: grid;
	grid-template-columns: 120px 1fr;
	gap: 10px;
	padding: 8px 0;
	border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.help__row:last-child {
	border-bottom: none;
}

.help__k {
	font-weight: 650;
}

.help__v {
	opacity: 0.75;
	font-size: 12px;
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

@media (max-width: 1500px) {
	.page {
		grid-template-columns: minmax(0, 1fr) 340px;
	}
}

@media (max-width: 1400px) {
	.page {
		grid-template-columns: 1fr;
	}

	.page__side {
		position: static;
		top: auto;
	}

	.trends {
		grid-template-columns: 1fr;
	}

	.events {
		grid-template-columns: 1fr;
	}

	.big {
		grid-template-columns: 1fr;
	}
}

@media (max-width: 720px) {
	.page {
		padding: 12px;
	}

	.hero {
		flex-direction: column;
	}

	.hero__subtitle {
		max-width: none;
	}

	.kpi-grid {
		grid-template-columns: 1fr;
	}

	.trend__head {
		flex-direction: column;
		align-items: flex-start;
	}

	.event {
		flex-direction: column;
	}

	.event__time {
		white-space: normal;
	}

	.help__row {
		grid-template-columns: 1fr;
	}

	.settings__row {
		grid-template-columns: 1fr;
	}

	.big__metrics {
		grid-template-columns: 1fr;
		align-items: start;
	}

	.big__mini {
		justify-items: start;
	}
}
</style>

