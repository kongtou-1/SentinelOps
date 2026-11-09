<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { AskDataResult } from "@/features/askdata/api";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const props = defineProps<{ result: AskDataResult }>();
const host = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;
let resize: ResizeObserver | undefined;

const spec = computed(() => props.result.chart);
const visible = computed(
  () => spec.value?.kind === "bar" || spec.value?.kind === "line",
);
const columns = computed(() => props.result.columns ?? []);
const rows = computed(() => (props.result.rows ?? []).slice(0, 50));

function columnIndex(name: string | null | undefined) {
  if (!name) return -1;
  return columns.value.findIndex((column) => column.name === name);
}
function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function themeColor(name: string, fallback: string) {
  return (
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() ||
    fallback
  );
}

async function render() {
  await nextTick();
  if (!host.value || !visible.value) return;
  chart ||= echarts.init(host.value);
  const kind = spec.value?.kind === "line" ? "line" : "bar";
  const xIndex = columnIndex(spec.value?.x);
  const data = rows.value;
  const categories =
    xIndex >= 0
      ? data.map((row) => {
          const value = row[xIndex];
          return value === null || value === undefined ? "" : String(value);
        })
      : data.map((_, index) => String(index + 1));
  const series = (spec.value?.y ?? [])
    .map((name) => ({ name, index: columnIndex(name) }))
    .filter((item) => item.index >= 0)
    .map((item) => ({
      name: item.name,
      type: kind,
      data: data.map((row) => numeric(row[item.index])),
      smooth: kind === "line",
      barMaxWidth: 36,
      connectNulls: true,
    }));
  const text = themeColor("--text", "#e6e9ef");
  const muted = themeColor("--muted", "#8993a4");
  chart.setOption(
    {
      textStyle: { color: text },
      tooltip: { trigger: "axis" },
      legend: { show: series.length > 1, top: 0, textStyle: { color: text } },
      grid: {
        left: 16,
        right: 24,
        top: series.length > 1 ? 36 : 16,
        bottom: 16,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: categories,
        axisLabel: {
          color: muted,
          rotate: categories.some((item) => item.length > 10) ? 28 : 0,
        },
        axisLine: { lineStyle: { color: "rgba(137,147,164,0.35)" } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: "rgba(137,147,164,0.15)" } },
      },
      series,
    } as echarts.EChartsCoreOption,
    true,
  );
}

onMounted(() => {
  void render();
  resize = new ResizeObserver(() => chart?.resize());
  if (host.value) resize.observe(host.value);
});
watch(
  () => props.result,
  () => void render(),
  { deep: true },
);
onUnmounted(() => {
  resize?.disconnect();
  chart?.dispose();
});
</script>

<template>
  <div v-if="visible" class="result-chart">
    <p v-if="spec?.title" class="chart-title">{{ spec.title }}</p>
    <div ref="host" class="chart-host" role="img" aria-label="查询结果图表"></div>
  </div>
</template>

<style scoped>
.result-chart {
  display: grid;
  gap: 8px;
}
.chart-title {
  margin: 0;
  font-size: 13px;
  color: var(--muted, #8993a4);
}
.chart-host {
  width: 100%;
  height: 280px;
}
</style>
