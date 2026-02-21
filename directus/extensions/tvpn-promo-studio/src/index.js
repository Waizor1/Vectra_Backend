import { defineModule } from "@directus/extensions-sdk";

import ModulePromoStudio from "./module.vue";

function injectPromoStudioLayoutFix() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const styleId = "tvpn-promo-studio-fullwidth-layout-style";
  const activeClass = "tvpn-promo-studio-route-active";

  const isPromoStudioRoute = () =>
    window.location.pathname.includes("/admin/tvpn-promo-studio");

  const ensureStyle = () => {
    if (document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      html.${activeClass} .private-view__main,
      html.${activeClass} .private-view__content,
      html.${activeClass} .private-view__main > *,
      html.${activeClass} .private-view__content > * {
        max-width: none !important;
        width: 100% !important;
      }
      html.${activeClass} .private-view__content {
        justify-content: stretch !important;
        justify-items: stretch !important;
        align-items: stretch !important;
      }
    `;
    document.head.appendChild(style);
  };

  const updateFlag = () => {
    const root = document.documentElement;
    if (!root) return;
    if (isPromoStudioRoute()) root.classList.add(activeClass);
    else root.classList.remove(activeClass);
  };

  ensureStyle();
  updateFlag();

  const origPushState = window.history.pushState;
  const origReplaceState = window.history.replaceState;

  if (!window.__tvpnPromoStudioHistoryPatched) {
    window.__tvpnPromoStudioHistoryPatched = true;
    window.history.pushState = function pushStatePatched(...args) {
      const out = origPushState.apply(this, args);
      updateFlag();
      return out;
    };
    window.history.replaceState = function replaceStatePatched(...args) {
      const out = origReplaceState.apply(this, args);
      updateFlag();
      return out;
    };
    window.addEventListener("popstate", updateFlag);
  }
}

injectPromoStudioLayoutFix();

export default defineModule({
  id: "tvpn-promo-studio",
  name: "Promo Studio",
  icon: "workspace_premium",
  color: "#0EA5A4",
  routes: [
    {
      path: "",
      component: ModulePromoStudio,
    },
  ],
});
