<template>
	<div class="activity-board">
		<div class="activity-board__header">
			<div>
				<div class="activity-board__eyebrow">Оперативная лента</div>
				<div class="activity-board__title">События</div>
				<div class="activity-board__subtitle">Прокручиваемые списки последних регистраций, платежей и промокодов без перегруза интерфейса.</div>
			</div>
			<div class="activity-board__note">3 потока · до 8 записей в каждом</div>
		</div>

		<div class="activity-board__grid">
			<section
				v-for="column in columns"
				:key="column.key"
				class="activity-column"
				:class="`activity-column--${column.key}`"
			>
				<div class="activity-column__head">
					<div class="activity-column__label">
						<v-icon :name="column.icon" />
						<div class="activity-column__label-text">
							<span class="activity-column__title">{{ column.title }}</span>
						</div>
					</div>
					<div class="activity-column__actions">
						<span class="activity-column__count">{{ column.items.length }}</span>
						<router-link class="activity-column__link" :to="{ path: column.allPath }">Все</router-link>
					</div>
				</div>

				<div class="activity-column__body">
					<div v-if="column.items.length" class="activity-column__list">
						<router-link
							v-for="item in column.items"
							:key="`${column.key}-${item.id}`"
							class="activity-card"
							:to="{ path: column.itemPath(item) }"
							:title="column.cardTitle(item)"
						>
							<div class="activity-card__main">
								<div class="activity-card__title">{{ column.cardLabel(item) }}</div>
								<div class="activity-card__meta">
									<span
										v-for="(token, tokenIndex) in column.meta(item)"
										:key="`${token.label}-${tokenIndex}`"
										class="activity-pill"
										:class="token.tone ? `activity-pill--${token.tone}` : ''"
									>
										{{ token.label }}
									</span>
								</div>
							</div>
							<div class="activity-card__time">{{ column.time(item) }}</div>
						</router-link>
					</div>
					<div v-else class="activity-column__empty">{{ column.emptyText }}</div>
				</div>
			</section>
		</div>
	</div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
	users: {
		type: Array,
		default: () => [],
	},
	payments: {
		type: Array,
		default: () => [],
	},
	promo: {
		type: Array,
		default: () => [],
	},
	fmtMoney: {
		type: Function,
		default: (value) => String(value ?? '—'),
	},
	shortDate: {
		type: Function,
		default: (value) => String(value ?? '—'),
	},
	fromNow: {
		type: Function,
		default: (value) => String(value ?? '—'),
	},
});

function paymentStatusTone(status) {
	const value = String(status || '').toLowerCase();
	if (!value) return 'muted';
	if (['succeeded', 'success', 'paid', 'completed'].includes(value)) return 'good';
	if (['pending', 'processing', 'created'].includes(value)) return 'warn';
	if (['failed', 'error', 'declined', 'expired'].includes(value)) return 'bad';
	return 'muted';
}

function normalizeContext(context) {
	if (typeof context !== 'string') return '';
	const trimmed = context.trim();
	return trimmed.length > 18 ? `${trimmed.slice(0, 18)}…` : trimmed;
}

function compactIdentifier(value, head = 20, tail = 8) {
	const raw = String(value || '');
	if (!raw) return '—';
	if (raw.length <= head + tail + 1) return raw;
	return `${raw.slice(0, head)}…${raw.slice(-tail)}`;
}

const columns = computed(() => [
	{
		key: 'users',
		icon: 'people',
		title: 'Новые пользователи',
		allPath: '/content/users',
		items: Array.isArray(props.users) ? props.users : [],
		emptyText: 'Свежих регистраций пока нет',
		itemPath: (item) => `/content/users/${item.id}`,
		cardTitle: (item) => item.username || item.full_name || `user #${item.id}`,
		cardLabel: (item) => item.username || item.full_name || `user #${item.id}`,
		meta: (item) =>
			[
				item.is_blocked ? { label: 'blocked', tone: 'bad' } : null,
				item.expired_at ? { label: `exp ${props.shortDate(item.expired_at)}`, tone: 'muted' } : null,
			].filter(Boolean),
		time: (item) => props.fromNow(item.registration_date),
	},
	{
		key: 'payments',
		icon: 'payments',
		title: 'Платежи',
		allPath: '/content/processed_payments',
		items: Array.isArray(props.payments) ? props.payments : [],
		emptyText: 'Свежих платежей пока нет',
		itemPath: (item) => `/content/processed_payments/${item.id}`,
		cardTitle: (item) => `#${item.payment_id || item.id}`,
		cardLabel: (item) => compactIdentifier(`#${item.payment_id || item.id}`, 18, 10),
		meta: (item) =>
			[
				{ label: props.fmtMoney(item.amount), tone: 'muted' },
				item.status ? { label: String(item.status), tone: paymentStatusTone(item.status) } : null,
				item.user_id ? { label: `user ${item.user_id}`, tone: 'soft' } : null,
			].filter(Boolean),
		time: (item) => props.fromNow(item.processed_at),
	},
	{
		key: 'promo',
		icon: 'confirmation_number',
		title: 'Промо-активность',
		allPath: '/content/promo_usages',
		items: Array.isArray(props.promo) ? props.promo : [],
		emptyText: 'Использований промокодов пока нет',
		itemPath: (item) => `/content/promo_usages/${item.id}`,
		cardTitle: (item) => `Промокод #${item.promo_code_id || item.id}`,
		cardLabel: (item) => `Промокод #${item.promo_code_id || item.id}`,
		meta: (item) =>
			[
				item.user_id ? { label: `user ${item.user_id}`, tone: 'soft' } : null,
				item.context ? { label: normalizeContext(item.context), tone: 'muted' } : null,
			].filter(Boolean),
		time: (item) => props.fromNow(item.used_at),
	},
]);
</script>

<style scoped>
.activity-board {
	display: grid;
	gap: 16px;
}

.activity-board__header {
	display: flex;
	justify-content: space-between;
	gap: 12px 20px;
	align-items: flex-start;
}

.activity-board__eyebrow {
	display: inline-flex;
	padding: 4px 9px;
	border-radius: 999px;
	font-size: 11px;
	font-weight: 700;
	letter-spacing: 0.06em;
	text-transform: uppercase;
	color: var(--tvpn-activity-eyebrow-text, rgba(191, 219, 254, 0.96));
	background: var(--tvpn-activity-eyebrow-bg, rgba(59, 130, 246, 0.16));
	border: 1px solid var(--tvpn-activity-eyebrow-border, rgba(96, 165, 250, 0.24));
}

.activity-board__title {
	margin-top: 10px;
	font-size: 18px;
	font-weight: 740;
	letter-spacing: -0.02em;
}

.activity-board__subtitle {
	margin-top: 5px;
	font-size: 13px;
	line-height: 1.55;
	opacity: 0.78;
	max-width: 760px;
}

.activity-board__note {
	font-size: 12px;
	line-height: 1.45;
	opacity: 0.72;
	white-space: nowrap;
}

.activity-board__grid {
	display: grid;
	grid-template-columns: repeat(3, minmax(0, 1fr));
	gap: 12px;
	align-items: stretch;
}

.activity-column {
	display: grid;
	grid-template-rows: auto minmax(0, 1fr);
	min-width: 0;
	min-height: 0;
	padding: 14px;
	border-radius: 14px;
	border: 1px solid var(--tvpn-border, rgba(148, 163, 184, 0.18));
	background: var(--tvpn-surface-soft, rgba(15, 23, 42, 0.44));
	box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.activity-column--payments {
	border-color: rgba(96, 165, 250, 0.22);
}

.activity-column__head {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	gap: 10px 12px;
	align-items: start;
	padding-bottom: 12px;
	margin-bottom: 12px;
	border-bottom: 1px solid rgba(148, 163, 184, 0.16);
	min-width: 0;
}

.activity-column__label {
	display: grid;
	grid-template-columns: auto minmax(0, 1fr);
	align-items: start;
	gap: 10px;
	min-width: 0;
}

.activity-column__label-text {
	display: grid;
	gap: 2px;
	min-width: 0;
}

.activity-column__title {
	font-size: 15px;
	line-height: 1.25;
	font-weight: 720;
	letter-spacing: -0.01em;
	overflow-wrap: anywhere;
}

.activity-column__actions {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	flex-shrink: 0;
	justify-self: end;
}

.activity-column__count {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	min-width: 28px;
	height: 28px;
	padding: 0 8px;
	border-radius: 999px;
	font-size: 12px;
	font-weight: 700;
	background: var(--tvpn-surface-muted, rgba(148, 163, 184, 0.12));
	border: 1px solid var(--tvpn-border, rgba(148, 163, 184, 0.18));
}

.activity-column__link {
	display: inline-flex;
	align-items: center;
	height: 28px;
	padding: 0 10px;
	border-radius: 999px;
	font-size: 12px;
	font-weight: 600;
	text-decoration: none;
	color: inherit;
	opacity: 0.82;
	border: 1px solid var(--tvpn-border, rgba(148, 163, 184, 0.24));
	background: var(--tvpn-surface-muted, rgba(255, 255, 255, 0.03));
	transition:
		background 0.18s ease,
		border-color 0.18s ease,
		opacity 0.18s ease;
}

.activity-column__link:hover {
	opacity: 1;
	background: var(--tvpn-accent-soft, rgba(59, 130, 246, 0.08));
	border-color: var(--tvpn-border-strong, rgba(96, 165, 250, 0.32));
}

.activity-column__body {
	min-height: 0;
}

.activity-column__list {
	display: grid;
	gap: 10px;
	max-height: clamp(280px, 46vh, 560px);
	min-height: 0;
	overflow-y: auto;
	overscroll-behavior: contain;
	padding-right: 4px;
	scrollbar-width: thin;
	scrollbar-color: rgba(148, 163, 184, 0.3) transparent;
}

.activity-column__list::-webkit-scrollbar {
	width: 8px;
}

.activity-column__list::-webkit-scrollbar-thumb {
	border-radius: 999px;
	background: rgba(148, 163, 184, 0.3);
}

.activity-column__empty {
	display: grid;
	place-items: center;
	min-height: 180px;
	padding: 12px;
	text-align: center;
	border-radius: 12px;
	border: 1px dashed var(--tvpn-border, rgba(148, 163, 184, 0.22));
	background: var(--tvpn-surface-muted, rgba(255, 255, 255, 0.02));
	font-size: 13px;
	opacity: 0.72;
}

.activity-card {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	gap: 12px;
	align-items: start;
	padding: 12px;
	border-radius: 12px;
	text-decoration: none;
	color: inherit;
	border: 1px solid var(--tvpn-border, rgba(148, 163, 184, 0.18));
	background: var(--tvpn-surface-strong, rgba(255, 255, 255, 0.035));
	box-shadow: var(--tvpn-shadow-soft, 0 8px 18px rgba(2, 6, 23, 0.08));
	transition:
		background 0.18s ease,
		border-color 0.18s ease,
		box-shadow 0.18s ease;
}

.activity-card:hover {
	background: var(--tvpn-accent-soft, rgba(59, 130, 246, 0.06));
	border-color: var(--tvpn-border-strong, rgba(96, 165, 250, 0.28));
	box-shadow: var(--tvpn-card-shadow, 0 10px 24px rgba(2, 6, 23, 0.12));
}

.activity-card:focus-visible,
.activity-column__link:focus-visible {
	outline: 2px solid rgba(147, 197, 253, 0.82);
	outline-offset: 2px;
}

.activity-card__main {
	min-width: 0;
}

.activity-card__title {
	display: -webkit-box;
	-webkit-box-orient: vertical;
	-webkit-line-clamp: 2;
	overflow: hidden;
	font-size: 15px;
	line-height: 1.38;
	font-weight: 720;
	letter-spacing: -0.01em;
	overflow-wrap: anywhere;
}

.activity-card__meta {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	margin-top: 8px;
}

.activity-card__time {
	padding-top: 2px;
	font-size: 12px;
	line-height: 1.35;
	opacity: 0.68;
	white-space: nowrap;
	text-align: right;
}

.activity-pill {
	display: inline-flex;
	align-items: center;
	min-height: 24px;
	padding: 3px 8px;
	border-radius: 999px;
	font-size: 11px;
	line-height: 1.25;
	border: 1px solid var(--tvpn-border, rgba(148, 163, 184, 0.18));
	background: var(--tvpn-surface-muted, rgba(255, 255, 255, 0.04));
	opacity: 0.94;
}

.activity-pill--soft {
	background: rgba(96, 165, 250, 0.1);
	border-color: rgba(96, 165, 250, 0.18);
}

.activity-pill--muted {
	background: rgba(148, 163, 184, 0.08);
}

.activity-pill--good {
	background: rgba(16, 185, 129, 0.16);
	border-color: rgba(16, 185, 129, 0.22);
}

.activity-pill--warn {
	background: rgba(245, 158, 11, 0.16);
	border-color: rgba(245, 158, 11, 0.22);
}

.activity-pill--bad {
	background: rgba(239, 68, 68, 0.16);
	border-color: rgba(239, 68, 68, 0.24);
}

@media (max-width: 1360px) {
	.activity-column__head {
		grid-template-columns: 1fr;
	}

	.activity-column__actions {
		justify-self: start;
	}
}

@media (max-width: 1279px) {
	.activity-board__grid {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	.activity-column--promo {
		grid-column: 1 / -1;
	}

	.activity-column__list {
		max-height: clamp(240px, 40vh, 420px);
	}
}

@media (max-width: 767px) {
	.activity-board {
		gap: 12px;
	}

	.activity-board__header {
		flex-direction: column;
		align-items: flex-start;
	}

	.activity-board__note {
		white-space: normal;
	}

	.activity-board__grid {
		grid-template-columns: 1fr;
	}

	.activity-column--promo {
		grid-column: auto;
	}

	.activity-column {
		padding: 10px;
	}

	.activity-column__head {
		grid-template-columns: 1fr;
	}

	.activity-column__list {
		max-height: none;
		overflow: visible;
		padding-right: 0;
	}

	.activity-column__empty {
		min-height: 120px;
	}

	.activity-card {
		grid-template-columns: 1fr;
		gap: 8px;
	}

	.activity-card__time {
		text-align: left;
		white-space: normal;
	}
}
</style>
