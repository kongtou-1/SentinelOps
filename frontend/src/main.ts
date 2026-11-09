import { createApp, watch } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";

import { applyTheme, theme } from "@/composables/useAppearance";
import "@/styles/index.css";
import App from "@/App.vue";
import { router } from "@/router";
import {
  isDesktop,
  initializeDesktop,
  connectionError,
  openExternal,
} from "@/platform/desktop";
import { invoke } from "@/platform/native";

applyTheme();

async function start() {
  if (isDesktop) {
    document.documentElement.dataset.platform = "desktop";
    watch(
      theme,
      (value) =>
        void invoke("set_window_theme", { theme: value }).catch(() => {}),
      { immediate: true },
    );
    try {
      await initializeDesktop();
    } catch (error) {
      connectionError.value = String(error);
    }
    document.addEventListener(
      "click",
      (event) => {
        const anchor = (event.target as Element)?.closest?.(
          "a[href]",
        ) as HTMLAnchorElement | null;
        if (!anchor || anchor.download) return;
        const url = new URL(anchor.href);
        if (
          ["http:", "https:"].includes(url.protocol) &&
          url.origin !== window.location.origin
        ) {
          event.preventDefault();
          void openExternal(url.href).catch((error) => {
            connectionError.value = String(error);
          });
        } else if (anchor.target === "_blank") {
          event.preventDefault();
          const target = url.hash.startsWith("#/")
            ? url.hash.slice(1)
            : url.pathname + url.search + url.hash;
          void router.push(target);
        }
      },
      true,
    );
  }
  createApp(App)
    .use(createPinia())
    .use(router)
    .use(ElementPlus, { locale: zhCn })
    .mount("#app");
}
void start();
