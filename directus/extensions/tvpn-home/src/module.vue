<template>
	<private-view title="Главная">
		<template #navigation>
			<div class="nav nav--premium">
				<div class="nav__brand">
					<div class="nav__brand-logo">SVPN</div>
						<div>
							<div class="nav__brand-title">Операционная панель</div>
							<div class="nav__brand-subtitle">Панель управления</div>
						</div>
				</div>

				<div v-for="section in navSections" :key="section.id" class="nav__section">
					<div class="nav__section-title">{{ section.title }}</div>
					<router-link
						v-for="item in section.items"
						:key="item.path"
						class="nav__item"
						:class="{ 'nav__item--active': isRouteActive(item.path) }"
						:to="{ path: item.path }"
					>
						<span class="nav__item-icon"><v-icon :name="item.icon" /></span>
						<span class="nav__item-label">{{ item.label }}</span>
						<span v-if="navBadge(item)" class="nav__item-badge">{{ navBadge(item) }}</span>
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
				<div class="home-hero">
					<div class="home-hero__left">
							<div class="home-hero__kicker">Vectra Connect</div>
							<div class="home-hero__title">Админ-панель Vectra Connect</div>
							<div class="home-hero__subtitle">
								Операционный обзор проекта: ключевые метрики, графики и основные инструменты управления в одном экране.
							</div>
						<div class="home-hero__cta-row">
							<router-link class="home-hero__cta home-hero__cta--primary" :to="{ path: '/content/users' }">
								<v-icon name="manage_accounts" />
								<span>Пользователи</span>
							</router-link>
								<router-link class="home-hero__cta home-hero__cta--ghost" :to="{ path: '/insights' }">
									<v-icon name="query_stats" />
									<span>Аналитика</span>
								</router-link>
						</div>
					</div>
					<div class="home-hero__right">
						<div class="home-hero__meta">
							<div class="home-hero__meta-label">Последнее обновление</div>
							<div class="home-hero__meta-value">{{ lastUpdatedLabel }}</div>
							<div class="home-hero__meta-stats">
								<div class="home-hero__meta-stat">
									<span>Подключения 7д</span>
									<strong>{{ fmt(stats.connections7d) }}</strong>
								</div>
								<div class="home-hero__meta-stat">
									<span>Регистрации 7д</span>
									<strong>{{ fmt(stats.registrations7d) }}</strong>
								</div>
								<div class="home-hero__meta-stat">
									<span>Платежи сегодня</span>
									<strong>{{ fmtMoney(trends.paymentsSumToday) }}</strong>
								</div>
							</div>
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

					<v-card class="kpi" :title="serviceTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--green">
								<v-icon name="cloud_done" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Трафик основной подписки (вчера)</div>
								<div class="kpi__value">{{ fmtGb(serviceLatest.main_paid?.traffic_gb) }}</div>
								<div class="kpi__hint">7д: {{ fmtGb(serviceWeekTotals.main_paid_traffic) }}</div>
							</div>
						</div>
					</v-card>

					<v-card class="kpi" :title="serviceTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--amber">
								<v-icon name="signal_cellular_alt" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Трафик LTE-подписки (вчера)</div>
								<div class="kpi__value">{{ fmtGb(serviceLatest.lte_paid?.traffic_gb) }}</div>
								<div class="kpi__hint">7д: {{ fmtGb(serviceWeekTotals.lte_paid_traffic) }}</div>
							</div>
						</div>
					</v-card>

					<v-card class="kpi" :title="serviceTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--purple">
								<v-icon name="payments" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Выручка осн. подписки (вчера)</div>
								<div class="kpi__value">{{ fmtMoney(serviceLatest.main_paid?.subscription_revenue_rub) }}</div>
								<div class="kpi__hint">7д: {{ fmtMoney(serviceWeekTotals.main_paid_revenue) }}</div>
							</div>
						</div>
					</v-card>

					<v-card class="kpi" :title="serviceTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--purple">
								<v-icon name="add_card" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Выручка LTE-топап (вчера)</div>
								<div class="kpi__value">{{ fmtMoney(serviceLatest.lte_paid?.lte_revenue_rub) }}</div>
								<div class="kpi__hint">7д: {{ fmtMoney(serviceWeekTotals.lte_paid_revenue) }}</div>
							</div>
						</div>
					</v-card>

					<v-card class="kpi" :title="trialTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--blue">
								<v-icon name="rocket_launch" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Триал: новые / трафик (вчера)</div>
								<div class="kpi__value">{{ fmt(trialLatest.new_trials) }} <span class="kpi__value-meta">· {{ fmtGb(trialLatest.traffic_gb) }}</span></div>
								<div class="kpi__hint">Сейчас на триале: {{ fmt(trialLatest.active_trials) }}</div>
							</div>
						</div>
					</v-card>

					<v-card class="kpi kpi--alert" :class="{ 'kpi--alert-active': hasActiveAbuseFlags }" :title="trialTooltip">
						<div class="kpi__row">
							<div class="kpi__icon kpi__icon--amber">
								<v-icon name="warning_amber" />
							</div>
							<div class="kpi__content">
								<div class="kpi__label">Подозрительных триалов</div>
								<div class="kpi__value">{{ fmt(abuseFlagsOpen.length) }}</div>
								<div class="kpi__hint" v-if="abuseFlagsOpen.length">Топ за день: {{ fmtGb(trialLatest.top_user_traffic_gb) }} · user&nbsp;{{ trialLatest.top_user_id || '—' }}</div>
								<div class="kpi__hint" v-else>За последние сутки чисто</div>
							</div>
						</div>
					</v-card>
				</div>

				<v-card v-if="false" class="panel panel--legacy">
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

			<v-card v-if="false" class="panel panel--legacy">
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

			<v-card class="panel panel--chart-hub">
				<div class="panel__head panel__head--chart">
					<div>
						<div class="panel__title">Операционный пульс (30 дней)</div>
						<div class="panel__subtitle">Главный график за месяц с быстрым переключением метрики.</div>
					</div>
					<div class="segmented segmented--compact">
						<button
							v-for="opt in pulseOptions"
							:key="opt.key"
							type="button"
							class="segmented__btn"
							:class="{ 'segmented__btn--active': pulseMetric === opt.key }"
							@click="pulseMetric = opt.key"
						>
							{{ opt.label }}
						</button>
					</div>
				</div>
				<div class="chart-hub__stats">
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Сегодня</div>
						<div class="chart-hub__value">{{ pulseChart.today }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">7 дней</div>
						<div class="chart-hub__value">{{ pulseChart.week }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Пик</div>
						<div class="chart-hub__value">{{ pulseChart.peak }}</div>
					</div>
				</div>
				<PremiumLineChart
					:height="320"
					:categories="pulseChart.categories"
					:series="pulseChart.seriesForChart"
					:value-formatter="pulseValueFormatter"
					mode="30d"
				/>
			</v-card>

			<v-card class="panel panel--chart-hub">
				<div class="panel__head panel__head--chart">
					<div>
						<div class="panel__title">Годовой обзор (12 месяцев)</div>
						<div class="panel__subtitle">Крупный график сезонности и роста по ключевым направлениям.</div>
					</div>
					<div class="segmented">
						<button
							v-for="opt in yearOptions"
							:key="opt.key"
							type="button"
							class="segmented__btn"
							:class="{ 'segmented__btn--active': yearMetric === opt.key }"
							@click="yearMetric = opt.key"
						>
							{{ opt.label }}
						</button>
					</div>
				</div>
				<div class="chart-hub__stats">
					<div class="chart-hub__stat">
						<div class="chart-hub__label">{{ yearChart.leadLabel }}</div>
						<div class="chart-hub__value">{{ yearChart.lead }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Среднее / мес</div>
						<div class="chart-hub__value">{{ yearChart.avg }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Пик</div>
						<div class="chart-hub__value">{{ yearChart.peak }}</div>
					</div>
				</div>
				<PremiumLineChart
					:height="340"
					:categories="yearChart.categories"
					:series="yearChart.seriesForChart"
					:value-formatter="yearValueFormatter"
					mode="12m"
				/>
			</v-card>

			<v-card class="panel panel--chart-hub">
				<div class="panel__head panel__head--chart">
					<div>
						<div class="panel__title">Услуга: трафик и выручка (30 дней)</div>
						<div class="panel__subtitle">Раздельно по основной подписке и LTE-топапам — рост usage против дохода.</div>
					</div>
					<div class="segmented segmented--compact">
						<button
							v-for="opt in serviceMetricOptions"
							:key="opt.key"
							type="button"
							class="segmented__btn"
							:class="{ 'segmented__btn--active': serviceMetric === opt.key }"
							@click="serviceMetric = opt.key"
						>
							{{ opt.label }}
						</button>
					</div>
				</div>
				<div class="chart-hub__stats">
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Сегодня</div>
						<div class="chart-hub__value">{{ serviceChart.today }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">7 дней</div>
						<div class="chart-hub__value">{{ serviceChart.week }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Пик</div>
						<div class="chart-hub__value">{{ serviceChart.peak }}</div>
					</div>
				</div>
				<PremiumLineChart
					:height="300"
					:categories="serviceChart.categories"
					:series="serviceChart.seriesForChart"
					:value-formatter="serviceValueFormatter"
					mode="30d"
				/>
				<v-notice v-if="!hasServiceData" type="info" class="panel__notice">
					Сборщик аналитики ещё не наполнил `analytics_service_daily`. Первый прогон ~раз в час, ночная сверка в 03:00 МСК.
				</v-notice>
			</v-card>

			<v-card class="panel panel--chart-hub">
				<div class="panel__head panel__head--chart">
					<div>
						<div class="panel__title">Триал-активность (30 дней)</div>
						<div class="panel__subtitle">Новые триалы и расход трафика — резкий пик трафика без роста новых = вероятный абузер.</div>
					</div>
					<div class="segmented segmented--compact">
						<button
							v-for="opt in trialMetricOptions"
							:key="opt.key"
							type="button"
							class="segmented__btn"
							:class="{ 'segmented__btn--active': trialMetric === opt.key }"
							@click="trialMetric = opt.key"
						>
							{{ opt.label }}
						</button>
					</div>
				</div>
				<div class="chart-hub__stats">
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Сегодня</div>
						<div class="chart-hub__value">{{ trialChart.today }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">7 дней</div>
						<div class="chart-hub__value">{{ trialChart.week }}</div>
					</div>
					<div class="chart-hub__stat">
						<div class="chart-hub__label">Пик</div>
						<div class="chart-hub__value">{{ trialChart.peak }}</div>
					</div>
				</div>
				<PremiumLineChart
					:height="280"
					:categories="trialChart.categories"
					:series="trialChart.seriesForChart"
					:value-formatter="trialValueFormatter"
					mode="30d"
				/>
				<v-notice v-if="!hasTrialData" type="info" class="panel__notice">
					Сборщик аналитики ещё не наполнил `analytics_trial_daily`.
				</v-notice>
			</v-card>

			<v-card class="panel panel--alerts">
				<div class="panel__head">
					<div>
						<div class="panel__title">Подозрительные триалы</div>
						<div class="panel__subtitle">Авто-флаги из `analytics_trial_risk_flags`. Алерт уходит в админ-чат при создании.</div>
					</div>
					<router-link class="panel__head-link" :to="{ path: '/content/analytics_trial_risk_flags' }">
						<v-icon name="open_in_new" left />
						<span>Все флаги</span>
					</router-link>
				</div>
				<div v-if="abuseFlagsOpen.length === 0" class="alerts__empty">
					<v-icon name="check_circle" />
					<span>Открытых флагов нет.</span>
				</div>
				<div v-else class="abuse-list">
					<router-link
						v-for="flag in abuseFlagsOpen"
						:key="flag.id"
						class="abuse-row"
						:class="`abuse-row--${flag.severity}`"
						:to="{ path: `/content/users/${flag.user_id}` }"
					>
						<div class="abuse-row__severity">
							<v-icon :name="flag.severity === 'critical' ? 'error' : 'warning_amber'" />
						</div>
						<div class="abuse-row__main">
							<div class="abuse-row__top">
								<span class="abuse-row__user">user&nbsp;{{ flag.user_id }}</span>
								<span class="abuse-row__day">{{ shortDate(flag.day) }}</span>
							</div>
							<div class="abuse-row__reason">{{ abuseReasonLabel(flag.reason) }}</div>
						</div>
						<div class="abuse-row__metrics">
							<div class="abuse-row__gb">{{ fmtGb(flag.traffic_gb) }}</div>
							<div class="abuse-row__share">{{ fmtPercent(flag.share_pct, 100) }} от триал-трафика</div>
						</div>
					</router-link>
				</div>
			</v-card>

			<v-card class="panel panel--activity">
				<RecentActivityBoard
					:users="events.users"
					:payments="events.payments"
					:promo="events.promo"
					:fmt-money="fmtMoney"
					:short-date="shortDate"
					:from-now="fromNow"
				/>
			</v-card>

			<div class="ops-grid">
				<v-card class="panel panel--ops-summary">
					<div class="panel__title">Админ-инструменты</div>
					<div class="panel__subtitle">Оперативный контроль состояния и ключевых параметров проекта.</div>
					<div class="ops-toolbar">
						<div class="ops-toolbar__meta">
							Один refresh: ~{{ refreshRequestEstimate }} API-запросов (оптимизировано для меньшей burst-нагрузки).
						</div>
						<v-button x-small secondary :loading="loading" :disabled="loading" @click="refresh">
							<v-icon name="refresh" left />
							Обновить данные
						</v-button>
					</div>
					<div class="ops-health">
						<div class="ops-health__item">
							<div class="ops-health__label">Техработы</div>
							<div class="ops-badge" :class="settings.maintenance_mode ? 'ops-badge--warn' : 'ops-badge--ok'">
								{{ settings.maintenance_mode ? 'Включены' : 'Выключены' }}
							</div>
						</div>
						<div class="ops-health__item">
							<div class="ops-health__label">Алерты</div>
							<div class="ops-badge" :class="settings.alerts_enabled ? 'ops-badge--ok' : 'ops-badge--muted'">
								{{ settings.alerts_enabled ? 'Активны' : 'Отключены' }}
							</div>
						</div>
						<div class="ops-health__item">
							<div class="ops-health__label">Истечения (окно)</div>
							<div class="ops-health__value">{{ settings.expiring_days }} дн</div>
						</div>
						<div class="ops-health__item">
							<div class="ops-health__label">Блокировки (окно)</div>
							<div class="ops-health__value">{{ settings.suspicious_block_days }} дн</div>
						</div>
					</div>
				</v-card>

				<v-card class="panel panel--ops-notify">
					<div class="panel__head panel__head--compact">
						<div>
							<div class="panel__title">Создать уведомление</div>
							<div class="panel__subtitle">Быстрое создание In-App уведомления для Mini App прямо с главной.</div>
						</div>
						<router-link class="inline-link" :to="{ path: '/content/in_app_notifications' }">
							Все уведомления
						</router-link>
					</div>

					<v-notice v-if="notificationError" type="danger">
						{{ notificationError }}
					</v-notice>
					<v-notice v-if="notificationSuccess" type="success">
						<div class="notification-result">
							<span>{{ notificationSuccess }}</span>
							<router-link
								v-if="notificationLastId"
								class="inline-link"
								:to="{ path: `/content/in_app_notifications/${notificationLastId}` }"
							>
								Открыть карточку
							</router-link>
						</div>
					</v-notice>

					<div class="notification-presets">
						<button type="button" class="notification-presets__btn" @click="applyNotificationWindow(24)">Окно 24ч</button>
						<button type="button" class="notification-presets__btn" @click="applyNotificationWindow(72)">Окно 3 дня</button>
						<button type="button" class="notification-presets__btn" @click="applyNotificationWindow(168)">Окно 7 дней</button>
					</div>

					<div class="notification-form">
						<label class="notification-form__field notification-form__field--wide">
							<span>Заголовок</span>
							<input
								v-model.trim="notificationForm.title"
								class="notification-form__input"
								type="text"
								maxlength="255"
								placeholder="Например: Техработы завершены"
							/>
						</label>
						<label class="notification-form__field notification-form__field--wide">
							<span>Текст уведомления</span>
							<textarea
								v-model.trim="notificationForm.body"
								class="notification-form__textarea"
								rows="3"
								placeholder="Например: Обновление завершено, приложение работает в штатном режиме."
							/>
						</label>
						<label class="notification-form__field">
							<span>Показывать с</span>
							<input v-model="notificationForm.start_at" class="notification-form__input" type="datetime-local" />
						</label>
						<label class="notification-form__field">
							<span>Показывать до</span>
							<input v-model="notificationForm.end_at" class="notification-form__input" type="datetime-local" />
						</label>
						<label class="notification-form__field notification-form__field--checkbox">
							<span>Активно сразу</span>
							<input v-model="notificationForm.is_active" class="notification-form__toggle" type="checkbox" />
						</label>
						<label class="notification-form__field">
							<span>Макс. показов на пользователя</span>
							<input
								v-model="notificationForm.max_per_user"
								class="notification-form__input"
								type="number"
								min="1"
								placeholder="пусто = без лимита"
							/>
						</label>
						<label class="notification-form__field">
							<span>Макс. показов за сессию</span>
							<input
								v-model="notificationForm.max_per_session"
								class="notification-form__input"
								type="number"
								min="1"
								placeholder="пусто = без лимита"
							/>
						</label>
						<label class="notification-form__field">
							<span>Автоскрытие (сек)</span>
							<input
								v-model="notificationForm.auto_hide_seconds"
								class="notification-form__input"
								type="number"
								min="1"
								placeholder="пусто = закрытие вручную"
							/>
						</label>
					</div>
					<div class="notification-form__hint">
						Пустые лимиты означают «без ограничений». Время указывается в локальной timezone браузера.
					</div>
					<div class="settings__actions">
						<v-button small :loading="notificationSaving" :disabled="notificationSaving" @click="createInAppNotification">
							Создать уведомление
						</v-button>
						<v-button small secondary :disabled="notificationSaving" @click="resetNotificationForm">
							Очистить
						</v-button>
					</div>
				</v-card>

				<v-card class="panel panel--ops-maintenance">
					<div class="panel__title">Режим обслуживания</div>
					<div class="panel__subtitle">Управление техработами и сообщением для клиентов приложения.</div>
					<v-notice v-if="settingsAccessHint" type="warning">
						{{ settingsAccessHint }}
					</v-notice>
					<v-notice v-if="settingsSaveError" type="danger">
						{{ settingsSaveError }}
					</v-notice>
					<div class="settings settings--wide">
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
					</div>
					<div class="settings__actions">
						<v-button small :loading="settingsSaving" :disabled="settingsSaving || settingsReadOnly" @click="saveSettings">Сохранить параметры</v-button>
					</div>
				</v-card>

				<v-card class="panel panel--ops-thresholds">
					<div class="panel__title">Пороги мониторинга и очереди</div>
					<div class="panel__subtitle">Чувствительность алертов и окна выборок для оперативных виджетов.</div>
					<div class="settings settings--wide">
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
					</div>
					<div class="settings__actions">
						<v-button small :loading="settingsSaving" :disabled="settingsSaving || settingsReadOnly" @click="saveSettings">Сохранить параметры</v-button>
					</div>
				</v-card>

				<v-card class="panel panel--ops-hwid">
					<div class="panel__head panel__head--compact">
						<div>
							<div class="panel__title">Очистить HWID</div>
							<div class="panel__subtitle">Break-glass инструмент: удаляет HWID из локальной anti-twink истории и из известных привязок RemnaWave.</div>
						</div>
						<div class="ops-badge ops-badge--warn">Осторожно</div>
					</div>

					<v-notice type="warning">
						Используйте только когда нужно вручную разблокировать конкретное устройство после удаления пользователя или зависших HWID-следов.
					</v-notice>

					<v-notice v-if="hwidPreviewError" type="danger">
						{{ hwidPreviewError }}
					</v-notice>
					<v-notice v-if="hwidPurgeError" type="danger">
						{{ hwidPurgeError }}
					</v-notice>
					<v-notice v-if="hwidPurgeSummary" :type="hwidPurgeResult?.partial ? 'warning' : 'success'">
						<div class="hwid-tool__notice">
							<span>
								{{ hwidPurgeResult?.partial ? 'Очистка выполнена частично.' : 'Очистка выполнена.' }}
								Локально удалено {{ fmt(hwidPurgeSummary.local_history_deleted) }},
								в RemnaWave: {{ fmt(hwidPurgeSummary.remnawave_deleted) }} удалено,
								{{ fmt(hwidPurgeSummary.remnawave_already_absent) }} уже отсутствовало,
								{{ fmt(hwidPurgeSummary.remnawave_user_missing) }} пользователей уже не найдено.
							</span>
						</div>
					</v-notice>

					<div class="notification-form hwid-tool__form">
						<label class="notification-form__field notification-form__field--wide">
							<span>HWID</span>
							<input
								v-model.trim="hwidTool.hwid"
								class="notification-form__input"
								type="text"
								maxlength="255"
								placeholder="Например: m42usbkzlcie4x4f"
								@input="handleHwidInputChange"
							/>
						</label>
						<label class="notification-form__field notification-form__field--wide">
							<span>Причина (опционально)</span>
							<textarea
								v-model.trim="hwidTool.reason"
								class="notification-form__textarea"
								rows="3"
								placeholder="Например: stale anti-twink block after admin delete"
							/>
						</label>
					</div>

					<label class="hwid-tool__confirm">
						<input v-model="hwidTool.confirm" class="hwid-tool__confirm-input" type="checkbox" />
						<span>Понимаю, что это удалит историю anti-twink для этого HWID.</span>
					</label>

					<div class="hwid-tool__actions">
						<v-button small secondary :loading="hwidPreviewLoading" :disabled="!hwidCanPreview" @click="previewHwidPurge">
							Проверить
						</v-button>
						<v-button small :loading="hwidPurgeLoading" :disabled="!hwidCanPurge" @click="purgeHwidEverywhere">
							Очистить везде
						</v-button>
					</div>

					<div v-if="hwidPreview" class="hwid-tool__preview">
						<div class="hwid-tool__preview-head">
							<div class="hwid-tool__preview-title">Предпросмотр для {{ hwidPreview.hwid }}</div>
							<div class="hwid-tool__preview-subtitle">Ниже показан контекст, найденный перед очисткой.</div>
						</div>

						<div class="ops-health">
							<div class="ops-health__item">
								<div class="ops-health__label">Локальных записей</div>
								<div class="ops-health__value">{{ fmt(hwidPreview.summary?.local_history_rows ?? 0) }}</div>
							</div>
							<div class="ops-health__item">
								<div class="ops-health__label">Live совпадений RemnaWave</div>
								<div class="ops-health__value">{{ fmt(hwidPreview.summary?.remnawave_live_matches ?? 0) }}</div>
							</div>
							<div class="ops-health__item">
								<div class="ops-health__label">Найдено владельцев</div>
								<div class="ops-health__value">{{ fmt(hwidPreview.summary?.owners ?? 0) }}</div>
							</div>
							<div class="ops-health__item">
								<div class="ops-health__label">Связанных локальных users</div>
								<div class="ops-health__value">{{ fmt(hwidPreview.summary?.local_users ?? 0) }}</div>
							</div>
						</div>

						<div v-if="!hwidPreview.summary?.has_matches" class="hwid-tool__empty">
							По текущему preview совпадения не найдены.
						</div>

						<v-notice v-if="hwidPreview.remnawave_scan && hwidPreview.remnawave_scan.ok === false" type="warning">
							Live-скан RemnaWave завершился с ошибкой:
							{{ hwidPreview.remnawave_scan.error || 'unknown error' }}.
							Локальная история всё равно была проверена.
						</v-notice>

						<div v-if="hwidOwners.length" class="hwid-tool__owners">
							<div v-for="owner in hwidOwners" :key="owner.user_uuid" class="hwid-tool__owner">
								<div class="hwid-tool__owner-head">
									<div>
										<div class="hwid-tool__owner-title">{{ formatHwidOwnerTitle(owner) }}</div>
										<div class="hwid-tool__owner-meta">{{ formatHwidOwnerMeta(owner) }}</div>
									</div>
									<div class="hwid-tool__owner-badges">
										<span v-if="owner.source_local_history" class="hwid-tool__badge">local history</span>
										<span v-if="owner.source_remnawave_live" class="hwid-tool__badge hwid-tool__badge--accent">RemnaWave live</span>
										<span v-if="owner.used_trial" class="hwid-tool__badge hwid-tool__badge--warn">used trial</span>
										<span v-if="owner.is_trial" class="hwid-tool__badge">trial</span>
									</div>
								</div>
								<div class="hwid-tool__owner-grid">
									<div class="hwid-tool__owner-row">
										<span>Локальных строк</span>
										<strong>{{ fmt(owner.local_history_rows ?? 0) }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Live совпадений</span>
										<strong>{{ fmt(owner.live_matches ?? 0) }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Последний local seen</span>
										<strong>{{ owner.local_last_seen_at || '—' }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Последний live seen</span>
										<strong>{{ owner.live_last_seen_at || '—' }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Платформы</span>
										<strong>{{ formatListInline(owner.live_platforms) }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Устройства</span>
										<strong>{{ formatListInline(owner.live_device_models) }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>expireAt</span>
										<strong>{{ owner.expired_at || '—' }}</strong>
									</div>
									<div class="hwid-tool__owner-row">
										<span>Active tariff</span>
										<strong>{{ owner.active_tariff_id || '—' }}</strong>
									</div>
								</div>
							</div>
						</div>

						<div v-if="Array.isArray(hwidPurgeResult?.remnawave_results) && hwidPurgeResult.remnawave_results.length" class="hwid-tool__results">
							<div class="hwid-tool__results-title">Результат попыток удаления в RemnaWave</div>
							<div class="hwid-tool__results-list">
								<div v-for="item in hwidPurgeResult.remnawave_results" :key="`${item.user_uuid}-${item.status}`" class="hwid-tool__result-row">
									<span>{{ item.user_uuid }}</span>
									<strong>{{ formatHwidPurgeStatus(item.status) }}</strong>
								</div>
							</div>
						</div>
					</div>
				</v-card>

				<v-card class="panel panel--ops-console">
				<div class="panel__title">Сервисные команды</div>
					<div class="panel__subtitle">Безопасные серверные команды (whitelisted) без shell-доступа.</div>
					<v-notice v-if="opsError" type="danger">
						{{ opsError }}
					</v-notice>
					<div class="ops-console">
						<label class="ops-console__label">
							<span>Команда</span>
							<select v-model="opsCommandId" class="ops-console__select">
								<option v-for="cmd in opsCommands" :key="cmd.id" :value="cmd.id">
									{{ cmd.label }}
								</option>
							</select>
						</label>
						<div class="ops-console__actions">
							<v-button small :loading="opsLoading" :disabled="opsLoading || !opsCommandId" @click="runOpsCommand">Выполнить</v-button>
						</div>
						<pre v-if="opsOutput" class="ops-console__output">{{ opsOutput }}</pre>
					</div>
				</v-card>

				<v-card class="panel panel--ops-wide panel--ops-links">
					<div class="panel__title">Управление основными параметрами</div>
					<div class="panel__subtitle">Основные параметры проекта доступны отсюда, ручное управление в `/content/*` сохраняется.</div>
					<div class="control-links">
						<router-link v-for="item in controlLinks" :key="item.path" class="control-link" :to="{ path: item.path }">
							<div class="control-link__head">
								<v-icon :name="item.icon" />
								<span>{{ item.title }}</span>
							</div>
							<div class="control-link__desc">{{ item.desc }}</div>
							<div class="control-link__path">{{ item.path }}</div>
						</router-link>
					</div>
				</v-card>

				<v-card class="panel panel--ops-wide panel--ops-map">
					<div class="panel__title">Что где находится</div>
					<div class="panel__subtitle">Короткая карта разделов.</div>
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
							<div class="help__k">Платежи</div>
							<div class="help__v">Processed payments + статусы, чтобы ловить аномалии.</div>
						</div>
					</div>
				</v-card>
			</div>
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

						<router-link class="action" :to="{ path: '/content/in_app_notifications' }">
							<v-icon name="notifications_active" />
							<div>
								<div class="action__title">In-App уведомления</div>
								<div class="action__desc">Список и ручная правка уведомлений Mini App</div>
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

				<v-card v-if="false" class="panel panel--legacy">
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

				<v-card v-if="false" class="panel panel--legacy">
					<div class="panel__title">Сервисные команды</div>
					<div class="panel__subtitle">Безопасные серверные операции из Directus (без полного shell-доступа).</div>

					<v-notice v-if="opsError" type="danger">
						{{ opsError }}
					</v-notice>

					<div class="ops-console">
						<label class="ops-console__label">
							<span>Команда</span>
							<select v-model="opsCommandId" class="ops-console__select">
								<option v-for="cmd in opsCommands" :key="cmd.id" :value="cmd.id">
									{{ cmd.label }}
								</option>
							</select>
						</label>

						<div class="ops-console__actions">
							<v-button small :loading="opsLoading" :disabled="opsLoading || !opsCommandId" @click="runOpsCommand">
								Выполнить
							</v-button>
						</div>

						<pre v-if="opsOutput" class="ops-console__output">{{ opsOutput }}</pre>
					</div>
				</v-card>

				<v-card v-if="false" class="panel panel--legacy">
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

				<v-card v-if="false" class="panel panel--legacy">
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
import { useRoute } from 'vue-router';
import { useApi } from '@directus/extensions-sdk';
import PremiumLineChart from './components/PremiumLineChart.vue';
import RecentActivityBoard from './components/RecentActivityBoard.vue';

const api = useApi();
const route = useRoute();

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
const settingsAccess = ref({
	checked: false,
	canRead: true,
	canUpdate: true,
});
const opsLoading = ref(false);
const opsError = ref('');
const opsOutput = ref('');
const hwidTool = ref({
	hwid: '',
	reason: '',
	confirm: false,
});
const hwidPreviewLoading = ref(false);
const hwidPreviewError = ref('');
const hwidPreview = ref(null);
const hwidPurgeLoading = ref(false);
const hwidPurgeError = ref('');
const hwidPurgeResult = ref(null);
const notificationSaving = ref(false);
const notificationError = ref('');
const notificationSuccess = ref('');
const notificationLastId = ref(null);
const opsCommandId = ref('fk_active_tariffs');
const opsCommands = [
	{ id: 'fk_users_overview', label: 'Проверить все FK -> users' },
	{ id: 'fk_active_tariffs', label: 'Проверить fk_active_tariffs_user' },
	{ id: 'fix_fk_active_tariffs', label: 'Исправить fk_active_tariffs_user (CASCADE)' },
	{ id: 'family_quick_health', label: 'Проверить Family-раздел (счётчики)' },
];
const refreshRequestEstimate = 24;
const NOTIFICATION_DEFAULT_WINDOW_HOURS = 24;

const settingsReadOnly = computed(() => settingsAccess.value.checked && !settingsAccess.value.canUpdate);
const normalizedHwidInput = computed(() => String(hwidTool.value.hwid || '').trim());
const hwidPreviewIsCurrent = computed(() => {
	return Boolean(hwidPreview.value && hwidPreview.value.hwid === normalizedHwidInput.value);
});
const hwidCanPreview = computed(() => {
	return Boolean(normalizedHwidInput.value) && !hwidPreviewLoading.value && !hwidPurgeLoading.value;
});
const hwidCanPurge = computed(() => {
	return (
		Boolean(normalizedHwidInput.value) &&
		hwidPreviewIsCurrent.value &&
		Boolean(hwidTool.value.confirm) &&
		!hwidPreviewLoading.value &&
		!hwidPurgeLoading.value
	);
});
const hwidOwners = computed(() => (Array.isArray(hwidPreview.value?.owners) ? hwidPreview.value.owners : []));
const hwidPurgeSummary = computed(() => {
	return hwidPurgeResult.value && typeof hwidPurgeResult.value.summary === 'object'
		? hwidPurgeResult.value.summary
		: null;
});

const settingsAccessHint = computed(() => {
	if (!settingsAccess.value.checked) return '';
	if (!settingsAccess.value.canRead && !settingsAccess.value.canUpdate) {
		return 'Коллекция `tvpn_admin_settings` недоступна для текущей роли. Панель использует локальные значения по умолчанию, сохранение отключено.';
	}
	if (!settingsAccess.value.canRead) {
		return 'Чтение `tvpn_admin_settings` недоступно. Панель использует локальные значения по умолчанию.';
	}
	if (!settingsAccess.value.canUpdate) {
		return 'Настройки `tvpn_admin_settings` доступны только для чтения. Сохранение отключено.';
	}
	return '';
});

function toDateTimeLocalValue(value) {
	const d = value instanceof Date ? value : new Date(value);
	if (Number.isNaN(d.getTime())) return '';
	const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
	return local.toISOString().slice(0, 16);
}

function buildNotificationFormDefault(windowHours = NOTIFICATION_DEFAULT_WINDOW_HOURS) {
	const now = new Date();
	const end = new Date(now.getTime() + windowHours * 60 * 60 * 1000);
	return {
		title: '',
		body: '',
		start_at: toDateTimeLocalValue(now),
		end_at: toDateTimeLocalValue(end),
		is_active: true,
		max_per_user: '',
		max_per_session: '',
		auto_hide_seconds: '',
	};
}

const notificationForm = ref(buildNotificationFormDefault());

const controlLinks = [
	{
		path: '/content/tariffs',
		icon: 'sell',
		title: 'Тарифы',
		desc: 'Цена, длительности, семейные варианты и базовые параметры монетизации.',
	},
	{
		path: '/content/active_tariffs',
		icon: 'subscriptions',
		title: 'Активные тарифы',
		desc: 'Оперативное управление статусами, лимитами usage и клиентскими привязками.',
	},
	{
		path: '/content/promo_codes',
		icon: 'confirmation_number',
		title: 'Промокоды',
		desc: 'Запуск акций, ограничение выдачи и контроль актуальных промо-кампаний.',
	},
	{
		path: '/content/users',
		icon: 'people',
		title: 'Пользователи',
		desc: 'Подписка, блокировки, баланс и персональные параметры в карточке пользователя.',
	},
	{
		path: '/content/processed_payments',
		icon: 'payments',
		title: 'Платежи',
		desc: 'Контроль статусов и суммы операций, быстрая проверка проблемных транзакций.',
	},
		{
			path: '/content/error_reports',
			icon: 'bug_report',
			title: 'Ошибки',
			desc: 'Триаж инцидентов и контроль технического состояния проекта.',
		},
		{
			path: '/content/in_app_notifications',
			icon: 'notifications_active',
			title: 'In-App уведомления',
			desc: 'Планирование и управление всплывающими уведомлениями Mini App.',
		},
		{
			path: '/content/promo_usages',
			icon: 'bolt',
			title: 'Использования промо',
			desc: 'Проверка фактического использования промокодов и связей с пользователями.',
		},
];

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

// Service-growth analytics rows are fetched directly from Directus tables
// populated by bloobcat/tasks/service_growth_analytics.py (hourly + nightly reconcile).
const serviceDaily = ref({
	main_paid: [],
	lte_paid: [],
	all_paid: [],
});
const trialDaily = ref([]);
const abuseFlagsOpen = ref([]);

const serviceMetricOptions = [
	{ key: 'main_paid_traffic', label: 'Трафик осн.', color: '#22c55e', kind: 'gb' },
	{ key: 'lte_paid_traffic', label: 'Трафик LTE', color: '#f59e0b', kind: 'gb' },
	{ key: 'main_paid_revenue', label: 'Выручка осн.', color: '#a855f7', kind: 'money' },
	{ key: 'lte_paid_revenue', label: 'Выручка LTE', color: '#8b5cf6', kind: 'money' },
];
const serviceMetric = ref('main_paid_traffic');

const trialMetricOptions = [
	{ key: 'new_trials', label: 'Новые триалы', color: '#3b82f6', kind: 'count' },
	{ key: 'traffic_gb', label: 'Триал-трафик', color: '#06b6d4', kind: 'gb' },
	{ key: 'flagged_users_count', label: 'Флаги абузеров', color: '#f43f5e', kind: 'count' },
];
const trialMetric = ref('traffic_gb');

const serviceTooltip = 'Источник: analytics_service_daily (по МСК-дням, обновляется ~раз в час).';
const trialTooltip = 'Источник: analytics_trial_daily / analytics_trial_risk_flags. Пороги настраиваются в tvpn_admin_settings.';

const ABUSE_REASON_LABELS = {
	daily_traffic_warning: 'Превышен дневной порог (warning)',
	daily_traffic_critical: 'Превышен дневной порог (critical)',
	trial_traffic_share_spike: 'Доминирующая доля триал-трафика',
};
function abuseReasonLabel(reason) {
	return ABUSE_REASON_LABELS[reason] || reason || '—';
}

const statsOk = computed(() => Object.values(stats.value).some((v) => typeof v === 'number'));
const widgetsOk = ref(false);

// --- Service-growth analytics computed ---
function lastDailyRow(rows) {
	if (!Array.isArray(rows) || rows.length === 0) return null;
	return rows[rows.length - 1] || null;
}

const serviceLatest = computed(() => ({
	main_paid: lastDailyRow(serviceDaily.value.main_paid),
	lte_paid: lastDailyRow(serviceDaily.value.lte_paid),
	all_paid: lastDailyRow(serviceDaily.value.all_paid),
}));

function sumLastN(rows, field, n = 7) {
	if (!Array.isArray(rows)) return 0;
	const tail = rows.slice(Math.max(0, rows.length - n));
	let total = 0;
	for (const row of tail) {
		const v = Number(row?.[field]);
		if (Number.isFinite(v)) total += v;
	}
	return total;
}

const serviceWeekTotals = computed(() => ({
	main_paid_traffic: sumLastN(serviceDaily.value.main_paid, 'traffic_gb'),
	lte_paid_traffic: sumLastN(serviceDaily.value.lte_paid, 'traffic_gb'),
	main_paid_revenue: sumLastN(serviceDaily.value.main_paid, 'subscription_revenue_rub'),
	lte_paid_revenue: sumLastN(serviceDaily.value.lte_paid, 'lte_revenue_rub'),
}));

const trialLatest = computed(() => {
	const last = lastDailyRow(trialDaily.value);
	return {
		new_trials: last?.new_trials ?? 0,
		active_trials: last?.active_trials ?? 0,
		traffic_gb: last?.traffic_gb ?? 0,
		top_user_id: last?.top_user_id ?? null,
		top_user_traffic_gb: last?.top_user_traffic_gb ?? 0,
		flagged_users_count: last?.flagged_users_count ?? 0,
	};
});

const hasServiceData = computed(() => {
	return ['main_paid', 'lte_paid', 'all_paid'].some((p) => Array.isArray(serviceDaily.value[p]) && serviceDaily.value[p].length > 0);
});
const hasTrialData = computed(() => Array.isArray(trialDaily.value) && trialDaily.value.length > 0);
const hasActiveAbuseFlags = computed(() => abuseFlagsOpen.value.length > 0);

function serviceCategoriesLabels() {
	const rows = serviceDaily.value.all_paid;
	if (!Array.isArray(rows) || rows.length === 0) return [];
	return rows.map((r) => formatLabel(String(r.day || ''), 'day'));
}
function trialCategoriesLabels() {
	if (!hasTrialData.value) return [];
	return trialDaily.value.map((r) => formatLabel(String(r.day || ''), 'day'));
}

function pickServiceSeries(metricKey) {
	const opt = serviceMetricOptions.find((m) => m.key === metricKey) || serviceMetricOptions[0];
	const productMap = {
		main_paid_traffic: { product: 'main_paid', field: 'traffic_gb' },
		lte_paid_traffic: { product: 'lte_paid', field: 'traffic_gb' },
		main_paid_revenue: { product: 'main_paid', field: 'subscription_revenue_rub' },
		lte_paid_revenue: { product: 'lte_paid', field: 'lte_revenue_rub' },
	};
	const map = productMap[metricKey] || productMap.main_paid_traffic;
	const rows = serviceDaily.value[map.product] || [];
	return rows.map((r) => Number(r?.[map.field]) || 0);
}

const serviceChart = computed(() => {
	const opt = serviceMetricOptions.find((m) => m.key === serviceMetric.value) || serviceMetricOptions[0];
	const series = pickServiceSeries(serviceMetric.value);
	const fmtFn = opt.kind === 'money' ? fmtMoney : (opt.kind === 'gb' ? fmtGb : fmt);
	return {
		categories: serviceCategoriesLabels(),
		seriesForChart: [{ name: opt.label, data: series, color: opt.color }],
		today: fmtFn(series.length ? series[series.length - 1] : 0),
		week: fmtFn(sumInSeries(series, 7)),
		peak: fmtFn(maxInSeries(series)),
		formatter: fmtFn,
	};
});

const trialChart = computed(() => {
	const opt = trialMetricOptions.find((m) => m.key === trialMetric.value) || trialMetricOptions[0];
	const rows = trialDaily.value;
	const series = (rows || []).map((r) => Number(r?.[opt.key]) || 0);
	const fmtFn = opt.kind === 'money' ? fmtMoney : (opt.kind === 'gb' ? fmtGb : fmt);
	return {
		categories: trialCategoriesLabels(),
		seriesForChart: [{ name: opt.label, data: series, color: opt.color }],
		today: fmtFn(series.length ? series[series.length - 1] : 0),
		week: fmtFn(sumInSeries(series, 7)),
		peak: fmtFn(maxInSeries(series)),
		formatter: fmtFn,
	};
});

function serviceValueFormatter(value) { return serviceChart.value.formatter(value); }
function trialValueFormatter(value) { return trialChart.value.formatter(value); }

const navSections = computed(() => [
	{
		id: 'overview',
		title: 'Обзор',
		items: [
			{ path: '/tvpn-home', icon: 'space_dashboard', label: 'Главная', badgeKey: null },
			{ path: '/insights', icon: 'query_stats', label: 'Аналитика', badgeKey: null },
		],
	},
	{
		id: 'users',
		title: 'Пользователи',
		items: [
			{ path: '/content/users', icon: 'people', label: 'Пользователи', badgeKey: 'users' },
			{ path: '/content/active_tariffs', icon: 'subscriptions', label: 'Активные тарифы', badgeKey: 'activeTariffs' },
			{ path: '/content/tariffs', icon: 'sell', label: 'Тарифы', badgeKey: null },
		],
	},
	{
		id: 'money',
		title: 'Монетизация',
		items: [
			{ path: '/content/processed_payments', icon: 'payments', label: 'Платежи', badgeKey: 'payments' },
			{ path: '/content/promo_codes', icon: 'confirmation_number', label: 'Промокоды', badgeKey: null },
			{ path: '/content/promo_batches', icon: 'inventory_2', label: 'Партии промокодов', badgeKey: null },
		],
	},
		{
			id: 'system',
			title: 'Система',
			items: [
				{ path: '/content/error_reports', icon: 'bug_report', label: 'Логи ошибок', badgeKey: 'blockedRecent' },
				{ path: '/content/in_app_notifications', icon: 'notifications_active', label: 'In-App уведомления', badgeKey: null },
			],
		},
]);

function navBadge(item) {
	if (!item?.badgeKey) return '';
	if (item.badgeKey === 'users') return compactMetric(stats.value.totalUsers);
	if (item.badgeKey === 'activeTariffs') return compactMetric(stats.value.activeTariffs);
	if (item.badgeKey === 'payments') return compactMetric(stats.value.processedPayments);
	if (item.badgeKey === 'blockedRecent') return compactMetric(quick.value.blockedRecent.length);
	return '';
}

function isRouteActive(path) {
	if (!path) return false;
	if (path === '/tvpn-home') return route.path === '/tvpn-home';
	return route.path === path || route.path.startsWith(`${path}/`);
}

const pulseOptions = [
	{ key: 'connections', label: 'Подключения', color: '#22c55e' },
	{ key: 'registrations', label: 'Регистрации', color: '#3b82f6' },
	{ key: 'payments', label: 'Платежи', color: '#a855f7' },
];
const pulseMetric = ref('connections');

const yearOptions = [
	{ key: 'registrations', label: 'Регистрации', color: '#3b82f6' },
	{ key: 'connections', label: 'Подключения', color: '#22c55e' },
	{ key: 'payments', label: 'Платежи', color: '#a855f7' },
	{ key: 'activeUsers', label: 'Активные пользователи', color: '#06b6d4' },
];
const yearMetric = ref('registrations');

const pulseChart = computed(() => {
	if (pulseMetric.value === 'registrations') {
		const series = trends.value.registrations30d || [];
		return {
			categories: (trends.value.registrations30d_labels || []).map((x) => formatLabel(x, 'day')),
			seriesForChart: [{ name: 'Регистрации', data: series, color: '#3b82f6' }],
			today: fmt(trends.value.registrationsToday),
			week: fmt(stats.value.registrations7d),
			peak: fmt(maxInSeries(series)),
			formatter: fmt,
		};
	}
	if (pulseMetric.value === 'payments') {
		const series = trends.value.paymentsSum30d || [];
		return {
			categories: (trends.value.paymentsSum30d_labels || []).map((x) => formatLabel(x, 'day')),
			seriesForChart: [{ name: 'Платежи (сумма)', data: series, color: '#a855f7' }],
			today: fmtMoney(trends.value.paymentsSumToday),
			week: fmtMoney(sumInSeries(series, 7)),
			peak: fmtMoney(maxInSeries(series)),
			formatter: fmtMoney,
		};
	}
	const series = trends.value.connections30d || [];
	return {
		categories: (trends.value.connections30d_labels || []).map((x) => formatLabel(x, 'day')),
		seriesForChart: [{ name: 'Подключения', data: series, color: '#22c55e' }],
		today: fmt(trends.value.connectionsToday),
		week: fmt(stats.value.connections7d),
		peak: fmt(maxInSeries(series)),
		formatter: fmt,
	};
});

const yearChart = computed(() => {
	if (yearMetric.value === 'connections') {
		return {
			categories: (year.value.connections12m_labels || []).map((x) => formatLabel(x, 'month')),
			seriesForChart: [{ name: 'Подключения', data: year.value.connections12m || [], color: '#22c55e' }],
			leadLabel: 'Итого',
			lead: fmt(Math.round(bigStats.value.connections12.sum)),
			avg: fmt(Math.round(bigStats.value.connections12.avg)),
			peak: fmt(Math.round(bigStats.value.connections12.max)),
			formatter: fmt,
		};
	}
	if (yearMetric.value === 'payments') {
		return {
			categories: (year.value.paymentsSum12m_labels || []).map((x) => formatLabel(x, 'month')),
			seriesForChart: [{ name: 'Платежи (сумма)', data: year.value.paymentsSum12m || [], color: '#a855f7' }],
			leadLabel: 'Итого',
			lead: fmtMoney(Math.round(bigStats.value.paymentsSum12.sum)),
			avg: fmtMoney(Math.round(bigStats.value.paymentsSum12.avg)),
			peak: fmtMoney(Math.round(bigStats.value.paymentsSum12.max)),
			formatter: fmtMoney,
		};
	}
	if (yearMetric.value === 'activeUsers') {
		return {
			categories: (year.value.activeUsers12m_labels || []).map((x) => formatLabel(x, 'month')),
			seriesForChart: [{ name: 'Активные пользователи', data: year.value.activeUsers12m || [], color: '#06b6d4' }],
			leadLabel: 'Сейчас',
			lead: fmt(Math.round(bigStats.value.activeUsers12.last)),
			avg: fmt(Math.round(bigStats.value.activeUsers12.avg)),
			peak: fmt(Math.round(bigStats.value.activeUsers12.max)),
			formatter: fmt,
		};
	}
	return {
		categories: (year.value.registrations12m_labels || []).map((x) => formatLabel(x, 'month')),
		seriesForChart: [{ name: 'Регистрации', data: year.value.registrations12m || [], color: '#3b82f6' }],
		leadLabel: 'Итого',
		lead: fmt(Math.round(bigStats.value.registrations12.sum)),
		avg: fmt(Math.round(bigStats.value.registrations12.avg)),
		peak: fmt(Math.round(bigStats.value.registrations12.max)),
		formatter: fmt,
	};
});

function pulseValueFormatter(value) {
	return pulseChart.value.formatter(value);
}

function yearValueFormatter(value) {
	return yearChart.value.formatter(value);
}

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

function compactMetric(value) {
	const n = Number(value);
	if (!Number.isFinite(n) || n <= 0) return '';
	if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
	if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}K`;
	return Math.round(n).toString();
}

function fmtGb(value) {
	const n = Number(value);
	if (!Number.isFinite(n)) return '—';
	if (n >= 1000) return `${(n / 1000).toFixed(2)} TB`;
	if (n >= 100) return `${Math.round(n)} GB`;
	if (n >= 10) return `${n.toFixed(1)} GB`;
	return `${n.toFixed(2)} GB`;
}

function fmtPercent(used, total) {
	const u = Number(used);
	const t = Number(total);
	if (!Number.isFinite(u) || !Number.isFinite(t) || t <= 0) return '—';
	const pct = (u / t) * 100;
	if (!Number.isFinite(pct)) return '—';
	return `${pct.toFixed(pct >= 10 ? 0 : 1)}%`;
}

function clearHwidToolFeedback({ preservePreview = false, preservePurgeResult = false } = {}) {
	hwidPreviewError.value = '';
	hwidPurgeError.value = '';
	if (!preservePreview) hwidPreview.value = null;
	if (!preservePurgeResult) hwidPurgeResult.value = null;
}

function handleHwidInputChange() {
	hwidTool.value.confirm = false;
	clearHwidToolFeedback();
}

function formatHwidOwnerTitle(owner) {
	if (owner?.full_name) return owner.full_name;
	if (owner?.username) return `@${owner.username}`;
	if (owner?.telegram_user_id != null) return `Пользователь #${owner.telegram_user_id}`;
	return 'Неизвестный владелец';
}

function formatHwidOwnerMeta(owner) {
	const parts = [];
	if (owner?.telegram_user_id != null) parts.push(`ID: ${owner.telegram_user_id}`);
	if (owner?.user_uuid) parts.push(`UUID: ${owner.user_uuid}`);
	return parts.join(' • ') || 'Без локального пользователя';
}

function formatListInline(values, fallback = '—') {
	if (!Array.isArray(values) || values.length === 0) return fallback;
	return values.filter(Boolean).join(', ') || fallback;
}

function formatHwidPurgeStatus(status) {
	if (status === 'deleted') return 'Удалено';
	if (status === 'already_absent') return 'Уже отсутствовал';
	if (status === 'user_missing') return 'Пользователь уже отсутствует';
	if (status === 'error') return 'Ошибка';
	return status || 'Неизвестно';
}

async function previewHwidPurge() {
	if (!hwidCanPreview.value) {
		hwidPreviewError.value = 'Укажите HWID для проверки.';
		return;
	}
	hwidPreviewLoading.value = true;
	clearHwidToolFeedback();
	try {
		const res = await api.post('/server-ops/hwid/preview', {
			hwid: normalizedHwidInput.value,
		});
		const preview = res?.data?.preview;
		if (!preview || typeof preview !== 'object') {
			throw new Error('Directus не вернул preview HWID.');
		}
		hwidPreview.value = preview;
		hwidTool.value.confirm = false;
	} catch (e) {
		const status = e?.response?.status;
		const detail = e?.response?.data?.error || e?.response?.data?.errors?.[0]?.message || e?.message || '';
		if (status === 403) {
			hwidPreviewError.value = 'Недостаточно прав: операция доступна только администраторам Directus.';
		} else if (status === 400) {
			hwidPreviewError.value = detail || 'Некорректный HWID.';
		} else if (status === 503) {
			hwidPreviewError.value = 'Не настроен ADMIN_INTEGRATION_URL / ADMIN_INTEGRATION_TOKEN в Directus.';
		} else {
			hwidPreviewError.value = detail ? `Не удалось получить preview: ${detail}` : 'Не удалось получить preview HWID.';
		}
	} finally {
		hwidPreviewLoading.value = false;
	}
}

async function purgeHwidEverywhere() {
	if (!hwidCanPurge.value) {
		if (!normalizedHwidInput.value) {
			hwidPurgeError.value = 'Укажите HWID.';
		} else if (!hwidPreviewIsCurrent.value) {
			hwidPurgeError.value = 'Сначала выполните проверку для текущего HWID.';
		} else if (!hwidTool.value.confirm) {
			hwidPurgeError.value = 'Подтвердите удаление anti-twink истории для этого HWID.';
		}
		return;
	}

	hwidPurgeLoading.value = true;
	hwidPurgeError.value = '';
	hwidPurgeResult.value = null;
	try {
		const res = await api.post('/server-ops/hwid/purge', {
			hwid: normalizedHwidInput.value,
			reason: String(hwidTool.value.reason || '').trim() || null,
		});
		const result = res?.data?.result;
		if (!result || typeof result !== 'object') {
			throw new Error('Directus не вернул результат очистки HWID.');
		}
		hwidPurgeResult.value = result;
		hwidTool.value.confirm = false;
	} catch (e) {
		const status = e?.response?.status;
		const detail = e?.response?.data?.error || e?.response?.data?.errors?.[0]?.message || e?.message || '';
		if (status === 403) {
			hwidPurgeError.value = 'Недостаточно прав: очистка доступна только администраторам Directus.';
		} else if (status === 400) {
			hwidPurgeError.value = detail || 'Некорректный HWID.';
		} else if (status === 503) {
			hwidPurgeError.value = 'Не настроен ADMIN_INTEGRATION_URL / ADMIN_INTEGRATION_TOKEN в Directus.';
		} else {
			hwidPurgeError.value = detail ? `Не удалось очистить HWID: ${detail}` : 'Не удалось очистить HWID.';
		}
	} finally {
		hwidPurgeLoading.value = false;
	}
}

function sumInSeries(series, take = 7) {
	if (!Array.isArray(series) || !series.length) return 0;
	const tail = series.slice(Math.max(0, series.length - take));
	return tail.reduce((sum, raw) => {
		const n = Number(raw);
		return Number.isFinite(n) ? sum + n : sum;
	}, 0);
}

function maxInSeries(series) {
	if (!Array.isArray(series) || !series.length) return 0;
	const nums = series.map((v) => Number(v)).filter((v) => Number.isFinite(v));
	return nums.length ? Math.max(...nums) : 0;
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

function resetNotificationForm() {
	notificationForm.value = buildNotificationFormDefault();
	notificationError.value = '';
	notificationSuccess.value = '';
	notificationLastId.value = null;
}

function applyNotificationWindow(hours) {
	const value = Number(hours);
	const safe = Number.isFinite(value) && value >= 1 ? value : NOTIFICATION_DEFAULT_WINDOW_HOURS;
	notificationForm.value = {
		...notificationForm.value,
		...buildNotificationFormDefault(safe),
		title: notificationForm.value.title,
		body: notificationForm.value.body,
		is_active: notificationForm.value.is_active,
		max_per_user: notificationForm.value.max_per_user,
		max_per_session: notificationForm.value.max_per_session,
		auto_hide_seconds: notificationForm.value.auto_hide_seconds,
	};
}

function optionalPositiveInt(raw) {
	if (raw === null || raw === undefined || String(raw).trim() === '') return null;
	const n = Number(raw);
	if (!Number.isFinite(n)) return null;
	const rounded = Math.floor(n);
	return rounded >= 1 ? rounded : null;
}

async function createInAppNotification() {
	if (notificationSaving.value) return;
	notificationSaving.value = true;
	notificationError.value = '';
	notificationSuccess.value = '';
	notificationLastId.value = null;
	try {
		const title = String(notificationForm.value.title || '').trim();
		const body = String(notificationForm.value.body || '').trim();
		if (!title || !body) {
			notificationError.value = 'Заполните заголовок и текст уведомления.';
			notificationSaving.value = false;
			return;
		}

		const startAt = new Date(notificationForm.value.start_at);
		const endAt = new Date(notificationForm.value.end_at);
		if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime())) {
			notificationError.value = 'Укажите корректные дату/время начала и окончания.';
			notificationSaving.value = false;
			return;
		}
		if (endAt < startAt) {
			notificationError.value = 'Дата окончания должна быть позже даты начала.';
			notificationSaving.value = false;
			return;
		}

		const payload = {
			title: title.slice(0, 255),
			body,
			start_at: startAt.toISOString(),
			end_at: endAt.toISOString(),
			is_active: Boolean(notificationForm.value.is_active),
			max_per_user: optionalPositiveInt(notificationForm.value.max_per_user),
			max_per_session: optionalPositiveInt(notificationForm.value.max_per_session),
			auto_hide_seconds: optionalPositiveInt(notificationForm.value.auto_hide_seconds),
		};

		const res = await api.post('/items/in_app_notifications', payload);
		const row = Array.isArray(res?.data?.data) ? res.data.data[0] : res?.data?.data;
		const createdId = row?.id ?? null;
		notificationLastId.value = createdId;
		notificationSuccess.value = createdId
			? `Уведомление #${createdId} успешно создано.`
			: 'Уведомление успешно создано.';
		notificationForm.value = {
			...buildNotificationFormDefault(),
			is_active: notificationForm.value.is_active,
		};
	} catch (e) {
		const status = e?.response?.status;
		const detail = e?.response?.data?.errors?.[0]?.message || e?.response?.data?.error || e?.message || '';
		if (status === 403) {
			notificationError.value = 'Недостаточно прав на создание in_app_notifications (нужно create).';
		} else if (status === 404) {
			notificationError.value = 'Коллекция in_app_notifications не найдена. Запустите scripts/directus_super_setup.py.';
		} else if (!e?.response) {
			notificationError.value = 'Ошибка сети при создании уведомления.';
		} else {
			notificationError.value = detail
				? `Не удалось создать уведомление: ${detail}`
				: 'Не удалось создать уведомление.';
		}
	} finally {
		notificationSaving.value = false;
	}
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

async function fetchSum(collection, field, params = {}) {
	const res = await api.get(`/items/${collection}`, {
		params: { [`aggregate[sum]`]: field, ...params },
	});
	const row = Array.isArray(res?.data?.data) ? res.data.data[0] : null;
	const raw = row?.sum?.[field] ?? row?.sum;
	const n = Number(raw);
	return Number.isFinite(n) ? n : null;
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

async function loadSettingsAccess() {
	if (settingsAccess.value.checked) return;
	try {
		const res = await api.get('/permissions/me');
		const payload = res?.data?.data;
		const entry = payload && typeof payload === 'object' ? payload.tvpn_admin_settings : null;
		const canRead = Boolean(entry?.read?.access);
		const canUpdate = Boolean(entry?.update?.access);
		settingsAccess.value = {
			checked: true,
			canRead,
			canUpdate,
		};
	} catch {
		// If the permission snapshot can't be loaded, keep the optimistic default.
	}
}

async function loadSettings() {
	settingsSaveError.value = '';
	if (settingsAccess.value.checked && !settingsAccess.value.canRead) return;
	try {
		const res = await api.get('/items/tvpn_admin_settings');
		const payload = res?.data?.data;
		const row = Array.isArray(payload) ? payload[0] : payload;
		if (!row) return;
		settingsId.value = row.id ?? null;
		settings.value = { ...settings.value, ...row };
	} catch {
		// Fallback for Directus setups where singleton route is enforced.
		try {
			const res = await api.get('/items/tvpn_admin_settings/singleton');
			const row = res?.data?.data;
			if (!row) return;
			settingsId.value = row.id ?? null;
			settings.value = { ...settings.value, ...row };
		} catch {
			// It's optional; admins may choose to keep defaults.
		}
	}
}

async function saveSettings() {
	if (settingsSaving.value) return;
	if (settingsReadOnly.value) {
		settingsSaveError.value = settingsAccessHint.value || 'Недостаточно прав на сохранение `tvpn_admin_settings`.';
		return;
	}
	settingsSaving.value = true;
	settingsSaveError.value = '';
	const payload = { ...settings.value };
	delete payload.id;
	try {
		// Try the most common singleton routes first; environments differ.
		try {
			await api.patch('/items/tvpn_admin_settings', payload);
		} catch (firstErr) {
			try {
				await api.patch('/items/tvpn_admin_settings/singleton', payload);
			} catch {
				throw firstErr;
			}
		}
		await loadSettings();
	} catch (mainErr) {
		const status = mainErr?.response?.status;
		const detail = mainErr?.response?.data?.errors?.[0]?.message || mainErr?.response?.data?.error || mainErr?.message || '';
		// Fallback for non-singleton/legacy environments.
		try {
			if (settingsId.value) {
				await api.patch(`/items/tvpn_admin_settings/${settingsId.value}`, payload);
				await loadSettings();
				settingsSaving.value = false;
				return;
			}
		} catch {
			// Ignore and show normalized error below.
		}
		if (status === 401) {
			settingsSaveError.value = 'Сессия Directus истекла. Обновите страницу и войдите заново.';
		} else if (status === 403) {
			settingsSaveError.value = 'Недостаточно прав на сохранение `tvpn_admin_settings` (нужно право update).';
		} else if (status === 404) {
			settingsSaveError.value = 'Коллекция `tvpn_admin_settings` не найдена. Запустите scripts/directus_super_setup.py.';
		} else if (!mainErr?.response) {
			settingsSaveError.value = 'Ошибка сети при сохранении. Проверьте доступность Directus.';
		} else {
			settingsSaveError.value = detail
				? `Не удалось сохранить настройки: ${detail}`
				: 'Не удалось сохранить настройки. Проверьте права роли и наличие коллекции.';
		}
	}
	settingsSaving.value = false;
}

async function runOpsCommand() {
	if (opsLoading.value || !opsCommandId.value) return;
	opsLoading.value = true;
	opsError.value = '';
	try {
		const res = await api.post('/server-ops/run', {
			commandId: opsCommandId.value,
		});
		const payload = res?.data ?? {};
		opsOutput.value = JSON.stringify(payload, null, 2);
	} catch (e) {
		const status = e?.response?.status;
		const detail = e?.response?.data?.error || e?.response?.data?.errors?.[0]?.message || e?.message || '';
		if (status === 403) {
			opsError.value = 'Недостаточно прав: доступно только администраторам Directus.';
		} else {
			opsError.value = detail ? `Ошибка выполнения команды: ${detail}` : 'Не удалось выполнить команду.';
		}
	} finally {
		opsLoading.value = false;
	}
}

async function refresh() {
	if (loading.value) return;
	loading.value = true;
	error.value = '';
	try {
		await loadSettingsAccess();
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
			// Service-growth analytics: pre-computed daily totals split by product.
			fetchItems('analytics_service_daily', {
				fields: 'day,traffic_gb,subscription_revenue_rub,lte_revenue_rub,lte_gb_purchased,payments_count,paying_users,rub_per_gb',
				'filter[product][_eq]': 'main_paid',
				sort: 'day',
				limit: 30,
			}),
			fetchItems('analytics_service_daily', {
				fields: 'day,traffic_gb,subscription_revenue_rub,lte_revenue_rub,lte_gb_purchased,payments_count,paying_users,rub_per_gb',
				'filter[product][_eq]': 'lte_paid',
				sort: 'day',
				limit: 30,
			}),
			fetchItems('analytics_service_daily', {
				fields: 'day,traffic_gb,subscription_revenue_rub,lte_revenue_rub,lte_gb_purchased,payments_count,paying_users,rub_per_gb',
				'filter[product][_eq]': 'all_paid',
				sort: 'day',
				limit: 30,
			}),
			fetchItems('analytics_trial_daily', {
				fields: 'day,new_trials,active_trials,traffic_gb,top_user_id,top_user_traffic_gb,flagged_users_count',
				sort: 'day',
				limit: 30,
			}),
			fetchItems('analytics_trial_risk_flags', {
				fields: 'id,user_id,day,traffic_gb,share_pct,reason,severity,status,created_at',
				'filter[status][_eq]': 'new',
				sort: '-created_at',
				limit: 10,
			}),
		]);

		const values = settled.map((r) => (r.status === 'fulfilled' ? r.value : null));
		const [
			totalUsers,
			activeTariffs,
			blockedUsers,
			processedPayments,
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
			serviceMain,
			serviceLte,
			serviceAll,
			trialDailyRows,
			abuseFlagsRows,
		] = values;

		const connections7d = Array.isArray(connections30?.series) && connections30.series.length ? sumInSeries(connections30.series, 7) : null;
		const registrations7d = Array.isArray(registrations30?.series) && registrations30.series.length ? sumInSeries(registrations30.series, 7) : null;

		stats.value = {
			totalUsers,
			activeTariffs,
			blockedUsers,
			processedPayments,
			connections7d,
			registrations7d,
		};

		serviceDaily.value = {
			main_paid: Array.isArray(serviceMain) ? serviceMain : [],
			lte_paid: Array.isArray(serviceLte) ? serviceLte : [],
			all_paid: Array.isArray(serviceAll) ? serviceAll : [],
		};
		trialDaily.value = Array.isArray(trialDailyRows) ? trialDailyRows : [];
		abuseFlagsOpen.value = Array.isArray(abuseFlagsRows) ? abuseFlagsRows : [];
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
		widgetsOk.value = [connections30, registrations30, activeUsers30, totalUsers30, payments30].some((widget) => Array.isArray(widget?.series) && widget.series.length > 0);
		lastUpdated.value = new Date().toISOString();

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

:deep(.private-view__navigation) {
	min-width: 0;
}

.page {
	--tvpn-radius: 14px;
	--tvpn-page-base: var(--tvpn-home-page-base, #0f172a);
	--tvpn-page-glow-a: var(--tvpn-home-page-glow-a, rgba(59, 130, 246, 0.12));
	--tvpn-page-glow-b: var(--tvpn-home-page-glow-b, rgba(16, 185, 129, 0.08));
	--tvpn-text: var(--tvpn-home-text, #c9d1d9);
	--tvpn-text-soft: var(--tvpn-home-text-soft, rgba(201, 209, 217, 0.72));
	--tvpn-border: var(--tvpn-home-border, rgba(148, 163, 184, 0.18));
	--tvpn-border-strong: var(--tvpn-home-border-strong, rgba(96, 165, 250, 0.34));
	--tvpn-surface-soft: var(--tvpn-home-surface-soft, rgba(22, 27, 34, 0.88));
	--tvpn-surface-strong: var(--tvpn-home-surface-strong, rgba(33, 38, 46, 0.94));
	--tvpn-surface-muted: var(--tvpn-home-surface-muted, rgba(255, 255, 255, 0.03));
	--tvpn-surface-muted-hover: var(--tvpn-home-surface-muted-hover, rgba(255, 255, 255, 0.055));
	--tvpn-surface-elevated: var(--tvpn-home-surface-elevated, rgba(2, 6, 23, 0.32));
	--tvpn-surface-elevated-strong: var(--tvpn-home-surface-elevated-strong, rgba(2, 6, 23, 0.45));
	--tvpn-input-bg: var(--tvpn-home-input-bg, rgba(255, 255, 255, 0.03));
	--tvpn-input-border: var(--tvpn-home-input-border, rgba(255, 255, 255, 0.10));
	--tvpn-section-border: var(--tvpn-home-section-border, rgba(255, 255, 255, 0.08));
	--tvpn-row-border: var(--tvpn-home-row-border, rgba(255, 255, 255, 0.06));
	--tvpn-shadow-soft: var(--tvpn-home-shadow-soft, 0 8px 24px rgba(2, 6, 23, 0.08));
	--tvpn-shadow-strong: var(--tvpn-home-shadow-strong, 0 14px 30px rgba(2, 6, 23, 0.12));
	--tvpn-card-shadow: var(--tvpn-home-card-shadow, 0 10px 24px rgba(2, 6, 23, 0.12));
	--tvpn-chart-bg: var(--tvpn-home-chart-bg, radial-gradient(circle at 0 0, rgba(120, 174, 255, 0.14), rgba(6, 14, 28, 0.72) 56%), linear-gradient(180deg, rgba(7, 16, 32, 0.98), rgba(7, 14, 30, 0.88)));
	--tvpn-chart-border: var(--tvpn-home-chart-border, rgba(147, 188, 255, 0.22));
	--tvpn-chart-shadow: var(--tvpn-home-chart-shadow, inset 0 0 0 1px rgba(15, 28, 52, 0.45), 0 24px 60px rgba(0, 0, 0, 0.35));
	--tvpn-chart-axis: var(--tvpn-home-chart-axis, rgba(215, 231, 255, 0.70));
	--tvpn-chart-axis-strong: var(--tvpn-home-chart-axis-strong, rgba(255, 255, 255, 0.14));
	--tvpn-chart-grid: var(--tvpn-home-chart-grid, rgba(255, 255, 255, 0.07));
	--tvpn-chart-tooltip-bg: var(--tvpn-home-chart-tooltip-bg, rgba(8, 16, 32, 0.94));
	--tvpn-chart-tooltip-border: var(--tvpn-home-chart-tooltip-border, rgba(255, 255, 255, 0.18));
	--tvpn-chart-tooltip-text: var(--tvpn-home-chart-tooltip-text, #eaf2ff);
	--tvpn-chart-empty-text: var(--tvpn-home-chart-empty-text, rgba(215, 231, 255, 0.56));
	--tvpn-accent-soft: var(--tvpn-home-accent-soft, rgba(59, 130, 246, 0.08));
	--tvpn-accent-strong: var(--tvpn-home-accent-strong, rgba(59, 130, 246, 0.18));
	--tvpn-accent-gradient: var(--tvpn-home-accent-gradient, linear-gradient(120deg, rgba(59, 130, 246, 0.18), rgba(16, 185, 129, 0.12)));
	--tvpn-activity-eyebrow-text: var(--tvpn-home-activity-eyebrow-text, rgba(191, 219, 254, 0.96));
	--tvpn-activity-eyebrow-bg: var(--tvpn-home-activity-eyebrow-bg, rgba(59, 130, 246, 0.16));
	--tvpn-activity-eyebrow-border: var(--tvpn-home-activity-eyebrow-border, rgba(96, 165, 250, 0.24));
	padding: 16px 20px 20px;
	max-width: 100%;
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(320px, 380px);
	gap: 16px;
	align-items: start;
	justify-items: stretch;
	width: 100%;
	min-width: 0;
	color: var(--tvpn-text);
	background:
		radial-gradient(circle at 0 0, var(--tvpn-page-glow-a), transparent 28%),
		radial-gradient(circle at 100% 0, var(--tvpn-page-glow-b), transparent 24%),
		var(--tvpn-page-base);
}

:global(body.light) {
	--tvpn-home-page-base: #eef3f8;
	--tvpn-home-page-glow-a: rgba(59, 130, 246, 0.10);
	--tvpn-home-page-glow-b: rgba(16, 185, 129, 0.06);
	--tvpn-home-text: #182334;
	--tvpn-home-text-soft: rgba(51, 65, 85, 0.76);
	--tvpn-home-border: rgba(148, 163, 184, 0.22);
	--tvpn-home-border-strong: rgba(59, 130, 246, 0.30);
	--tvpn-home-surface-soft: rgba(255, 255, 255, 0.88);
	--tvpn-home-surface-strong: rgba(255, 255, 255, 0.96);
	--tvpn-home-surface-muted: rgba(248, 250, 252, 0.92);
	--tvpn-home-surface-muted-hover: rgba(241, 245, 249, 0.96);
	--tvpn-home-surface-elevated: rgba(241, 245, 249, 0.94);
	--tvpn-home-surface-elevated-strong: rgba(226, 232, 240, 0.96);
	--tvpn-home-input-bg: rgba(255, 255, 255, 0.98);
	--tvpn-home-input-border: rgba(148, 163, 184, 0.24);
	--tvpn-home-section-border: rgba(148, 163, 184, 0.18);
	--tvpn-home-row-border: rgba(148, 163, 184, 0.16);
	--tvpn-home-shadow-soft: 0 12px 24px rgba(15, 23, 42, 0.08);
	--tvpn-home-shadow-strong: 0 18px 30px rgba(15, 23, 42, 0.12);
	--tvpn-home-card-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
	--tvpn-home-chart-bg: radial-gradient(circle at 0 0, rgba(96, 165, 250, 0.18), rgba(255, 255, 255, 0.96) 56%), linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.98));
	--tvpn-home-chart-border: rgba(148, 163, 184, 0.28);
	--tvpn-home-chart-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.75), 0 20px 44px rgba(15, 23, 42, 0.08);
	--tvpn-home-chart-axis: rgba(71, 85, 105, 0.84);
	--tvpn-home-chart-axis-strong: rgba(148, 163, 184, 0.36);
	--tvpn-home-chart-grid: rgba(148, 163, 184, 0.16);
	--tvpn-home-chart-tooltip-bg: rgba(255, 255, 255, 0.98);
	--tvpn-home-chart-tooltip-border: rgba(148, 163, 184, 0.28);
	--tvpn-home-chart-tooltip-text: #182334;
	--tvpn-home-chart-empty-text: rgba(71, 85, 105, 0.68);
	--tvpn-home-accent-soft: rgba(59, 130, 246, 0.08);
	--tvpn-home-accent-strong: rgba(59, 130, 246, 0.14);
	--tvpn-home-accent-gradient: linear-gradient(120deg, rgba(59, 130, 246, 0.16), rgba(16, 185, 129, 0.10));
	--tvpn-home-activity-eyebrow-text: #1d4ed8;
	--tvpn-home-activity-eyebrow-bg: rgba(59, 130, 246, 0.10);
	--tvpn-home-activity-eyebrow-border: rgba(96, 165, 250, 0.20);
}

.page__main {
	display: grid;
	gap: 16px;
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
	gap: 16px;
	position: sticky;
	top: 16px;
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

.home-hero {
	display: flex;
	align-items: flex-start;
	justify-content: space-between;
	gap: 16px;
	padding: 20px;
	border-radius: var(--tvpn-radius);
	background:
		linear-gradient(145deg, rgba(59, 130, 246, 0.12), rgba(16, 185, 129, 0.05)),
		var(--tvpn-surface-strong);
	border: 1px solid var(--tvpn-border-strong);
	margin-bottom: 0;
	box-shadow: var(--tvpn-shadow-soft);
	min-height: 0;
	height: auto;
}

.home-hero__left,
.home-hero__right {
	min-width: 0;
}

.home-hero__left {
	flex: 1 1 560px;
}

.home-hero__right {
	flex: 0 0 min(340px, 40%);
}

.home-hero__meta {
	padding: 12px 14px;
	border-radius: 12px;
	background: var(--tvpn-surface-soft);
	border: 1px solid var(--tvpn-border);
	box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.home-hero__title {
	font-size: clamp(21px, 2.2vw, 26px);
	font-weight: 700;
	line-height: 1.2;
	letter-spacing: -0.015em;
}

.home-hero__subtitle {
	margin-top: 8px;
	opacity: 0.8;
	max-width: 720px;
	font-size: 13px;
	line-height: 1.5;
}

.home-hero__meta-label {
	font-size: 12px;
	color: var(--tvpn-text-soft);
}

.home-hero__meta-value {
	margin-top: 2px;
	font-weight: 600;
}

.kpi-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
	gap: 13px;
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
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
	width: 100%;
	min-width: 0;
	box-shadow: var(--tvpn-shadow-soft);
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

.kpi {
	padding: 14px;
	border-radius: 12px;
	min-height: 76px;
	width: 100%;
	max-width: none !important;
	justify-self: stretch !important;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
	box-shadow: var(--tvpn-shadow-soft);
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

.kpi__value-meta {
	font-size: 14px;
	font-weight: 500;
	opacity: 0.65;
	margin-left: 4px;
}

.kpi__hint {
	font-size: 11px;
	margin-top: 4px;
	opacity: 0.7;
}

.kpi--alert {
	border-color: var(--tvpn-border);
}
.kpi--alert.kpi--alert-active {
	border-color: rgba(244, 63, 94, 0.55);
	box-shadow: 0 0 0 1px rgba(244, 63, 94, 0.18) inset, 0 6px 18px rgba(244, 63, 94, 0.18);
}

.panel__notice {
	margin-top: 10px;
}

.panel__head-link {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	color: var(--tvpn-text-soft);
	text-decoration: none;
	font-size: 12px;
	padding: 6px 10px;
	border-radius: 8px;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-muted);
}
.panel__head-link:hover {
	background: var(--tvpn-surface-muted-hover);
	color: var(--tvpn-text);
}

.panel--alerts {
	display: grid;
	gap: 12px;
}

.alerts__empty {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	padding: 10px 12px;
	border-radius: 10px;
	background: var(--tvpn-surface-muted);
	border: 1px dashed var(--tvpn-border);
	font-size: 13px;
	color: var(--tvpn-text-soft);
}

.abuse-list {
	display: grid;
	gap: 6px;
}

.abuse-row {
	display: grid;
	grid-template-columns: 28px 1fr auto;
	align-items: center;
	gap: 12px;
	padding: 10px 12px;
	border-radius: 10px;
	background: var(--tvpn-surface-muted);
	border: 1px solid var(--tvpn-row-border);
	color: inherit;
	text-decoration: none;
	transition: background 120ms ease, border-color 120ms ease;
}
.abuse-row:hover {
	background: var(--tvpn-surface-muted-hover);
	border-color: var(--tvpn-border-strong);
}
.abuse-row--critical {
	border-color: rgba(244, 63, 94, 0.45);
	background: rgba(244, 63, 94, 0.08);
}
.abuse-row--warning {
	border-color: rgba(245, 158, 11, 0.40);
	background: rgba(245, 158, 11, 0.06);
}
.abuse-row__severity :deep(i) {
	font-size: 22px;
	color: rgba(244, 63, 94, 0.92);
}
.abuse-row--warning .abuse-row__severity :deep(i) {
	color: rgba(245, 158, 11, 0.95);
}
.abuse-row__top {
	display: flex;
	align-items: baseline;
	gap: 10px;
}
.abuse-row__user {
	font-weight: 700;
	font-size: 13px;
}
.abuse-row__day {
	font-size: 11px;
	opacity: 0.65;
}
.abuse-row__reason {
	font-size: 12px;
	opacity: 0.85;
	margin-top: 2px;
}
.abuse-row__metrics {
	text-align: right;
}
.abuse-row__gb {
	font-weight: 700;
	font-size: 13px;
}
.abuse-row__share {
	font-size: 11px;
	opacity: 0.7;
	margin-top: 2px;
}

.panel {
	display: grid;
	grid-template-columns: 1fr;
	padding: 14px;
	border-radius: var(--tvpn-radius);
	overflow: hidden;
	width: 100%;
	max-width: none !important;
	box-sizing: border-box;
	justify-self: stretch !important;
	background: var(--tvpn-surface-strong);
	border: 1px solid var(--tvpn-border);
	box-shadow: var(--tvpn-shadow-soft);
}

.panel > * {
	width: 100%;
	min-width: 0;
	justify-self: stretch;
}

.panel__title {
	font-weight: 700;
	font-size: 15px;
	line-height: 1.3;
	letter-spacing: 0.01em;
}

.panel__subtitle {
	opacity: 0.75;
	font-size: 12px;
	line-height: 1.45;
	margin-top: 4px;
	margin-bottom: 12px;
}

.panel__title,
.panel__subtitle,
.widgets__title,
.widgets__hint,
.action__title,
.action__desc,
.health__hint {
	overflow-wrap: anywhere;
}

.panel,
.kpi,
.trend,
.action,
.widgets__row,
.control-link,
.nav__item {
	transition:
		background 0.18s ease,
		border-color 0.18s ease,
		box-shadow 0.18s ease,
		transform 0.18s ease;
}

:where(.home-hero__cta, .widgets__row, .action, .control-link, .nav__item):focus-visible {
	outline: 2px solid rgba(147, 197, 253, 0.8);
	outline-offset: 2px;
}

.panel--activity {
	padding: 16px;
	border-color: var(--tvpn-border);
	background:
		linear-gradient(180deg, rgba(59, 130, 246, 0.05), rgba(59, 130, 246, 0.02)),
		var(--tvpn-surface-strong);
	box-shadow: var(--tvpn-shadow-soft);
}

.ops-grid {
	display: grid;
	grid-template-columns: repeat(2, minmax(0, 1fr));
	grid-template-areas:
		'summary summary'
		'notify notify'
		'maintenance thresholds'
		'hwid hwid'
		'console console'
		'links links'
		'map map';
	gap: 14px;
	min-width: 0;
	align-items: start;
}

.ops-grid > * {
	min-width: 0;
	align-self: start;
	height: auto;
}

.panel--ops-summary { grid-area: summary; }

.panel--ops-notify { grid-area: notify; }

.panel--ops-maintenance { grid-area: maintenance; }

.panel--ops-thresholds { grid-area: thresholds; }

.panel--ops-hwid { grid-area: hwid; }

.panel--ops-console { grid-area: console; }

.panel--ops-links { grid-area: links; }

.panel--ops-map { grid-area: map; }

.ops-toolbar {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 10px;
	margin-bottom: 10px;
}

.ops-toolbar__meta {
	font-size: 12px;
	opacity: 0.74;
}

.inline-link {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	font-size: 12px;
	text-decoration: none;
	color: inherit;
	opacity: 0.82;
	padding: 5px 9px;
	border-radius: 9px;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
}

.inline-link:hover {
	opacity: 1;
	border-color: var(--tvpn-border-strong);
	background: var(--tvpn-accent-soft);
}

.notification-result {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 10px;
	flex-wrap: wrap;
}

.ops-stack {
	display: grid;
	gap: 12px;
}

.ops-section {
	display: grid;
	gap: 10px;
	padding: 12px;
	border-radius: 12px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-muted);
	min-width: 0;
	width: 100%;
	box-sizing: border-box;
}

.ops-section__title {
	font-weight: 700;
	letter-spacing: 0.01em;
}

.ops-section__subtitle {
	font-size: 12px;
	opacity: 0.75;
	overflow-wrap: anywhere;
}

.ops-health {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
	gap: 10px;
}

.ops-health__item {
	padding: 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-elevated);
	display: grid;
	gap: 6px;
}

.ops-health__label {
	font-size: 11px;
	opacity: 0.75;
}

.ops-health__value {
	font-size: 13px;
	font-weight: 700;
}

.ops-badge {
	width: fit-content;
	padding: 4px 8px;
	border-radius: 999px;
	font-size: 11px;
	font-weight: 700;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-surface-muted);
}

.ops-badge--ok {
	background: rgba(16, 185, 129, 0.16);
}

.ops-badge--warn {
	background: rgba(245, 158, 11, 0.22);
}

.ops-badge--muted {
	background: rgba(148, 163, 184, 0.18);
}

.control-links {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
	gap: 10px;
}

.control-link {
	display: grid;
	gap: 6px;
	padding: 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-border-strong);
	background: linear-gradient(150deg, rgba(59, 130, 246, 0.16), var(--tvpn-surface-muted));
	text-decoration: none;
	color: inherit;
	min-width: 0;
	box-shadow: var(--tvpn-shadow-soft);
}

.control-link:hover {
	background: linear-gradient(150deg, rgba(59, 130, 246, 0.20), var(--tvpn-surface-muted-hover));
	border-color: var(--tvpn-border-strong);
	transform: translateY(-1px);
}

.control-link__head {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 700;
}

.control-link__desc {
	font-size: 12px;
	opacity: 0.82;
	line-height: 1.35;
}

.control-link__path {
	font-size: 11px;
	opacity: 0.68;
	font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

.settings--wide {
	gap: 9px;
}

.settings__actions {
	display: flex;
	justify-content: flex-start;
	gap: 8px;
	flex-wrap: wrap;
}

.notification-presets {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
	margin-bottom: 10px;
}

.notification-presets__btn {
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-muted);
	color: inherit;
	padding: 6px 10px;
	border-radius: 10px;
	font-size: 12px;
	cursor: pointer;
}

.notification-presets__btn:hover {
	border-color: var(--tvpn-border-strong);
	background: var(--tvpn-accent-soft);
}

.notification-form {
	display: grid;
	grid-template-columns: repeat(2, minmax(0, 1fr));
	gap: 10px;
}

.notification-form__field {
	display: grid;
	gap: 6px;
	font-size: 12px;
	opacity: 0.9;
}

.notification-form__field--wide {
	grid-column: 1 / -1;
}

.notification-form__field--checkbox {
	grid-template-columns: minmax(0, 1fr) auto;
	align-items: center;
}

.notification-form__input,
.notification-form__textarea {
	width: 100%;
	min-width: 0;
	padding: 7px 9px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-input-bg);
	color: inherit;
	box-sizing: border-box;
}

.notification-form__textarea {
	min-height: 78px;
	resize: vertical;
}

.notification-form__toggle {
	width: 16px;
	height: 16px;
}

.notification-form__hint {
	margin-top: 8px;
	font-size: 12px;
	opacity: 0.74;
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
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-muted);
	box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
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
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	align-items: center;
	gap: 10px;
	padding: 8px 10px;
	border-radius: 10px;
	text-decoration: none;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-muted);
}

.widgets__row:hover {
	background: var(--tvpn-surface-muted-hover);
	border-color: var(--tvpn-border-strong);
	transform: translateY(-1px);
}

.widgets__name {
	min-width: 0;
	font-weight: 600;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	line-height: 1.25;
}

.widgets__meta {
	font-size: 12px;
	opacity: 0.75;
	white-space: nowrap;
	justify-self: end;
	text-align: right;
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

.settings__row > span {
	min-width: 0;
	line-height: 1.35;
	overflow-wrap: anywhere;
}

.settings__input {
	width: 100%;
	min-width: 0;
}

.settings__input--num {
	padding: 6px 8px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-input-bg);
	color: inherit;
}

.settings__input--textarea {
	min-height: 72px;
	padding: 8px 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-input-bg);
	color: inherit;
	resize: vertical;
}

.ops-console {
	display: grid;
	gap: 10px;
	min-width: 0;
	width: 100%;
	box-sizing: border-box;
}

.ops-console__label {
	display: grid;
	gap: 6px;
	font-size: 12px;
	opacity: 0.9;
}

.ops-console__select {
	width: 100%;
	padding: 8px 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-input-bg);
	color: inherit;
	min-width: 0;
	box-sizing: border-box;
}

.ops-console__actions {
	display: flex;
	gap: 8px;
}

.ops-console__output {
	margin: 0;
	padding: 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-elevated-strong);
	max-height: 240px;
	overflow: auto;
	font-size: 11px;
	line-height: 1.35;
	white-space: pre-wrap;
	word-break: break-word;
}

.hwid-tool__notice {
	display: grid;
	gap: 6px;
}

.hwid-tool__form {
	margin-top: 10px;
}

.hwid-tool__confirm {
	display: grid;
	grid-template-columns: auto minmax(0, 1fr);
	gap: 10px;
	align-items: start;
	margin-top: 10px;
	padding: 10px 12px;
	border-radius: 12px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-muted);
	font-size: 12px;
	line-height: 1.4;
}

.hwid-tool__confirm-input {
	margin-top: 3px;
}

.hwid-tool__actions {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
	margin-top: 12px;
}

.hwid-tool__preview {
	display: grid;
	gap: 12px;
	margin-top: 14px;
	max-height: min(72vh, 860px);
	overflow: auto;
	overscroll-behavior: contain;
	scrollbar-gutter: stable;
	padding-right: 4px;
}

.hwid-tool__preview-head {
	display: grid;
	gap: 4px;
}

.hwid-tool__preview-title {
	font-weight: 700;
}

.hwid-tool__preview-subtitle,
.hwid-tool__empty {
	font-size: 12px;
	opacity: 0.76;
}

.hwid-tool__owners {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
	gap: 10px;
}

.hwid-tool__owner {
	display: grid;
	gap: 10px;
	padding: 12px;
	border-radius: 14px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-elevated);
}

.hwid-tool__owner-head {
	display: flex;
	align-items: flex-start;
	justify-content: space-between;
	gap: 10px;
	flex-wrap: wrap;
}

.hwid-tool__owner-title {
	font-weight: 700;
}

.hwid-tool__owner-meta {
	margin-top: 4px;
	font-size: 12px;
	opacity: 0.74;
	word-break: break-word;
}

.hwid-tool__owner-badges {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
}

.hwid-tool__badge {
	padding: 4px 8px;
	border-radius: 999px;
	border: 1px solid var(--tvpn-input-border);
	background: var(--tvpn-surface-muted);
	font-size: 11px;
	font-weight: 700;
}

.hwid-tool__badge--accent {
	background: rgba(59, 130, 246, 0.16);
}

.hwid-tool__badge--warn {
	background: rgba(245, 158, 11, 0.22);
}

.hwid-tool__owner-grid {
	display: grid;
	gap: 8px;
}

.hwid-tool__owner-row {
	display: grid;
	grid-template-columns: minmax(0, 1fr);
	gap: 3px;
	align-items: start;
	font-size: 12px;
}

.hwid-tool__owner-row > span {
	opacity: 0.76;
}

.hwid-tool__owner-row > strong {
	text-align: left;
	word-break: break-word;
	line-height: 1.4;
}

.hwid-tool__results {
	display: grid;
	gap: 8px;
}

.hwid-tool__results-title {
	font-size: 12px;
	font-weight: 700;
	opacity: 0.82;
}

.hwid-tool__results-list {
	display: grid;
	gap: 6px;
}

.hwid-tool__result-row {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	gap: 10px;
	padding: 8px 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-section-border);
	background: var(--tvpn-surface-muted);
	font-size: 12px;
}

.hwid-tool__result-row > span {
	word-break: break-word;
}

.help__row {
	display: grid;
	grid-template-columns: 120px 1fr;
	gap: 10px;
	padding: 8px 0;
	border-bottom: 1px solid var(--tvpn-row-border);
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
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-muted);
	box-shadow: var(--tvpn-shadow-soft);
}

.action:hover {
	background: var(--tvpn-surface-muted-hover);
	border-color: var(--tvpn-border-strong);
	transform: translateY(-1px);
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
	border-bottom: 1px solid var(--tvpn-row-border);
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
	border: 1px solid transparent;
	transition: border-color 0.18s ease, background 0.18s ease;
}

.nav__item:hover {
	background: var(--tvpn-accent-soft);
	border-color: var(--tvpn-border);
}

.nav__item--active {
	background: var(--tvpn-accent-strong);
	border-color: var(--tvpn-border-strong);
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
	border: 1px solid var(--tvpn-border);
	background:
		linear-gradient(135deg, rgba(59, 130, 246, 0.12), rgba(16, 185, 129, 0.06)),
		var(--tvpn-surface-soft);
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

.nav__brand-title {
	font-size: 13px;
	font-weight: 700;
}

.nav__brand-subtitle {
	font-size: 11px;
	opacity: 0.7;
}

.nav--premium .nav__item {
	display: grid;
	grid-template-columns: 28px minmax(0, 1fr) auto;
	gap: 10px;
}

.nav__item-icon {
	display: grid;
	place-items: center;
	width: 28px;
	height: 28px;
	border-radius: 8px;
	background: var(--tvpn-accent-strong);
}

.nav__item-label {
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.nav__item-badge {
	font-size: 10px;
	line-height: 1;
	padding: 4px 6px;
	border-radius: 999px;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
	opacity: 0.9;
}

.panel__head {
	display: flex;
	justify-content: space-between;
	gap: 12px;
	align-items: flex-start;
}

.panel__head--chart {
	align-items: center;
}

.panel__head--compact {
	align-items: flex-start;
	margin-bottom: 8px;
}

.panel__head--compact .panel__subtitle {
	margin-bottom: 0;
}

.panel--chart-hub {
	border: 1px solid var(--tvpn-border);
	background:
		linear-gradient(165deg, rgba(59, 130, 246, 0.08), rgba(16, 185, 129, 0.04)),
		var(--tvpn-surface-strong);
	box-shadow: var(--tvpn-shadow-soft);
	gap: 12px;
	overflow: visible;
}

.segmented {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	padding: 4px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
}

.segmented--compact {
	max-width: 360px;
}

.segmented__btn {
	border: none;
	outline: none;
	padding: 7px 10px;
	border-radius: 8px;
	font-size: 12px;
	background: transparent;
	color: inherit;
	opacity: 0.75;
	cursor: pointer;
}

.segmented__btn:hover {
	opacity: 1;
	background: var(--tvpn-accent-soft);
}

.segmented__btn--active {
	opacity: 1;
	background: var(--tvpn-accent-gradient);
}

.chart-hub__stats {
	display: grid;
	grid-template-columns: repeat(3, minmax(0, 1fr));
	gap: 10px;
	align-items: stretch;
}

.chart-hub__stat {
	padding: 10px;
	border-radius: 10px;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-surface-soft);
	min-height: 72px;
	display: grid;
	align-content: start;
}

.panel--chart-hub :deep(.chart) {
	margin-top: 2px;
}

.panel--chart-hub :deep(.chart__canvas) {
	overflow: hidden;
}

.chart-hub__label {
	font-size: 11px;
	opacity: 0.75;
}

.chart-hub__value {
	margin-top: 3px;
	font-size: 17px;
	font-weight: 750;
	letter-spacing: -0.01em;
}

.home-hero__kicker {
	display: inline-flex;
	width: fit-content;
	padding: 3px 10px;
	border-radius: 999px;
	font-size: 11px;
	font-weight: 700;
	letter-spacing: 0.07em;
	text-transform: uppercase;
	border: 1px solid var(--tvpn-border);
	background: var(--tvpn-accent-strong);
}

.home-hero__cta-row {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
	margin-top: 12px;
}

.home-hero__cta {
	display: inline-flex;
	gap: 8px;
	align-items: center;
	padding: 8px 12px;
	border-radius: 10px;
	text-decoration: none;
	color: inherit;
	font-size: 12px;
	font-weight: 650;
	border: 1px solid var(--tvpn-border);
	transition:
		background 0.18s ease,
		border-color 0.18s ease,
		box-shadow 0.18s ease,
		transform 0.18s ease;
	box-shadow: var(--tvpn-shadow-soft);
}

.home-hero__cta:hover {
	transform: translateY(-1px);
	border-color: var(--tvpn-border-strong);
	box-shadow: var(--tvpn-card-shadow);
}

.home-hero__cta--primary {
	background: linear-gradient(120deg, rgba(59, 130, 246, 0.34), rgba(16, 185, 129, 0.22));
}

.home-hero__cta--ghost {
	background: var(--tvpn-surface-soft);
}

.home-hero__meta-stats {
	margin-top: 10px;
	display: grid;
	gap: 6px;
}

.home-hero__meta-stat {
	display: flex;
	justify-content: space-between;
	gap: 10px;
	font-size: 12px;
	opacity: 0.9;
	padding: 4px 0;
	border-bottom: 1px solid var(--tvpn-border);
}

.home-hero__meta-stat:last-child {
	border-bottom: none;
	padding-bottom: 0;
}

.home-hero__meta-stat strong {
	font-weight: 750;
	letter-spacing: -0.01em;
}

@media (max-width: 1500px) {
	.page {
		grid-template-columns: minmax(0, 1fr) 340px;
	}
}

@media (max-width: 1400px) {
	.page {
		grid-template-columns: 1fr;
		gap: 12px;
	}

	.page__side {
		position: static;
		top: auto;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		align-items: stretch;
	}

	.page__side > * {
		height: 100%;
	}

	.trends {
		grid-template-columns: 1fr;
	}

	.big {
		grid-template-columns: 1fr;
	}

	.panel__head--chart {
		flex-direction: column;
		align-items: flex-start;
	}
}

@media (max-width: 1200px) {
	.ops-grid {
		grid-template-columns: 1fr;
		grid-template-areas:
			'summary'
			'notify'
			'maintenance'
			'thresholds'
			'hwid'
			'console'
			'links'
			'map';
		gap: 12px;
	}

	.page__side {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	.page__side > :last-child {
		grid-column: 1 / -1;
	}
}

@media (max-width: 980px) {
	/* On phones/tablets, hide custom module navigation column to keep main content readable. */
	:deep(.private-view__navigation) {
		display: none !important;
		width: 0 !important;
		min-width: 0 !important;
		max-width: 0 !important;
		padding: 0 !important;
		margin: 0 !important;
		border: 0 !important;
		overflow: hidden !important;
	}

	:deep(.private-view__main),
	:deep(.private-view__content) {
		max-width: none !important;
		width: 100% !important;
	}

	.notification-form {
		grid-template-columns: 1fr;
	}

	.chart-hub__stats {
		grid-template-columns: 1fr;
	}

	.home-hero {
		flex-direction: column;
		padding: 14px;
		gap: 12px;
		min-height: auto !important;
		height: auto !important;
		max-height: none !important;
	}

	.home-hero__left,
	.home-hero__right {
		flex: 0 0 auto;
		width: 100%;
	}

	.home-hero__subtitle {
		max-width: none;
	}

	.home-hero__cta-row {
		width: 100%;
	}

	.home-hero__cta {
		flex: 1 1 160px;
		justify-content: center;
	}
}

@media (max-width: 720px) {
	.page {
		padding: 12px;
		gap: 10px;
	}

	.kpi-grid {
		grid-template-columns: 1fr;
	}

	.trend__head {
		flex-direction: column;
		align-items: flex-start;
	}

	.help__row {
		grid-template-columns: 1fr;
	}

	.settings__row {
		grid-template-columns: 1fr;
		align-items: start;
		gap: 6px;
	}

	.big__metrics {
		grid-template-columns: 1fr;
		align-items: start;
	}

	.big__mini {
		justify-items: start;
	}

	.chart-hub__stats {
		grid-template-columns: 1fr;
	}

	.ops-toolbar {
		flex-direction: column;
		align-items: flex-start;
	}

	.panel__head--compact {
		flex-direction: column;
		align-items: flex-start;
	}

	.ops-health {
		grid-template-columns: 1fr;
	}

	.control-links {
		grid-template-columns: 1fr;
	}

	.page__side {
		grid-template-columns: 1fr;
		gap: 10px;
	}

	.page__side > :last-child {
		grid-column: auto;
	}

	.panel {
		padding: 12px;
	}

	.hwid-tool__preview {
		max-height: min(68vh, 720px);
	}

	.notification-presets {
		gap: 6px;
	}

	.notification-presets__btn {
		flex: 1 1 auto;
		min-width: 120px;
	}

	.widgets__row {
		grid-template-columns: 1fr;
		gap: 4px;
	}

	.widgets__name {
		white-space: normal;
		overflow: visible;
		text-overflow: clip;
	}

	.widgets__meta {
		justify-self: start;
		text-align: left;
		white-space: normal;
	}

	.nav--premium {
		display: grid;
		gap: 10px;
		overflow: visible;
		padding-bottom: 0;
	}

	.nav--premium .nav__brand,
	.nav--premium .nav__section {
		min-width: 0;
	}
}
</style>
