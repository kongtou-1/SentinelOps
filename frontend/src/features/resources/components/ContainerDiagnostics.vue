<script setup lang="ts">
import { ref, onUnmounted } from "vue";
import { api } from "@/api/client";
import type { Row } from "@/types/records";
import { date } from "@/utils/display";
import {
  healthLabels,
  availabilityLabels,
  monitoringReason,
} from "@/utils/monitoring";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
const props = defineProps<{ id: string }>(),
  auth = useAuth();
const health = ref<Row>(),
  open = ref(false),
  result = ref<Row>(),
  error = ref(""),
  busy = ref(false),
  kind = ref("state");
let controller: AbortController | undefined;
onUnmounted(() => controller?.abort());
async function load() {
  try {
    health.value = await api<Row>(`/containers/${props.id}/health`);
  } catch {
    health.value = {
      status: "unknown",
      availability: "unavailable",
      reason: "query_failed",
    };
  }
}
usePoll(load, () => 30000);
async function inspect(which: string) {
  controller?.abort();
  controller = new AbortController();
  const current = controller;
  open.value = true;
  kind.value = which;
  result.value = undefined;
  error.value = "";
  busy.value = true;
  try {
    const data = await api<Row>(
      `/containers/${props.id}/diagnostics?kind=${which}`,
      "GET",
      undefined,
      current.signal,
    );
    if (!current.signal.aborted) result.value = data;
  } catch (e) {
    if (!current.signal.aborted)
      error.value = e instanceof Error ? e.message : "读取失败";
  } finally {
    if (controller === current) busy.value = false;
  }
}
</script>
<template>
  <section class="container-diagnostics">
    <div class="status-row">
      <h3>Docker 检查与诊断</h3>
      <el-tag
        :type="
          health?.status === 'healthy'
            ? 'success'
            : health?.status === 'unhealthy'
              ? 'danger'
              : health?.status === 'starting'
                ? 'warning'
                : 'info'
        "
        >{{ healthLabels[health?.status || "unknown"] }}</el-tag
      >
    </div>
    <p class="form-hint">
      {{ availabilityLabels[health?.availability || "unavailable"] }} ·
      <template v-if="health?.reason"
        >{{ monitoringReason(health.reason) }} · </template
      >最近采样
      {{ date(health?.sampled_at) }}
    </p>
    <div v-if="auth.can('containers:diagnose')" class="actions">
      <el-button size="small" @click="inspect('state')"
        >查看 Docker 状态</el-button
      ><el-button size="small" @click="inspect('logs')">查看近期日志</el-button>
    </div>
    <el-drawer
      v-model="open"
      :title="kind === 'logs' ? '近期容器日志' : 'Docker 状态'"
      size="min(800px, 90vw)"
      @closed="controller?.abort()"
    >
      <p class="form-hint">
        {{
          kind === "logs"
            ? "固定读取最近 15 分钟，最多 200 行"
            : "仅读取指定容器的 State"
        }}，输出最多 16 KiB，已执行脱敏。
      </p>
      <el-skeleton v-if="busy" :rows="5" animated />
      <el-alert
        v-if="error || result?.error_code"
        :title="error || '读取失败：' + result?.error_code"
        type="error"
        :closable="false"
      />
      <template v-if="result && !result.error_code"
        ><p class="form-hint">读取于 {{ date(result.collected_at) }}</p>
        <el-alert
          v-if="result.truncated"
          title="输出超过限制，结果已截断"
          type="warning"
          :closable="false"
        />
        <pre class="diagnostic-output">{{ result[kind] || "没有输出" }}</pre>
      </template>
    </el-drawer>
  </section>
</template>
<style scoped>
.diagnostic-output {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.7;
}
</style>
