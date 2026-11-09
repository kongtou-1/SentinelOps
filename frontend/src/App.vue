<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuth } from "@/features/auth/store";
import ConsoleLayout from "@/layouts/ConsoleLayout.vue";
import DiscoverLayout from "@/layouts/DiscoverLayout.vue";
import { isDesktop } from "@/platform/desktop";

const route = useRoute();
const router = useRouter();
const auth = useAuth();

function expired() {
  auth.clear();
  void router.replace({ path: "/login", query: { redirect: route.fullPath } });
}
function refreshPermissions() {
  if (!document.hidden && !["/login", "/connection"].includes(route.path))
    void auth.load().catch(() => {});
}
onMounted(() => {
  window.addEventListener("session-expired", expired);
  window.addEventListener("permissions-changed", refreshPermissions);
  window.addEventListener("focus", refreshPermissions);
  document.addEventListener("visibilitychange", refreshPermissions);
});
onUnmounted(() => {
  window.removeEventListener("session-expired", expired);
  window.removeEventListener("permissions-changed", refreshPermissions);
  window.removeEventListener("focus", refreshPermissions);
  document.removeEventListener("visibilitychange", refreshPermissions);
});
</script>

<template>
  <div v-if="isDesktop" class="desktop-titlebar" aria-hidden="true">
    <div class="desktop-titlebar-drag">
      <img src="/favicon.svg" alt="" width="18" height="18" />
      <span>SentinelOps</span>
    </div>
  </div>
  <router-view
    v-if="
      route.path === '/login' ||
      route.path === '/connection' ||
      route.meta.introduction
    "
  />
  <DiscoverLayout v-else-if="route.meta.discover" />
  <ConsoleLayout v-else />
</template>
