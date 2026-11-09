<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, query } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { metricLabels } from "@/config/metrics";
import { metricValue, metricUnit, number } from "@/utils/display";
import type { Row } from "@/types/records";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";

const props = defineProps<{
  serverId: string;
  timeWindow?: { start: string; end: string };
  inventory?: Row;
}>();
const groups = [
  {
    label: "文件系统",
    keys: ["device", "mountpoint", "fstype"],
    metrics: [
      "host_disk_percent",
      "host_disk_available_bytes",
      "host_inode_percent",
    ],
  },
  {
    label: "磁盘 I/O",
    keys: ["device"],
    metrics: [
      "host_disk_read_bytes",
      "host_disk_write_bytes",
      "host_disk_io_wait_seconds",
    ],
  },
  {
    label: "网卡",
    keys: ["device"],
    metrics: [
      "host_network_receive_bytes",
      "host_network_transmit_bytes",
      "host_network_receive_errors",
      "host_network_transmit_errors",
      "host_network_receive_drops",
      "host_network_transmit_drops",
    ],
  },
];
const tab = ref(0),
  selected = ref<Row>(),
  values = ref<Record<string, MetricData>>({}),
  errors = ref<string[]>([]),
  data = ref<MetricData>(),
  metric = ref("host_disk_percent");
const group = computed(() => groups[tab.value]!);
const chartRange = ref<{ start: string; end: string }>();
const mountInventoryKnown = computed(() =>
  (props.inventory?.observations || []).some((o: Row) =>
    Array.isArray(o.mounts),
  ),
);
const affected = computed(() => {
  const mountpoint = selected.value?.mountpoint;
  if (tab.value !== 0 || typeof mountpoint !== "string") return [];
  const prefix = mountpoint === "/" ? "/" : mountpoint.replace(/\/$/, "") + "/";
  const ids = new Set(
    (props.inventory?.observations || [])
      .filter((o: Row) =>
        o.mounts?.some(
          (m: Row) =>
            typeof m.source === "string" &&
            (m.source === mountpoint || m.source.startsWith(prefix)),
        ),
      )
      .map((o: Row) => o.container_id),
  );
  return (props.inventory?.containers || []).filter((c: Row) => ids.has(c.id));
});
const rows = computed(() => {
  const items = new Map<string, Row>();
  for (const name of group.value.metrics)
    for (const series of values.value[name]?.result || []) {
      const labels = Object.fromEntries(
        group.value.keys.map((k) => [k, series.metric[k] || ""]),
      );
      const key = JSON.stringify(labels);
      const row = items.get(key) || { key, ...labels };
      row[name] = metricValue(name, series.value?.[1]);
      items.set(key, row);
    }
  return [...items.values()];
});
let generation = 0;
async function load() {
  const version = ++generation;
  const results = await Promise.allSettled(
    group.value.metrics.map((name) =>
      api<MetricData>(
        `/servers/${props.serverId}/metrics` + query({ metric: name }),
      ),
    ),
  );
  if (version !== generation) return;
  values.value = {};
  errors.value = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled")
      values.value[group.value.metrics[index]!] = result.value;
    else
      errors.value.push(
        result.reason instanceof Error ? result.reason.message : "采样查询失败",
      );
  });
  if (selected.value)
    selected.value = rows.value.find((row) => row.key === selected.value?.key);
  if (!selected.value) selected.value = rows.value[0];
}
const { refresh } = usePoll(load, () => 30000);
let chartGeneration = 0;
async function chart() {
  const version = ++chartGeneration;
  if (!selected.value) {
    data.value = undefined;
    return;
  }
  const end = new Date(),
    range = props.timeWindow || {
      start: new Date(end.getTime() - 86400000).toISOString(),
      end: end.toISOString(),
    };
  chartRange.value = range;
  try {
    const result = await api<MetricData>(
      `/servers/${props.serverId}/metrics` +
        query({
          metric: metric.value,
          ...range,
          step: Math.max(
            30,
            Math.ceil(
              (Date.parse(range.end) - Date.parse(range.start)) /
                1000 /
                1500 /
                15,
            ) * 15,
          ),
        }),
    );
    if (version !== chartGeneration) return;
    data.value = {
      ...result,
      result: result.result?.filter((series) =>
        group.value.keys.every(
          (k) => (series.metric[k] || "") === selected.value?.[k],
        ),
      ),
    };
  } catch (e) {
    if (version === chartGeneration) {
      data.value = undefined;
      errors.value = [e instanceof Error ? e.message : "历史查询失败"];
    }
  }
}
watch(tab, () => {
  selected.value = undefined;
  values.value = {};
  metric.value = group.value.metrics[0]!;
  void refresh();
});
watch(
  () => [
    selected.value?.key,
    metric.value,
    props.timeWindow?.start,
    props.timeWindow?.end,
  ],
  () => void chart(),
);
function format(name: string, value: unknown) {
  if (value == null) return "—";
  if (name === "host_disk_available_bytes")
    return number(Number(value) / 1024 ** 3) + " GiB";
  return number(Number(value)) + " " + metricUnit(name);
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h2>磁盘与网络</h2>
      <el-button
        @click="
          refresh();
          chart();
        "
        >刷新</el-button
      >
    </div>
    <el-radio-group v-model="tab"
      ><el-radio-button
        v-for="(item, index) in groups"
        :key="item.label"
        :value="index"
        >{{ item.label }}</el-radio-button
      ></el-radio-group
    >
    <el-alert
      v-for="error in errors"
      :key="error"
      :title="error"
      type="error"
      :closable="false"
    />
    <el-table
      :data="rows"
      highlight-current-row
      @row-click="(row: Row) => (selected = row)"
    >
      <el-table-column
        v-for="key in group.keys"
        :key="key"
        :prop="key"
        :label="
          (
            {
              device: '设备',
              mountpoint: '挂载点',
              fstype: '文件系统',
            } as Record<string, string>
          )[key]
        "
        min-width="130"
        show-overflow-tooltip
      />
      <el-table-column
        v-for="name in group.metrics"
        :key="name"
        :label="metricLabels[name]"
        min-width="130"
        ><template #default="{ row }">{{
          format(name, row[name])
        }}</template></el-table-column
      >
    </el-table>
    <template v-if="selected"
      ><div class="panel-heading selected-entity">
        <h3>{{ selected.mountpoint || selected.device }}</h3>
        <el-select
          v-model="metric"
          aria-label="对象监测指标"
          style="width: 230px"
          ><el-option
            v-for="name in group.metrics"
            :key="name"
            :value="name"
            :label="metricLabels[name]"
        /></el-select>
      </div>
      <MetricChart :data="data" :metric="metric" /><template v-if="tab === 0"
        ><h3>关联容器</h3>
        <el-tag v-if="!mountInventoryKnown" type="warning">挂载采集未知</el-tag
        ><el-tag v-else-if="!affected.length" type="info">无绑定挂载</el-tag>
        <div
          v-for="container in affected"
          :key="container.id"
          class="affected-container"
        >
          <router-link
            :to="{ path: '/containers/' + container.id, query: chartRange }"
            >{{ container.name }}</router-link
          ><router-link
            v-if="container.application_id"
            :to="{
              path: '/applications/' + container.application_id,
              query: chartRange,
            }"
            >服务监测 →</router-link
          >
        </div></template
      ></template
    >
  </section>
</template>
<style scoped>
.selected-entity {
  margin-top: 20px;
}
.el-radio-group {
  margin-bottom: 16px;
}
</style>
