import type { Router } from "vue-router";
import { useAuth } from "@/features/auth/store";
import { ApiError, tokens } from "@/api/client";
import { isDesktop, server, connectionError } from "@/platform/desktop";
import { clearChunkRecovery, recoverChunk } from "@/utils/chunkRecovery";

export function registerNavigationGuards(router: Router) {
  router.beforeEach(async (to) => {
    const auth = useAuth();
    if (isDesktop) {
      if (to.path === "/connection") return;
      if (!server.value.url) return "/connection";
    }
    if (to.path === "/login") return;
    if (!tokens()) return { path: "/login", query: { redirect: to.fullPath } };
    try {
      await auth.load();
    } catch (error) {
      if (
        isDesktop &&
        !(error instanceof ApiError && [401, 403].includes(error.status))
      ) {
        connectionError.value =
          error instanceof Error ? error.message : String(error);
        return "/connection";
      }
      auth.clear();
      return { path: "/login", query: { redirect: to.fullPath } };
    }
    if (to.meta.permission && !auth.can(String(to.meta.permission)))
      return "/forbidden";
    const boards: Record<string, string> = {
      "/": "overview",
      "/incidents": "incidents",
      "/predictions": "predictions",
      "/inspections": "inspections",
      "/evaluations": "evaluations",
      "/data-governance": "data-governance",
    };
    const board = boards[to.path];
    if (board && auth.user?.dashboard_access?.[board] === false)
      return {
        path: "/forbidden",
        query: { resource_kind: "dashboard", resource_id: board },
      };
  });
  router.onError((error, to) => {
    recoverChunk(error, to.fullPath);
  });
  router.afterEach((_to, _from, failure) => {
    if (!failure) clearChunkRecovery();
  });
}
