import { defineModule } from "@directus/extensions-sdk";
import ModuleGoldenPeriod from "./module.vue";

const ROUTE_PATH = "tvpn-golden-period";
const ACTIVE_CLASS = "tvpn-golden-period-route-active";
const STYLE_ID = "tvpn-golden-period-fullwidth-layout-style";

function injectFullwidthLayoutStyle() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const isActive = () =>
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
        align-items: stretch !important;
      }
    `;
    document.head.appendChild(style);
  };

  const syncActive = () => {
    ensureStyle();
    document.documentElement.classList.toggle(ACTIVE_CLASS, isActive());
  };

  syncActive();
  window.addEventListener("popstate", syncActive);

  if (!window.__tvpnGoldenPeriodHistoryPatched) {
    window.__tvpnGoldenPeriodHistoryPatched = true;
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
  name: "Golden Period",
  icon: "local_fire_department",
  routes: [
    {
      path: "",
      component: ModuleGoldenPeriod,
    },
  ],
});
