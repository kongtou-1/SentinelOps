<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuth } from "@/features/auth/store";
import { DataLine } from "@element-plus/icons-vue";
import { isDesktop, server } from "@/platform/desktop";
const auth = useAuth(),
  route = useRoute(),
  router = useRouter(),
  username = ref(""),
  password = ref(""),
  busy = ref(false),
  error = ref("");
async function submit() {
  if (busy.value) return;
  error.value = "";
  if (!/^[a-z0-9_.-]{3,64}$/.test(username.value) || !password.value) {
    error.value = "请输入有效用户名和密码";
    return;
  }
  busy.value = true;
  try {
    await auth.login(username.value, password.value);
    password.value = "";
    const target = String(route.query.redirect || "/");
    await router.replace(
      target.startsWith("/") && !target.startsWith("//") && target != "/login"
        ? target
        : "/",
    );
  } catch (e) {
    error.value = e instanceof Error ? e.message : "登录失败";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="login-screen">
    <div class="login-wrap">
      <div class="brand">
        <span class="brand-mark"
          ><el-icon><DataLine /></el-icon></span
        ><span>SentinelOps<small>智能预测运维</small></span>
      </div>
      <div class="login-panel">
        <h1>登录运维平台</h1>
        <div v-if="isDesktop" class="desktop-server">
          <span>{{ server.url }}</span
          ><router-link to="/connection">服务器连接</router-link>
        </div>
        <p>人机协同智能预测运维工作台</p>
        <el-form label-position="top" @submit.prevent="submit"
          ><el-form-item label="用户名" required
            ><el-input
              v-model="username"
              placeholder="例如 admin"
              autocomplete="username"
              :maxlength="64" /></el-form-item
          ><el-form-item label="密码" required
            ><el-input
              v-model="password"
              type="password"
              show-password
              placeholder="请输入登录密码"
              autocomplete="current-password"
              :maxlength="128" /></el-form-item
          ><el-alert
            v-if="error"
            :title="error"
            type="error"
            :closable="false"
            class="error-box"
          /><el-button
            native-type="submit"
            type="primary"
            :loading="busy"
            style="width: 100%; height: 42px"
            >登录</el-button
          ></el-form
        >
        <div class="login-note">
          联系管理员开通账号或重置密码。<br />按账号权限开放查看、诊断、审批及执行操作。
        </div>
      </div>
    </div>
  </div>
</template>
<style scoped>
.desktop-server {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 16px 0;
  font-size: 13px;
}
.desktop-server span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--el-text-color-secondary);
}
</style>
