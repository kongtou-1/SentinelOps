<script setup lang="ts">
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";

type MailSettings = Models["MailSettingsResponse"];
const auth = useAuth();
const canManage = computed(() => auth.can("users:manage"));
const saved = ref<MailSettings>();
const editing = ref(false);
const form = ref({
  host: "",
  port: 465,
  security: "ssl",
  sender: "",
  username: "",
  password: "",
});
const busy = ref(false),
  loading = ref(false),
  testing = ref(false),
  error = ref("");
const recipient = ref("");
const dirty = computed(
  () =>
    !!saved.value &&
    (form.value.password !== "" ||
      ["host", "port", "security", "sender", "username"].some(
        (key) =>
          form.value[key as keyof typeof form.value] !==
          saved.value?.[key as keyof MailSettings],
      )),
);

async function reload() {
  loading.value = true;
  try {
    const data = await api<MailSettings>("/system/mail-settings");
    saved.value = data;
    form.value = {
      host: data.host,
      port: data.port,
      security: data.security,
      sender: data.sender,
      username: data.username,
      password: "",
    };
    editing.value = false;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "平台邮箱加载失败";
  } finally {
    loading.value = false;
  }
}
async function save() {
  if (!saved.value || !form.value.host || !form.value.sender) {
    ElMessage.warning("请填写 SMTP 服务器和发件邮箱");
    return;
  }
  busy.value = true;
  try {
    saved.value = await api<MailSettings>("/system/mail-settings", "PUT", {
      ...form.value,
      password: form.value.password || null,
      expected_version: saved.value.version,
    });
    form.value.password = "";
    form.value.sender = saved.value.sender;
    editing.value = false;
    ElMessage.success("平台发件邮箱已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
function cancelEdit() {
  if (!saved.value) return;
  form.value = {
    host: saved.value.host,
    port: saved.value.port,
    security: saved.value.security,
    sender: saved.value.sender,
    username: saved.value.username,
    password: "",
  };
  editing.value = false;
}
async function sendTest() {
  if (testing.value) return;
  if (!recipient.value) {
    ElMessage.warning("请填写测试收件邮箱");
    return;
  }
  if (!saved.value || dirty.value) {
    ElMessage.warning("请先保存平台邮箱设置");
    return;
  }
  testing.value = true;
  try {
    if (
      !(await confirmOperation(
        `向 ${recipient.value} 发送一封测试邮件？`,
        "发送测试邮件",
      ))
    )
      return;
    await api("/system/mail-settings/test", "POST", {
      expected_version: saved.value.version,
      recipient: recipient.value,
    });
    ElMessage.success("测试邮件已提交发送，请在收件箱确认");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "测试邮件发送失败");
  } finally {
    testing.value = false;
  }
}
onMounted(reload);
defineExpose({ dirty, reload });
</script>
<template>
  <section v-loading="loading" class="platform-mail">
    <div class="mail-heading">
      <h2>平台发件邮箱</h2>
      <el-tag v-if="dirty" type="warning">修改未保存</el-tag>
      <el-tag v-else-if="saved?.local_test_service" type="warning"
        >仅本地测试</el-tag
      >
      <el-tag v-else-if="saved" type="success">{{
        saved.source === "platform" ? "已保存" : "使用部署配置"
      }}</el-tag>
      <el-tag v-if="!canManage" type="info">只读</el-tag>
      <el-button
        v-if="canManage && !editing"
        class="mail-edit"
        :disabled="!saved || loading"
        @click="editing = true"
        >编辑</el-button
      >
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-descriptions v-if="saved && !editing" :column="2" border>
      <el-descriptions-item label="发件邮箱">{{
        saved.sender || "未配置"
      }}</el-descriptions-item>
      <el-descriptions-item label="SMTP 服务器">{{
        saved.host || "未配置"
      }}</el-descriptions-item>
      <el-descriptions-item label="端口">{{ saved.port }}</el-descriptions-item>
      <el-descriptions-item label="加密方式">{{
        { ssl: "SSL/TLS", starttls: "STARTTLS", none: "不加密（本地测试）" }[
          saved.security
        ]
      }}</el-descriptions-item>
      <el-descriptions-item label="登录账号">{{
        saved.username || "未配置"
      }}</el-descriptions-item>
      <el-descriptions-item label="密码 / SMTP 授权码">{{
        saved.password_configured ? "已配置" : "未配置"
      }}</el-descriptions-item>
    </el-descriptions>
    <el-form
      v-if="saved && editing"
      label-position="top"
      :disabled="!canManage || busy || testing"
      @submit.prevent="save"
    >
      <div class="mail-grid">
        <el-form-item label="发件邮箱" required
          ><el-input
            v-model.trim="form.sender"
            placeholder="用于发送平台通知和复盘邮件"
            :maxlength="254"
        /></el-form-item>
        <el-form-item label="SMTP 服务器" required
          ><el-input
            v-model.trim="form.host"
            placeholder="例如 smtp.example.com"
            :maxlength="253"
        /></el-form-item>
        <el-form-item label="端口" required
          ><el-input-number
            v-model="form.port"
            :min="1"
            :max="65535"
            controls-position="right"
        /></el-form-item>
        <el-form-item label="加密方式" required
          ><el-select v-model="form.security"
            ><el-option label="SSL/TLS" value="ssl" /><el-option
              label="STARTTLS"
              value="starttls" /><el-option
              label="不加密（本地测试）"
              value="none" /></el-select
        ></el-form-item>
        <el-form-item label="登录账号"
          ><el-input
            v-model.trim="form.username"
            placeholder="通常与发件邮箱相同"
            :maxlength="254"
        /></el-form-item>
        <el-form-item v-if="canManage" label="密码 / SMTP 授权码"
          ><el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="
              saved.password_configured
                ? '已配置，留空保留'
                : '填写邮箱提供的 SMTP 授权码'
            "
            :maxlength="4096"
        /></el-form-item>
      </div>
      <div v-if="canManage" class="mail-actions">
        <el-button @click="cancelEdit">取消</el-button>
        <el-button
          type="primary"
          :loading="busy"
          :disabled="!dirty"
          native-type="submit"
          >保存平台邮箱</el-button
        >
      </div>
    </el-form>
    <div v-if="canManage && saved && editing" class="mail-test">
      <el-input
        v-model.trim="recipient"
        placeholder="测试收件邮箱"
        aria-label="测试收件邮箱"
        :disabled="busy || testing"
        :maxlength="254"
      />
      <el-button :loading="testing" :disabled="busy || dirty" @click="sendTest"
        >发送测试邮件</el-button
      >
    </div>
  </section>
</template>
<style scoped>
.platform-mail {
  padding-bottom: 28px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}
.mail-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin: 16px 0 24px;
}
.mail-heading h2 {
  margin: 0;
}
.mail-edit {
  margin-left: auto;
}
.mail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
}
.mail-grid :deep(.el-input-number),
.mail-grid .el-select {
  width: 100%;
}
.mail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.mail-test {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 24px;
}
.mail-test .el-input {
  max-width: 420px;
}
.mail-actions .el-button,
.mail-test .el-button {
  margin-left: 0;
}
@media (max-width: 700px) {
  .mail-grid {
    grid-template-columns: 1fr;
  }
  .mail-test {
    flex-wrap: wrap;
  }
}
</style>
