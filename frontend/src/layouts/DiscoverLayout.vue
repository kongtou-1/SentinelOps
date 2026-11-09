<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Collection, Moon, Sunny } from "@element-plus/icons-vue";
import { useAuth } from "@/features/auth/store";
import { isDesktop } from "@/platform/desktop";
import { theme, toggleTheme } from "@/composables/useAppearance";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const activeTab = computed(() =>
  route.path === "/discover/tools" || route.query.kind === "tool"
    ? "tools"
    : "skills",
);
async function logout() {
  await auth.logout().catch(() => {});
  await router.push("/login");
}
</script>
<template>
  <div class="discover-shell">
    <header class="discover-topbar">
      <router-link to="/discover/skills" class="discover-brand"
        ><el-icon><Collection /></el-icon><strong>资源中心</strong
        ><span>SentinelOps</span></router-link
      >
      <div class="actions">
        <router-link v-if="isDesktop" to="/connection">服务器连接</router-link>
        <router-link to="/" class="return-platform">← 返回运维平台</router-link>
        <el-button
          text
          circle
          :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          @click="toggleTheme"
          ><el-icon><Sunny v-if="theme === 'dark'" /><Moon v-else /></el-icon
        ></el-button>
        <el-dropdown @command="logout"
          ><el-button text>{{ auth.user?.username }} ▾</el-button
          ><template #dropdown
            ><el-dropdown-menu
              ><el-dropdown-item command="logout"
                >退出登录</el-dropdown-item
              ></el-dropdown-menu
            ></template
          ></el-dropdown
        >
      </div>
    </header>
    <div class="discover-navigation">
      <nav class="discover-tabs" aria-label="资源中心导航">
        <router-link
          to="/discover/skills"
          :class="{ active: activeTab === 'skills' }"
          >Skill 市场</router-link
        >
        <router-link
          to="/discover/tools"
          :class="{ active: activeTab === 'tools' }"
          >实用工具</router-link
        >
      </nav>
      <div class="actions discover-actions">
        <router-link to="/discover/mine">我的提交</router-link>
        <router-link v-if="auth.can('market:review')" to="/discover/manage"
          >内容管理</router-link
        >
        <el-button
          v-if="auth.can('market:submit')"
          type="primary"
          @click="
            router.push({
              path: '/discover/items/new',
              query: { kind: activeTab === 'tools' ? 'tool' : 'skill' },
            })
          "
          >提交内容</el-button
        >
      </div>
    </div>
    <main class="discover-main"><router-view :key="route.path" /></main>
  </div>
</template>
<style scoped>
.discover-shell {
  min-height: var(--app-viewport-height, 100vh);
  background: var(--page-bg);
}
.discover-topbar {
  height: 76px;
  padding: 0 max(24px, calc((100vw - 1240px) / 2));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.discover-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.discover-brand .el-icon {
  font-size: 26px;
  color: var(--el-color-primary);
}
.discover-brand strong {
  font-size: 20px;
}
.discover-brand span {
  color: var(--muted);
  font-size: 13px;
}
.discover-navigation {
  max-width: 1240px;
  padding: 24px 0 0;
  margin: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border);
}
.discover-tabs {
  display: flex;
  gap: 30px;
  align-self: stretch;
}
.discover-tabs a {
  font-size: 18px;
  padding: 12px 0 20px;
  color: var(--muted);
  border-bottom: 3px solid transparent;
}
.discover-tabs a.active {
  color: var(--el-text-color-primary);
  border-color: var(--el-color-primary);
  font-weight: 650;
}
.discover-actions {
  padding-bottom: 12px;
  gap: 18px;
}
.discover-actions a {
  color: var(--el-text-color-regular);
}
.discover-main {
  max-width: 1240px;
  margin: 0 auto;
  padding: 32px 0;
  min-height: calc(var(--app-viewport-height, 100vh) - 230px);
}
@media (max-width: 1290px) {
  .discover-navigation,
  .discover-main {
    margin-left: 24px;
    margin-right: 24px;
  }
}
@media (max-width: 700px) {
  .discover-topbar {
    padding: 0 16px;
    height: 64px;
  }
  .discover-brand span {
    display: none;
  }
  .return-platform {
    font-size: 12px;
  }
  .discover-navigation {
    flex-wrap: wrap;
    padding-top: 12px;
    margin: 0 16px;
    gap: 8px;
  }
  .discover-tabs {
    gap: 24px;
  }
  .discover-tabs a {
    padding-bottom: 12px;
  }
  .discover-main {
    margin: 0 16px;
    padding-top: 24px;
  }
  .discover-actions {
    width: 100%;
    justify-content: flex-end;
    gap: 16px;
  }
}
</style>
