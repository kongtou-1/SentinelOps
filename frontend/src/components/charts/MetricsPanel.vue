<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { all, api, query } from "@/api/client";
import {
  containerMetrics,
  hostMetrics,
  llmMetrics,
  metricLabels,
} from "@/config/metrics";
import { type Row } from "@/types/records";
import { parseTimeWindow } from "@/utils/monitoring";
import { usePoll } from "@/composables/usePoll";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";
const props = defineProps<{
    kind: string;
    id?: string;
    timeWindow?: { start: string; end: string };
  }>(),
  resources = ref<Row[]>([]),
  selected = ref(props.id || ""),
  metric = ref(
    props.kind === "servers"
      ? "host_cpu_percent"
      : props.kind === "containers"
        ? "container_memory_bytes"
        : "llm_latency_p95_seconds",
  ),
  hours = ref(24),
  data = ref<MetricData | null>(null),
  error = ref(""),
  loading = ref(false),
  updated = ref("");
const pinned = ref(props.timeWindow);
watch(hours, () => {
  pinned.value = undefined;
});
const metrics = computed(() =>
  props.kind === "servers"
    ? hostMetrics
    : props.kind === "containers"
      ? containerMetrics
      : llmMetrics,
);
let controller: AbortController | undefined,
  version = 0;
async function load() {
  if (!selected.value) return;
  const n = ++version;
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const range = parseTimeWindow(pinned.value?.start, pinned.value?.end);
    const end = range ? Date.parse(range.end) / 1000 : Date.now() / 1000,
      start = range ? Date.parse(range.start) / 1000 : end - hours.value * 3600;
    const r = await api<MetricData>(
      `/${props.kind}/${selected.value}/metrics` +
        query({
          metric: metric.value,
          start: new Date(start * 1000).toISOString(),
          end: new Date(end * 1000).toISOString(),
          step: Math.max(30, Math.ceil((hours.value * 3600) / 2000 / 15) * 15),
        }),
      "GET",
      undefined,
      controller.signal,
    );
    if (n === version) {
      data.value = r;
      updated.value = new Date().toLocaleTimeString("zh-CN");
    }
  } catch (e) {
    if (
      n === version &&
      !(e instanceof DOMException && e.name === "AbortError")
    ) {
      error.value = e instanceof Error ? e.message : "监控查询失败";
      data.value = null;
    }
  } finally {
    if (n === version) loading.value = false;
  }
}
const { refresh } = usePoll(load, () => (selected.value ? 30000 : null));
onMounted(async () => {
  if (!props.id) {
    try {
      resources.value = await all<Row>("/" + props.kind);
      selected.value =
        resources.value.find((r) => r.monitoring_enabled !== false)?.id || "";
    } catch (e) {
      error.value = e instanceof Error ? e.message : "资源加载失败";
    }
  }
});
watch(
  () => [selected.value, metric.value, hours.value],
  () => {
    version++;
    controller?.abort();
    data.value = null;
    void refresh();
  },
);
onUnmounted(() => {
  version++;
  controller?.abort();
});
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h2>资源使用趋势</h2>
      <span class="row-sub">{{ updated ? "更新于 " + updated : "" }}</span>
    </div>
    <div class="filter-bar">
      <el-select v-if="!id" v-model="selected" placeholder="选择资源" filterable
        ><el-option
          v-for="r in resources"
          :key="r.id"
          :label="r.name"
          :value="r.id" /></el-select
      ><el-select
        v-model="metric"
        filterable
        placeholder="指标"
        style="width: 240px"
        ><el-option
          v-for="m in metrics"
          :key="m"
          :label="metricLabels[m] || m"
          :value="m" /></el-select
      ><el-radio-group v-model="hours"
        ><el-radio-button :value="1">1h</el-radio-button
        ><el-radio-button :value="6">6h</el-radio-button
        ><el-radio-button :value="24">24h</el-radio-button
        ><el-radio-button :value="168">7天</el-radio-button></el-radio-group
      >
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-loading="loading"><MetricChart :data="data" :metric="metric" /></div>
    <div v-if="kind === 'llm-endpoints'" class="form-hint">
      成功率、分类错误率和延迟分位数为每个采样时刻之前 5 分钟的滑动窗口指标。
    </div>
    <div v-if="kind === 'containers'" class="form-hint">
      CPU 按核心累计，可以超过 100%；重启和 OOM 指标表示最近 5 分钟次数。
    </div>
  </section>
</template>
