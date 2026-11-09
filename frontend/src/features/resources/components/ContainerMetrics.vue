<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { api, query } from "@/api/client";
import type { Row } from "@/types/records";
import { metricLabels } from "@/config/metrics";
import { number } from "@/utils/display";
import { monitoringReason, parseTimeWindow } from "@/utils/monitoring";
import { usePoll } from "@/composables/usePoll";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";
import StatusTag from "@/components/common/StatusTag.vue";

const props = defineProps<{
  id: string;
  container: Row;
  values: Record<string, { value: number | null; reason?: string }>;
  timeWindow?: { start: string; end: string };
}>();
const groups = [
  {
    label: "CPU",
    metrics: [
      "container_cpu_host_percent",
      "container_cpu_quota_percent",
      "container_cpu_percent",
    ],
  },
  {
    label: "内存",
    metrics: ["container_memory_bytes", "container_memory_limit_bytes"],
  },
  {
    label: "网络",
    metrics: [
      "container_network_receive_bytes",
      "container_network_transmit_bytes",
    ],
  },
  {
    label: "事件",
    metrics: [
      "container_restarts_5m",
      "container_oom_5m",
      "container_unhealthy",
      "container_up",
    ],
  },
];
const summaries = [
  { label: "CPU 占主机", metric: "container_cpu_host_percent" },
  { label: "CPU 配额使用率", metric: "container_cpu_quota_percent" },
  { label: "内存", metric: "container_memory_bytes" },
  { label: "重启 · 近 5 分钟", metric: "container_restarts_5m" },
  { label: "网络 RX", metric: "container_network_receive_bytes" },
  { label: "网络 TX", metric: "container_network_transmit_bytes" },
  { label: "OOM · 近 5 分钟", metric: "container_oom_5m" },
];
const active = ref(0),
  metric = ref("container_cpu_percent"),
  hours = ref(24),
  data = ref<MetricData | null>(null),
  error = ref(""),
  loading = ref(false),
  updated = ref("");
const options = computed(() => groups[active.value]!.metrics);
const pinned = ref(props.timeWindow);
watch(hours, () => {
  pinned.value = undefined;
});
const title = computed(
  () =>
    ({
      container_cpu_percent: "CPU 使用趋势",
      container_memory_bytes: "内存使用趋势",
      container_network_receive_bytes: "网络接收趋势",
      container_network_transmit_bytes: "网络发送趋势",
    })[metric.value] || metricLabels[metric.value],
);
function selectGroup(index: number, event?: KeyboardEvent) {
  active.value = index;
  metric.value = groups[index]!.metrics[0]!;
  if (event) {
    const tabs = (event.currentTarget as HTMLElement).parentElement;
    void nextTick(() =>
      tabs
        ?.querySelector<HTMLButtonElement>(`#container-metric-tab-${index}`)
        ?.focus(),
    );
  }
}
function summary(metric: string) {
  const value = props.values[metric]?.value;
  if (value === null || value === undefined) return "—";
  if (metric.includes("cpu")) return `${number(value)}%`;
  if (metric.endsWith("_5m")) return `${number(value, 0)} 次`;
  const bytes = metric === "container_memory_bytes" ? value * 1e9 : value;
  const unit =
    bytes >= 1e9 ? "GB" : bytes >= 1e6 ? "MB" : bytes >= 1e3 ? "KB" : "B";
  const divisor =
    unit === "GB" ? 1e9 : unit === "MB" ? 1e6 : unit === "KB" ? 1e3 : 1;
  return `${number(bytes / divisor)} ${unit}${metric.includes("network") ? "/s" : ""}`;
}
let controller: AbortController | undefined,
  version = 0;
async function load() {
  const current = ++version;
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const range = parseTimeWindow(pinned.value?.start, pinned.value?.end);
    const end = range ? Date.parse(range.end) : Date.now();
    const result = await api<MetricData>(
      `/containers/${props.id}/metrics` +
        query({
          metric: metric.value,
          start:
            range?.start || new Date(end - hours.value * 3600000).toISOString(),
          end: new Date(end).toISOString(),
          step: Math.max(30, Math.ceil((hours.value * 3600) / 2000 / 15) * 15),
        }),
      "GET",
      undefined,
      controller.signal,
    );
    if (current === version) {
      data.value = result;
      updated.value = new Date().toLocaleTimeString("zh-CN");
    }
  } catch (e) {
    if (
      current === version &&
      !(e instanceof DOMException && e.name === "AbortError")
    ) {
      error.value = e instanceof Error ? e.message : "监控查询失败";
      data.value = null;
    }
  } finally {
    if (current === version) loading.value = false;
  }
}
const { refresh } = usePoll(load, () => 30000);
watch(
  () => [props.id, metric.value, hours.value],
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
  <section class="panel container-metrics">
    <div class="panel-heading">
      <div>
        <h2>资源使用趋势</h2>
        <p class="container-name">{{ container.name }}</p>
      </div>
      <StatusTag :value="container.status" />
    </div>
    <dl class="metric-summary">
      <div v-for="item in summaries" :key="item.metric">
        <dt>{{ item.label }}</dt>
        <dd>{{ summary(item.metric) }}</dd>
        <small v-if="values[item.metric]?.reason">{{
          monitoringReason(values[item.metric]?.reason)
        }}</small>
      </div>
    </dl>
    <div class="metric-tabs" role="tablist" aria-label="容器资源分类">
      <button
        v-for="(group, index) in groups"
        :id="`container-metric-tab-${index}`"
        :key="group.label"
        type="button"
        role="tab"
        :aria-selected="active === index"
        :tabindex="active === index ? 0 : -1"
        aria-controls="container-metric-chart"
        :class="{ active: active === index }"
        @click="selectGroup(index)"
        @keydown.right.prevent="
          selectGroup((index + 1) % groups.length, $event)
        "
        @keydown.left.prevent="
          selectGroup((index + groups.length - 1) % groups.length, $event)
        "
      >
        {{ group.label }}
      </button>
    </div>
    <div
      id="container-metric-chart"
      role="tabpanel"
      :aria-labelledby="`container-metric-tab-${active}`"
    >
      <div class="panel-heading chart-heading">
        <h3>{{ title }}</h3>
        <el-radio-group v-model="hours" size="small" aria-label="历史时间范围">
          <el-radio-button :value="1">1h</el-radio-button>
          <el-radio-button :value="6">6h</el-radio-button>
          <el-radio-button :value="24">24h</el-radio-button>
          <el-radio-button :value="168">7天</el-radio-button>
        </el-radio-group>
      </div>
      <el-radio-group
        v-if="options.length > 1"
        v-model="metric"
        size="small"
        class="metric-options"
        aria-label="趋势指标"
      >
        <el-radio-button
          v-for="option in options"
          :key="option"
          :value="option"
          >{{ metricLabels[option] }}</el-radio-button
        >
      </el-radio-group>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div v-loading="loading">
        <MetricChart :data="data" :metric="metric" />
      </div>
    </div>
    <div class="trend-footer">
      <span>{{
        metric === "container_cpu_percent"
          ? "100% 代表占满 1 个 CPU 核心，多核累计可超过 100%。"
          : active === 3
            ? "重启和 OOM 为每个采样时刻之前 5 分钟的次数。"
            : "展示实际采集数据，缺采时段保留断点。"
      }}</span>
      <span>{{ updated ? "更新于 " + updated : "" }}</span>
    </div>
  </section>
</template>
<style scoped>
.container-metrics {
  min-width: 0;
  margin-bottom: 0;
}
.container-name {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.metric-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 24px 16px;
  margin: 26px 0;
}
.metric-summary dt {
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.metric-summary dd {
  margin: 0;
  font-size: 24px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.metric-summary small {
  color: var(--muted);
}
.metric-tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  border-radius: 12px;
  background: var(--surface-overlay);
}
.metric-tabs button {
  background: transparent;
  border: 0;
  border-radius: 9px;
  padding: 10px 8px;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
}
.metric-tabs button.active {
  color: var(--text);
  background: var(--surface);
  box-shadow: 0 1px 4px #00000012;
}
.metric-tabs button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
.chart-heading {
  margin: 24px 0 12px;
  flex-wrap: wrap;
  gap: 12px;
}
.chart-heading h3 {
  margin: 0;
  font-size: 16px;
}
.metric-options {
  display: flex;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.trend-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  margin-top: 12px;
}
@media (max-width: 1200px) {
  .metric-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .metric-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
