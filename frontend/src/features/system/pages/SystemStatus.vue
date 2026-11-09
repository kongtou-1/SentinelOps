<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { api } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import type { Row } from "@/types/records";
import DiagnosisPolicy from "@/features/system/components/DiagnosisPolicy.vue";
import PlatformHealth from "@/features/system/components/PlatformHealth.vue";
const live = ref<Row>(),
  ready = ref<Row>(),
  status = ref<Row>(),
  components = ref<Row>(),
  records = ref<Row[]>([]),
  backupError = ref(""),
  error = ref(""),
  checked = ref(""),
  activeTab = ref("overview"),
  editing = ref(false),
  loading = ref(false);
async function load() {
  loading.value = true;
  error.value = "";
  const r = await Promise.allSettled([
    api<Row>("/health/live"),
    api<Row>("/health/ready"),
    api<Row>("/system/status"),
    api<Row>("/system/components"),
    api<Row[]>("/system/backup-records?limit=100"),
  ]);
  live.value = r[0].status === "fulfilled" ? r[0].value : undefined;
  ready.value = r[1].status === "fulfilled" ? r[1].value : undefined;
  status.value = r[2].status === "fulfilled" ? r[2].value : undefined;
  components.value = r[3].status === "fulfilled" ? r[3].value : undefined;
  records.value = r[4].status === "fulfilled" ? r[4].value : [];
  backupError.value =
    r[4].status === "rejected" ? "备份记录读取失败，请刷新重试" : "";
  const names = ["平台接口", "数据库就绪检查", "系统信息", "平台组件"];
  error.value = r
    .slice(0, 4)
    .map((x, index) =>
      x.status === "rejected"
        ? `${names[index]}：${x.reason instanceof Error ? x.reason.message : "检查失败"}`
        : "",
    )
    .filter(Boolean)
    .join("；");
  checked.value = new Date().toLocaleString("zh-CN", { hour12: false });
  loading.value = false;
}
const { refresh } = usePoll(load, () => (editing.value ? null : 30000));
</script>
<template>
  <div class="list-workspace">
    <section class="page-header">
      <div class="page-heading">
        <div>
          <div class="heading-copy">
            <h1>系统状态</h1>
            <PageIntro />
          </div>
        </div>

        <div class="actions">
          <span v-if="checked" class="checked-time">更新于 {{ checked }}</span>
          <el-button :loading="loading" @click="refresh">刷新状态</el-button>
        </div>
      </div>
    </section>
    <section class="panel workspace-content">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="error-box"
      />
      <el-tabs
        class="workspace-tabs"
        v-model="activeTab"
        aria-label="系统状态分类"
      >
        <el-tab-pane label="运行概况" name="overview" />
        <el-tab-pane label="后台任务" name="tasks" />
        <el-tab-pane label="备份与恢复" name="backup" />
        <el-tab-pane label="自动诊断" name="diagnosis" />
      </el-tabs>
      <PlatformHealth
        :active-tab="activeTab"
        :data="components"
        :records="records"
        :backup-error="backupError"
        :live="live"
        :ready="ready"
        :status="status"
        :loading="loading"
        @editing="editing = $event"
        @refresh="refresh"
      />
      <DiagnosisPolicy v-show="activeTab === 'diagnosis'" />
    </section>
  </div>
</template>
<style scoped>
.checked-time {
  color: var(--el-text-color-secondary);
}
</style>
