import {
  createRouter,
  createWebHistory,
  createWebHashHistory,
} from "vue-router";
import { isDesktop } from "@/platform/desktop";
import { routes } from "./routes";
import { registerNavigationGuards } from "./guards";

export const router = createRouter({
  history: isDesktop ? createWebHashHistory() : createWebHistory(),
  routes: [
    ...routes,
    ...(isDesktop
      ? [
          {
            path: "/connection",
            component: () =>
              import("@/features/connection/pages/Connection.vue"),
            meta: { title: "服务器连接" },
          },
        ]
      : []),
  ],
});

registerNavigationGuards(router);
