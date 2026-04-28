import { defineModule } from "@directus/extensions-sdk";

import ModuleTariffStudio from "./module.vue";

function injectTariffStudioLayoutFix() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const styleId = "tvpn-tariff-studio-fullwidth-layout-style";
  const activeClass = "tvpn-tariff-studio-route-active";
  const isStudioRoute = () => window.location.pathname.includes("/admin/tvpn-tariff-studio");

  if (!document.getElementById(styleId)) {
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
  }

  const updateFlag = () => {
    const root = document.documentElement;
    if (!root) return;
    if (isStudioRoute()) root.classList.add(activeClass);
    else root.classList.remove(activeClass);
  };

  updateFlag();
  if (!window.__tvpnTariffStudioHistoryPatched) {
    window.__tvpnTariffStudioHistoryPatched = true;
    const origPushState = window.history.pushState;
    const origReplaceState = window.history.replaceState;
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

injectTariffStudioLayoutFix();

export default defineModule({
  id: "tvpn-tariff-studio",
  name: "Tariff Studio",
  icon: "tune",
  color: "#3BC9DB",
  routes: [
    {
      path: "",
      component: ModuleTariffStudio,
    },
  ],
});
