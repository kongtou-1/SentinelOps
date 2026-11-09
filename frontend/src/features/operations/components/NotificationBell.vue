<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { Bell } from "@element-plus/icons-vue";
import { api } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
const unread = ref<number>();
const { refresh } = usePoll(
  async () => {
    try {
      unread.value = (
        await api<{ unread: number }>("/notifications/count")
      ).unread;
    } catch {
      unread.value = undefined;
    }
  },
  () => 30000,
);
const refreshUnread = () => void refresh();
onMounted(() => window.addEventListener("notifications-read", refreshUnread));
onUnmounted(() =>
  window.removeEventListener("notifications-read", refreshUnread),
);
</script>
<template>
  <router-link to="/notifications" class="nav-icon" aria-label="我的待办"
    ><el-badge :value="unread" :hidden="!unread" :max="99"
      ><el-icon><Bell /></el-icon></el-badge
  ></router-link>
</template>
