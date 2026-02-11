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

injectGlobalHomeButton();

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

