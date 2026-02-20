import { defineModule } from '@directus/extensions-sdk';

import ModuleContentOps from './module.vue';

function injectContentOpsFullWidthLayoutFix() {
	if (typeof window === 'undefined' || typeof document === 'undefined') return;

	const STYLE_ID = 'tvpn-content-ops-fullwidth-layout-style';
	const ACTIVE_CLASS = 'tvpn-content-ops-route-active';

	function isContentOpsRoute() {
		return window.location.pathname.includes('/admin/tvpn-content-ops');
	}

	function ensureStyle() {
		if (document.getElementById(STYLE_ID)) return;
		const style = document.createElement('style');
		style.id = STYLE_ID;
		style.textContent = `
			html.${ACTIVE_CLASS} .private-view__main,
			html.${ACTIVE_CLASS} .private-view__content,
			html.${ACTIVE_CLASS} .private-view__main > *,
			html.${ACTIVE_CLASS} .private-view__content > * {
				max-width: none !important;
				width: 100% !important;
			}
			html.${ACTIVE_CLASS} .private-view__content {
				justify-content: stretch !important;
				justify-items: stretch !important;
				align-items: stretch !important;
			}
		`;
		document.head.appendChild(style);
	}

	function updateActiveFlag() {
		const root = document.documentElement;
		if (!root) return;
		if (isContentOpsRoute()) {
			root.classList.add(ACTIVE_CLASS);
		} else {
			root.classList.remove(ACTIVE_CLASS);
		}
	}

	ensureStyle();
	updateActiveFlag();

	const origPushState = window.history.pushState;
	const origReplaceState = window.history.replaceState;

	if (!window.__tvpnContentOpsHistoryPatched) {
		window.__tvpnContentOpsHistoryPatched = true;

		window.history.pushState = function pushStatePatched(...args) {
			const out = origPushState.apply(this, args);
			updateActiveFlag();
			return out;
		};
		window.history.replaceState = function replaceStatePatched(...args) {
			const out = origReplaceState.apply(this, args);
			updateActiveFlag();
			return out;
		};
		window.addEventListener('popstate', updateActiveFlag);
	}
}

injectContentOpsFullWidthLayoutFix();

export default defineModule({
	id: 'tvpn-content-ops',
	name: 'Контент Ops',
	icon: 'hub',
	color: '#06B6D4',
	routes: [
		{
			path: '',
			component: ModuleContentOps,
		},
	],
});
