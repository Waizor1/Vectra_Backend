import { defineModule } from '@directus/extensions-sdk';

import ModuleHome from './module.vue';

// Inject a global header button that brings the user back to our Home dashboard.
// Directus doesn't provide a first-class "global header slot" via the extensions SDK,
// but app extensions are evaluated at app startup (to build the module list),
// so we can safely attach a small DOM-based enhancer here.
function injectGlobalHomeButton() {
	if (typeof window === 'undefined' || typeof document === 'undefined') return;

	const BTN_ID = 'tvpn-home-global-header-btn';
	const STYLE_ID = 'tvpn-home-global-header-style';

	function adminBaseUrl() {
		// Keep it resilient to custom domains/subpaths.
		const { origin, pathname } = window.location;
		const idx = pathname.indexOf('/admin');
		if (idx === -1) return `${origin}/admin`;
		return `${origin}${pathname.slice(0, idx)}/admin`;
	}

	function gotoHome() {
		// Use full navigation to avoid depending on internal router APIs.
		window.location.assign(`${adminBaseUrl()}/tvpn-home`);
	}

	function ensureStyle() {
		if (document.getElementById(STYLE_ID)) return;
		const style = document.createElement('style');
		style.id = STYLE_ID;
		style.textContent = `
			#${BTN_ID} {
				display: inline-flex;
				align-items: center;
				gap: 8px;
				height: 34px;
				padding: 0 12px;
				border-radius: 10px;
				border: 1px solid rgba(255,255,255,0.10);
				background: rgba(255,255,255,0.04);
				color: inherit;
				cursor: pointer;
				user-select: none;
				font-weight: 650;
				font-size: 12px;
			}
			#${BTN_ID}:hover { background: rgba(255,255,255,0.07); }
			#${BTN_ID}:active { transform: translateY(0.5px); }
			#${BTN_ID} .tvpn-home-dot {
				width: 8px; height: 8px; border-radius: 999px;
				background: rgba(59,130,246,0.9);
				box-shadow: 0 0 0 3px rgba(59,130,246,0.16);
			}
		`;
		document.head.appendChild(style);
	}

	function findHeaderActionsEl() {
		// Try multiple selectors across Directus versions.
		return (
			document.querySelector('.private-view__header .actions') ||
			document.querySelector('.private-view__header [class*="actions"]') ||
			document.querySelector('header .actions') ||
			document.querySelector('header [class*="actions"]')
		);
	}

	function ensureButton() {
		if (document.getElementById(BTN_ID)) return;
		const actions = findHeaderActionsEl();
		if (!actions) return;

		ensureStyle();

		const btn = document.createElement('button');
		btn.id = BTN_ID;
		btn.type = 'button';
		btn.title = 'Перейти на Главную';
		btn.innerHTML = `<span class="tvpn-home-dot"></span><span>Главная</span>`;
		btn.addEventListener('click', gotoHome);

		// Prefer placing it as the first action.
		actions.prepend(btn);
	}

	// Initial attempt + keep alive on route/layout changes.
	ensureButton();
	const mo = new MutationObserver(() => ensureButton());
	mo.observe(document.documentElement, { childList: true, subtree: true });
}

// On some Directus builds the main content area is constrained by max-width containers
// above our module (e.g. `.private-view__main` / `.private-view__content`).
// Scoped CSS inside `module.vue` cannot override ancestor styles (CSS can't target parents),
// so we apply a small global override and enable it ONLY when we're on `/admin/tvpn-home`.
function injectTvpnHomeFullWidthLayoutFix() {
	if (typeof window === 'undefined' || typeof document === 'undefined') return;

	const STYLE_ID = 'tvpn-home-fullwidth-layout-style';
	const ACTIVE_CLASS = 'tvpn-home-route-active';
	const MAX_PARENT_HOPS = 10;

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

	function applyInlineBreakout() {
		// Some Directus versions wrap module content in centered, max-width containers.
		// We can't "select parents" in CSS, so remove constraints by walking up from `.page`.
		const page = document.querySelector('.page');
		if (!page) return;

		let el = page.parentElement;
		let hops = 0;
		while (el && hops < MAX_PARENT_HOPS && el !== document.body && el !== document.documentElement) {
			const cs = window.getComputedStyle(el);

			// Remove width constraints.
			if (cs.maxWidth && cs.maxWidth !== 'none') el.style.maxWidth = 'none';
			el.style.width = '100%';

			// If container is centered via auto margins, uncenter it.
			if (cs.marginLeft === 'auto') el.style.marginLeft = '0';
			if (cs.marginRight === 'auto') el.style.marginRight = '0';

			el = el.parentElement;
			hops += 1;
		}
	}

	function updateActiveFlag() {
		const root = document.documentElement;
		if (!root) return;
		const active = isTvpnHomeRoute();
		if (active) root.classList.add(ACTIVE_CLASS);
		else root.classList.remove(ACTIVE_CLASS);

		// Apply breakout after route changes/render.
		if (active) window.setTimeout(applyInlineBreakout, 0);
	}

	ensureStyle();
	updateActiveFlag();

	// Directus is an SPA; watch route changes cheaply.
	let lastPath = window.location.pathname;
	window.setInterval(() => {
		const current = window.location.pathname;
		if (current !== lastPath) {
			lastPath = current;
			updateActiveFlag();
			return;
		}
		// Also re-apply breakout in case Directus re-rendered wrappers.
		if (document.documentElement?.classList?.contains(ACTIVE_CLASS)) applyInlineBreakout();
	}, 250);
}

injectGlobalHomeButton();
injectTvpnHomeFullWidthLayoutFix();

export default defineModule({
  id: 'tvpn-home',
  name: 'Главная',
  icon: 'space_dashboard',
  color: '#3B82F6',
  routes: [
    {
      path: '',
      component: ModuleHome,
    },
  ],
});

