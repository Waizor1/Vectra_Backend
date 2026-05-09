import { defineModule } from "@directus/extensions-sdk";

import ModuleSegmentStudio from "./module.vue";

function injectSegmentStudioLayoutFix() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const styleId = "tvpn-segment-studio-fullwidth-layout-style";
  const activeClass = "tvpn-segment-studio-route-active";

  const isStudioRoute = () =>
    window.location.pathname.includes("/admin/tvpn-segment-studio");

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
    if (isStudioRoute()) root.classList.add(activeClass);
    else root.classList.remove(activeClass);
  };

  ensureStyle();
  updateFlag();

  if (!window.__tvpnSegmentStudioHistoryPatched) {
    window.__tvpnSegmentStudioHistoryPatched = true;
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

injectSegmentStudioLayoutFix();

export default defineModule({
  id: "tvpn-segment-studio",
  name: "Segment Studio",
  icon: "campaign",
  color: "#F59E0B",
  routes: [
    {
      path: "",
      component: ModuleSegmentStudio,
    },
  ],
});
