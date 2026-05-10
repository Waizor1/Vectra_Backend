import { defineModule } from "@directus/extensions-sdk";

import ModuleUtmStats from "./module.vue";

const ROUTE_PATH = "tvpn-utm-stats";
const ACTIVE_CLASS = "tvpn-utm-stats-route-active";
const STYLE_ID = "tvpn-utm-stats-fullwidth-layout-style";

function injectFullwidthLayoutStyle() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const isUtmStatsRoute = () =>
    window.location.pathname.includes(`/admin/${ROUTE_PATH}`);

  const ensureStyle = () => {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
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
  };

  const syncActive = () => {
    ensureStyle();
    document.documentElement.classList.toggle(ACTIVE_CLASS, isUtmStatsRoute());
  };

  // Initial sync + listen for SPA navigations.
  syncActive();
  window.addEventListener("popstate", syncActive);
  // Patch pushState/replaceState once across modules; use a unique guard.
  if (!window.__tvpnUtmStatsHistoryPatched) {
    window.__tvpnUtmStatsHistoryPatched = true;
    const origPush = history.pushState;
    const origReplace = history.replaceState;
    history.pushState = function (...args) {
      const r = origPush.apply(this, args);
      syncActive();
      return r;
    };
    history.replaceState = function (...args) {
      const r = origReplace.apply(this, args);
      syncActive();
      return r;
    };
  }
}

injectFullwidthLayoutStyle();

export default defineModule({
  id: ROUTE_PATH,
  name: "UTM Stats",
  icon: "trending_up",
  routes: [
    {
      path: "",
      component: ModuleUtmStats,
    },
  ],
});
