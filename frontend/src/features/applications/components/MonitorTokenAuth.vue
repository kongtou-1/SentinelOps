<script setup lang="ts">
import { ref, watch } from "vue";
import type { Models } from "@/api/client";

const props = defineProps<{ configured: boolean }>();
const emit = defineEmits<{ change: [] }>();
const mode = ref(props.configured ? "keep" : "none");
const loginUrl = ref(""),
  refreshUrl = ref("");
const loginBody = ref(""),
  refreshBody = ref(""),
  headers = ref("");
const accessPath = ref("access_token"),
  refreshPath = ref("refresh_token");
const expiresPath = ref("expires_in"),
  refreshField = ref("refresh_token");
const lifetime = ref(300);
watch(
  [
    mode,
    loginUrl,
    refreshUrl,
    loginBody,
    refreshBody,
    headers,
    accessPath,
    refreshPath,
    expiresPath,
    refreshField,
    lifetime,
  ],
  () => emit("change"),
);

function object(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label}必须是有效的 JSON 对象`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error(`${label}必须是 JSON 对象`);
  return parsed as Record<string, unknown>;
}
function configuration(): Models["MonitorTokenAuth"] | false | undefined {
  if (mode.value === "keep") return undefined;
  if (mode.value === "none") return false;
  const authHeaders = object(headers.value, "认证请求头");
  if (Object.values(authHeaders).some((v) => typeof v !== "string"))
    throw new Error("认证请求头的值必须是字符串");
  return {
    login_url: loginUrl.value.trim(),
    login_body: object(loginBody.value, "登录请求体"),
    headers: authHeaders as Record<string, string>,
    refresh_url: refreshUrl.value.trim(),
    refresh_body: object(refreshBody.value, "刷新请求体"),
    refresh_token_field: refreshField.value.trim(),
    access_token_path: accessPath.value.trim(),
    refresh_token_path: refreshPath.value.trim(),
    expires_in_path: expiresPath.value.trim(),
    default_expires_in: lifetime.value,
  };
}
defineExpose({ configuration });
</script>

<template>
  <el-form-item label="Token 认证">
    <el-select v-model="mode">
      <el-option v-if="configured" label="保留现有认证" value="keep" />
      <el-option label="不使用自动 Token" value="none" />
      <el-option label="配置登录与刷新" value="token" />
    </el-select>
  </el-form-item>
  <template v-if="mode === 'token'">
    <el-form-item label="登录地址（POST）" required>
      <el-input
        v-model="loginUrl"
        placeholder="https://example.com/auth/login"
      />
    </el-form-item>
    <el-form-item label="登录请求体 JSON">
      <el-input
        v-model="loginBody"
        type="textarea"
        :rows="3"
        autocomplete="off"
        placeholder='{"username":"monitor","password":""}'
      />
    </el-form-item>
    <el-form-item label="认证请求头 JSON">
      <el-input
        v-model="headers"
        type="textarea"
        :rows="2"
        autocomplete="off"
      />
    </el-form-item>
    <el-form-item label="刷新地址（POST）">
      <el-input
        v-model="refreshUrl"
        placeholder="https://example.com/auth/refresh"
      />
    </el-form-item>
    <template v-if="refreshUrl.trim()">
      <el-form-item label="刷新请求体 JSON">
        <el-input
          v-model="refreshBody"
          type="textarea"
          :rows="2"
          autocomplete="off"
        />
      </el-form-item>
      <el-form-item label="刷新 Token 请求字段" required>
        <el-input v-model="refreshField" />
      </el-form-item>
    </template>
    <el-form-item label="Access Token 路径" required>
      <el-input v-model="accessPath" placeholder="data.access_token" />
    </el-form-item>
    <el-form-item label="Refresh Token 路径" required>
      <el-input v-model="refreshPath" placeholder="data.refresh_token" />
    </el-form-item>
    <el-form-item label="有效期响应路径（秒）" required>
      <el-input v-model="expiresPath" placeholder="data.expires_in" />
    </el-form-item>
    <el-form-item label="未返回有效期时（秒）" required>
      <el-input-number v-model="lifetime" :min="1" :max="86400" />
    </el-form-item>
  </template>
</template>
