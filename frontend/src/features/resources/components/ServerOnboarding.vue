<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, download } from "@/api/client";
import type { Row } from "@/types/records";
import { date } from "@/utils/display";
import { availabilityLabels, monitoringReason } from "@/utils/monitoring";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
const props = defineProps<{ server: Row }>(),
  auth = useAuth();
const summary = ref<Row>(),
  error = ref("");
const names: Record<string, string> = {
  node: "node_exporter",
  cadvisor: "cAdvisor",
  "docker-state": "Docker 状态（可选）",
  blackbox: "HTTP 探测（可选，平台视角）",
};
const ready = computed(() =>
  ["node", "cadvisor"].every(
    (j) => summary.value?.collectors[j]?.availability === "available",
  ),
);
async function load() {
  try {
    summary.value = (
      await api<Row[]>(
        "/monitoring/servers-summary?server_ids=" + props.server.id,
      )
    )[0];
    error.value = "";
  } catch (e) {
    summary.value = undefined;
    error.value = e instanceof Error ? e.message : "接入检查失败";
  }
}
const { refresh } = usePoll(load, () => 30000);
async function compose() {
  try {
    await download(`/servers/${props.server.id}/collectors-compose`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "下载失败");
  }
}
async function probeConfig() {
  try {
    await download(`/servers/${props.server.id}/probe-config`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "下载失败");
  }
}
</script>
<template>
  <section class="panel">
    <div class="status-row">
      <h2>监控接入检查</h2>
      <el-button @click="refresh">重新检查</el-button>
      <el-button
        v-if="auth.can('resources:manage') && server.blackbox_exporter_target"
        @click="probeConfig"
        >下载主机探测配置</el-button
      >
    </div>
    <div class="onboarding-steps">
      <el-steps :active="ready ? 3 : 1" simple>
        <el-step title="创建资产" /><el-step title="部署采集器" /><el-step
          title="验证接通"
        /><el-step title="查看指标" />
      </el-steps>
    </div>
    <p class="form-hint">
      在目标服务器部署采集器，再将资产中的地址和端口配置为 Prometheus
      可访问的地址。HTTP 探测从平台侧发起。
    </p>
    <template v-if="auth.can('resources:manage')">
      <el-button @click="compose">下载采集器 Compose</el-button>
      <p class="form-hint">
        核对下载文件中的镜像与挂载；Docker 状态采集器需要构建或分发项目的
        collector 镜像。部署命令：
      </p>
      <pre>docker compose -f collectors-compose.yaml up -d</pre>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table
      :data="
        Object.entries(summary?.collectors || {}).map(([job, s]) => ({
          job,
          ...(s as Row),
        }))
      "
    >
      <el-table-column label="采集项" min-width="200"
        ><template #default="{ row }">{{
          names[row.job]
        }}</template></el-table-column
      >
      <el-table-column
        prop="target"
        label="目标"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column label="接入状态" width="150"
        ><template #default="{ row }">{{
          availabilityLabels[row.availability]
        }}</template></el-table-column
      >
      <el-table-column label="最近采样" width="180"
        ><template #default="{ row }">{{
          date(row.sampled_at)
        }}</template></el-table-column
      >
      <el-table-column label="原因与下一步" min-width="240"
        ><template #default="{ row }">{{
          monitoringReason(row.reason) || "采集正常"
        }}</template></el-table-column
      >
    </el-table>
  </section>
</template>

<style scoped>
.onboarding-steps {
  overflow-x: auto;
  margin: 16px 0;
}
.onboarding-steps .el-steps {
  min-width: 600px;
}
</style>
