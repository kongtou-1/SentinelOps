<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { theme } from "@/composables/useAppearance";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Row } from "@/types/records";
echarts.use([
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);
const props = defineProps<{ rows: Row[] }>(),
  host = ref<HTMLDivElement>();
const days = computed(() =>
  Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - 6 + i);
    return d;
  }),
);
let chart: echarts.ECharts | undefined, observer: ResizeObserver | undefined;
async function render() {
  await nextTick();
  if (!host.value) return;
  const style = getComputedStyle(document.documentElement);
  const muted = style.getPropertyValue("--muted").trim();
  const border = style.getPropertyValue("--border").trim();
  const text = style.getPropertyValue("--text").trim();
  const surface = style.getPropertyValue("--surface-overlay").trim();
  chart ||= echarts.init(host.value);
  chart.setOption(
    {
      color: ["#f2557f", "#f09a45", "#4c82ee"],
      textStyle: { fontFamily: "Noto Sans SC, Segoe UI, sans-serif" },
      tooltip: {
        trigger: "axis",
        renderMode: "richText",
        backgroundColor: surface,
        borderColor: border,
        textStyle: { color: text },
      },
      legend: {
        bottom: 0,
        textStyle: { color: muted, fontSize: 11 },
        itemWidth: 8,
        itemHeight: 8,
      },
      grid: { left: 8, right: 8, top: 12, bottom: 50, containLabel: true },
      xAxis: {
        type: "category",
        data: days.value.map((d) => `${d.getMonth() + 1}/${d.getDate()}`),
        axisLine: { lineStyle: { color: border } },
        axisLabel: { color: muted, fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: muted, fontSize: 11 },
        splitLine: { lineStyle: { color: border } },
      },
      series: ["critical", "warning", "other"].map((severity, i) => ({
        name: ["严重", "警告", "其他"][i],
        type: "bar",
        stack: "alerts",
        barMaxWidth: 35,
        data: days.value.map(
          (d) =>
            props.rows.filter((r) => {
              const t = new Date(r.created_at).getTime();
              const end = new Date(d);
              end.setDate(end.getDate() + 1);
              return (
                t >= d.getTime() &&
                t < end.getTime() &&
                (severity === "other"
                  ? !["critical", "warning"].includes(r.severity)
                  : r.severity === severity)
              );
            }).length,
        ),
      })),
    },
    true,
  );
}
onMounted(() => {
  void render();
  observer = new ResizeObserver(() => chart?.resize());
  if (host.value) observer.observe(host.value);
});
watch(
  () => [props.rows, theme.value],
  () => void render(),
  { deep: true },
);
onUnmounted(() => {
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div
    ref="host"
    class="chart"
    role="img"
    :aria-label="
      '近七天新增告警共 ' + rows.length + ' 条，按严重、警告和其他等级分布'
    "
  />
</template>
