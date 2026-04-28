import { defineModule } from '@directus/extensions-sdk';

import ModuleHome from './module.vue';

// On some Directus builds the main content area is constrained by max-width containers
// above our module (e.g. `.private-view__main` / `.private-view__content`).
// Scoped CSS inside `module.vue` cannot override ancestor styles (CSS can't target parents),
// so we apply a small global override and enable it ONLY when we're on `/admin/tvpn-home`.
//
// IMPORTANT:
// We intentionally avoid mutating inline styles of shared layout wrappers.
// Directus is an SPA and those wrappers are reused across routes; inline tweaks can
// desync hitboxes/overlays on other pages (e.g. the users list "card" not opening).
function injectTvpnHomeFullWidthLayoutFix() {
	if (typeof window === 'undefined' || typeof document === 'undefined') return;

	const STYLE_ID = 'tvpn-home-fullwidth-layout-style';
	const ACTIVE_CLASS = 'tvpn-home-route-active';

	function isTvpnHomeRoute() {
		// Support custom subpaths: /something/admin/tvpn-home
		return window.location.pathname.includes('/admin/tvpn-home');
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
			@media (max-width: 980px) {
				html.${ACTIVE_CLASS} .private-view__navigation {
					display: none !important;
					width: 0 !important;
					min-width: 0 !important;
					max-width: 0 !important;
					padding: 0 !important;
					margin: 0 !important;
					border: 0 !important;
					overflow: hidden !important;
				}
				/* Directus mobile slide-over nav (module bar + module navigation). */
				html.${ACTIVE_CLASS} #dialog-outlet .container.left .module-bar,
				html.${ACTIVE_CLASS} #dialog-outlet .container.left .module-nav.mobile-nav,
				html.${ACTIVE_CLASS} #dialog-outlet .container.left .v-overlay,
				html.${ACTIVE_CLASS} #dialog-outlet .container.left .overlay {
					display: none !important;
				}
				html.${ACTIVE_CLASS} #dialog-outlet .container.left {
					width: 0 !important;
					min-width: 0 !important;
					max-width: 0 !important;
					pointer-events: none !important;
				}
				/* Hide mobile sidebar toggles to avoid opening hidden overlays. */
				html.${ACTIVE_CLASS} .v-icon.nav-toggle,
				html.${ACTIVE_CLASS} .v-icon.sidebar-toggle {
					display: none !important;
				}
				html.${ACTIVE_CLASS} .private-view__main,
				html.${ACTIVE_CLASS} .private-view__content {
					max-width: none !important;
					width: 100% !important;
				}
			}
		`;
		document.head.appendChild(style);
	}

	function updateActiveFlag() {
		const root = document.documentElement;
		if (!root) return;
		const active = isTvpnHomeRoute();
		if (active) {
			root.classList.add(ACTIVE_CLASS);
		} else {
			root.classList.remove(ACTIVE_CLASS);
		}
	}

	ensureStyle();
	updateActiveFlag();

	// Directus is an SPA; react to route changes immediately.
	const origPushState = window.history.pushState;
	const origReplaceState = window.history.replaceState;

	// eslint-disable-next-line no-underscore-dangle
	if (!window.__tvpnHomeHistoryPatched) {
		// eslint-disable-next-line no-underscore-dangle, no-param-reassign
		window.__tvpnHomeHistoryPatched = true;

		window.history.pushState = function pushStatePatched(...args) {
			// @ts-ignore - DOM lib typing mismatch in some builds
			const out = origPushState.apply(this, args);
			updateActiveFlag();
			return out;
		};
		window.history.replaceState = function replaceStatePatched(...args) {
			// @ts-ignore - DOM lib typing mismatch in some builds
			const out = origReplaceState.apply(this, args);
			updateActiveFlag();
			return out;
		};
		window.addEventListener('popstate', updateActiveFlag);
	}
}

injectTvpnHomeFullWidthLayoutFix();

export default defineModule({
  id: 'tvpn-home',
  name: 'Главная',
  // Make it visually obvious in the left module bar.
  icon: 'home',
  color: '#3B82F6',
  routes: [
    {
      path: '',
      component: ModuleHome,
    },
  ],
});

