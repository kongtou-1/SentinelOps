<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Search,
  Fold,
  Expand,
  Moon,
  Menu,
  Sunny,
  SwitchButton,
  DataLine,
} from "@element-plus/icons-vue";
import { useAuth } from "@/features/auth/store";
import { isDesktop } from "@/platform/desktop";
import { label } from "@/utils/display";
import {
  theme,
  navigationLayout,
  toggleTheme,
  toggleNavigation,
} from "@/composables/useAppearance";
import { navigation } from "@/config/navigation";
import NotificationBell from "@/features/operations/components/NotificationBell.vue";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const sidebarOpen = ref(true);
watch(
  () => route.path,
  () => {
    sidebarOpen.value = false;
  },
);
function moveNavigation() {
  toggleNavigation();
  sidebarOpen.value = true;
}
const searchOpen = ref(false),
  searchText = ref("");
const visible = computed(() =>
  navigation
    .map((g) => ({ ...g, items: g.items.filter((i) => auth.can(i[2]!)) }))
    .filter((g) => g.items.length),
);
const matches = computed(() =>
  visible.value
    .flatMap((g) => g.items)
    .filter((i) =>
      i[0]!.toLowerCase().includes(searchText.value.trim().toLowerCase()),
    ),
);
function active(items: string[][]) {
  if (route.path.endsWith("/diagnosis"))
    return items.some((i) => i[1] === "/approvals");
  return items.some((i) =>
    i[1] === "/"
      ? route.path === "/"
      : route.path === i[1] || route.path.startsWith(i[1] + "/"),
  );
}
function go(target: string) {
  searchOpen.value = false;
  searchText.value = "";
  void router.push(target);
}
function openStandalonePage(event: MouseEvent, path: string) {
  if (isDesktop) {
    event.preventDefault();
    void router.push(path);
    return;
  }
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const tab = window.open(path, "_blank");
  if (tab) {
    tab.opener = null;
    event.preventDefault();
  }
}
async function logout() {
  await auth.logout().catch(() => {});
  await router.push("/login");
}
function shortcut(e: KeyboardEvent) {
  if (
    (e.ctrlKey || e.metaKey) &&
    e.key.toLowerCase() === "k" &&
    route.path !== "/login"
  ) {
    e.preventDefault();
    searchOpen.value = !searchOpen.value;
  }
}
onMounted(() => {
  window.addEventListener("keydown", shortcut);
});
onUnmounted(() => {
  window.removeEventListener("keydown", shortcut);
});
</script>
<template>
  <div
    class="app-shell"
    :class="{ 'side-layout': navigationLayout === 'side' }"
  >
    <header class="topbar">
      <router-link class="brand" to="/" aria-label="SentinelOps 总览">
        <span class="brand-mark"
          ><el-icon><DataLine /></el-icon></span
        ><span>SentinelOps</span>
      </router-link>
      <button
        class="global-search"
        aria-label="搜索页面"
        @click="searchOpen = true"
      >
        <el-icon><Search /></el-icon><span>搜索页面</span><kbd>Ctrl K</kbd>
      </button>
      <nav
        v-if="navigationLayout === 'top'"
        class="top-navigation"
        aria-label="主导航"
      >
        <template v-for="group in visible" :key="group.title">
          <el-tooltip
            v-if="group.items.length === 1"
            :content="group.title"
            placement="bottom"
          >
            <router-link
              :to="group.items[0]![1]!"
              class="nav-icon"
              :class="{ active: active(group.items) }"
              :aria-label="group.title"
              :aria-current="active(group.items) ? 'page' : undefined"
              ><el-icon><component :is="group.icon" /></el-icon
            ></router-link>
          </el-tooltip>
          <el-dropdown v-else trigger="click" @command="go">
            <button
              class="nav-icon"
              :class="{ active: active(group.items) }"
              :aria-label="group.title"
              :title="group.title"
            >
              <el-icon><component :is="group.icon" /></el-icon>
            </button>
            <template #dropdown
              ><el-dropdown-menu
                ><el-dropdown-item
                  v-for="item in group.items"
                  :key="item[1]"
                  :command="item[1]"
                  :class="{ 'menu-current': route.path === item[1] }"
                  >{{ item[0] }}</el-dropdown-item
                ></el-dropdown-menu
              ></template
            >
          </el-dropdown>
        </template>
      </nav>
      <div class="header-tools">
        <router-link v-if="isDesktop" to="/connection" class="discover-entry"
          >服务器连接</router-link
        >
        <NotificationBell />
        <a
          v-if="auth.can('resources:read')"
          href="/project-intro"
          target="_blank"
          rel="noopener"
          class="discover-entry project-intro-entry"
          @click="openStandalonePage($event, '/project-intro')"
          >项目介绍 ↗</a
        >
        <a
          v-if="auth.can('market:read')"
          href="/discover/skills"
          target="_blank"
          rel="noopener"
          class="discover-entry"
          @click="openStandalonePage($event, '/discover/skills')"
          >资源中心 ↗</a
        >
        <button
          v-if="navigationLayout === 'side'"
          class="nav-icon mobile-menu"
          aria-label="打开左侧导航"
          @click="sidebarOpen = !sidebarOpen"
        >
          <el-icon><Menu /></el-icon>
        </button>
        <el-tooltip
          :content="
            navigationLayout === 'top' ? '导航移到左侧' : '导航移到顶部'
          "
        >
          <button
            class="nav-icon navigation-toggle"
            :aria-label="
              navigationLayout === 'top' ? '导航移到左侧' : '导航移到顶部'
            "
            @click="moveNavigation"
          >
            <el-icon
              ><component :is="navigationLayout === 'top' ? Fold : Expand"
            /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip
          :content="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
        >
          <button
            class="nav-icon theme-toggle"
            :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
            @click="toggleTheme"
          >
            <el-icon><Sunny v-if="theme === 'dark'" /><Moon v-else /></el-icon>
          </button>
        </el-tooltip>
        <el-dropdown trigger="click" @command="logout"
          ><button
            class="account-button"
            :aria-label="'账号 ' + auth.user?.username"
          >
            <span>{{
              auth.user?.username.slice(0, 1).toUpperCase()
            }}</span></button
          ><template #dropdown
            ><el-dropdown-menu
              ><el-dropdown-item disabled
                >{{ auth.user?.username }} ·
                {{ label(auth.user?.role) }}</el-dropdown-item
              ><el-dropdown-item command="logout" :icon="SwitchButton"
                >退出登录</el-dropdown-item
              ></el-dropdown-menu
            ></template
          ></el-dropdown
        >
      </div>
    </header>
    <button
      v-if="navigationLayout === 'side' && sidebarOpen"
      class="sidebar-backdrop"
      aria-label="收起左侧导航"
      @click="sidebarOpen = false"
    />
    <aside
      v-if="navigationLayout === 'side'"
      class="sidebar"
      :class="{ 'is-open': sidebarOpen }"
    >
      <router-link class="brand sidebar-brand" to="/"
        ><span class="brand-mark"
          ><el-icon><DataLine /></el-icon></span
        ><span>SentinelOps</span></router-link
      >
      <nav aria-label="左侧主导航">
        <div v-for="group in visible" :key="group.title" class="sidebar-group">
          <router-link
            :to="group.items[0]![1]!"
            class="sidebar-link"
            :class="{ active: active(group.items) }"
            :aria-current="active(group.items) ? 'page' : undefined"
            ><el-icon><component :is="group.icon" /></el-icon
            ><span>{{ group.title }}</span></router-link
          >
          <div
            v-if="active(group.items) && group.items.length > 1"
            class="sidebar-children"
          >
            <router-link
              v-for="item in group.items"
              :key="item[1]"
              :to="item[1]!"
              :class="{ active: route.path === item[1] }"
              >{{ item[0] }}</router-link
            >
          </div>
        </div>
      </nav>
    </aside>
    <main class="page"><router-view :key="route.path" /></main>
    <footer class="app-footer">
      <span><i class="status-dot" />SentinelOps</span
      ><span>感知 · 预测 · 诊断 · 审批 · 复盘</span>
    </footer>
    <el-dialog
      v-model="searchOpen"
      title="搜索页面"
      width="560px"
      class="search-dialog"
      :close-on-click-modal="true"
    >
      <el-input
        v-model="searchText"
        placeholder="搜索资源、预测、告警、审批…"
        :prefix-icon="Search"
        autofocus
        clearable
        @keyup.enter="matches[0] && go(matches[0][1]!)"
      />
      <div class="search-results">
        <button v-for="item in matches" :key="item[1]" @click="go(item[1]!)">
          <span>{{ item[0] }}</span
          ><small>{{ item[1] }}</small>
        </button>
        <div v-if="!matches.length" class="empty-hint">没有匹配的页面</div>
      </div>
    </el-dialog>
  </div>
</template>
