<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import { Connection } from "@element-plus/icons-vue";
import {
  server,
  connectionError,
  testServer,
  saveServer,
  normalizeServer,
} from "@/platform/desktop";
import { useAuth } from "@/features/auth/store";

const router = useRouter(),
  auth = useAuth();
const address = ref(server.value.url),
  busy = ref(false),
  tested = ref("");
const error = ref(connectionError.value);
async function test() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  tested.value = "";
  try {
    tested.value = await testServer(address.value);
  } catch (e) {
    error.value = String(e instanceof Error ? e.message : e);
  } finally {
    busy.value = false;
  }
}
async function connect() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const target = normalizeServer(address.value);
    if (auth.user && target !== server.value.url) {
      try {
        await ElMessageBox.confirm(
          "更换服务器后需要重新登录，未保存的内容将丢失。",
          "更换服务器",
          {
            type: "warning",
            confirmButtonText: "更换服务器",
            cancelButtonText: "取消",
          },
        );
      } catch {
        return;
      }
    }
    // Validate before ending the current session. Persisting validates again natively.
    await testServer(target);
    if (target === server.value.url) {
      connectionError.value = "";
      await router.replace(auth.user ? "/" : "/login");
      return;
    }
    if (auth.user) await auth.logout().catch(() => {});
    await saveServer(target);
    auth.clear();
    connectionError.value = "";
    // A full reload discards all old service stores, requests and page drafts.
    window.location.replace(window.location.pathname + "#/login");
  } catch (e) {
    error.value = String(e instanceof Error ? e.message : e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="login-screen">
    <div class="connection-panel">
      <div class="connection-brand">
        <el-icon><Connection /></el-icon><strong>SentinelOps</strong>
      </div>
      <h1>连接服务器</h1>
      <el-form label-position="top" @submit.prevent="connect">
        <el-form-item label="服务器地址" required>
          <el-input
            v-model="address"
            placeholder="https://ops.example.com"
            :disabled="busy"
            clearable
            @input="tested = ''"
          />
        </el-form-item>
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
        />
        <el-alert
          v-else-if="tested"
          title="连接成功"
          type="success"
          :closable="false"
          show-icon
        />
        <div class="connection-actions">
          <el-button :loading="busy" @click="test">测试连接</el-button>
          <el-button native-type="submit" type="primary" :loading="busy"
            >保存并连接</el-button
          >
        </div>
        <el-button
          v-if="server.url"
          link
          :disabled="busy"
          @click="router.replace(auth.user ? '/' : '/login')"
          >返回{{ auth.user ? "平台" : "登录" }}</el-button
        >
      </el-form>
    </div>
  </div>
</template>
<style scoped>
.connection-panel {
  width: min(460px, calc(100vw - 48px));
  padding: 40px;
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--surface);
  box-shadow: 0 24px 80px #0002;
}
.connection-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--el-color-primary);
  font-size: 23px;
}
h1 {
  margin: 30px 0 24px;
  font-size: 24px;
}
.connection-actions {
  display: flex;
  margin: 24px 0 16px;
}
.connection-actions > button {
  flex: 1;
}
</style>
