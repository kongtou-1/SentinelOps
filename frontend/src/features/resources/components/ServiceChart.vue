<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { PieChart, GraphChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { theme } from "@/composables/useAppearance";
import type { Slice } from "@/features/resources/model/containerServices";
echarts.use([
  PieChart,
  GraphChart,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);
const props = defineProps<{
  slices?: Slice[];
  nodes?: { id: string; name: string }[];
  links?: { source: string; target: string }[];
  unit?: string;
  usedPercent?: number | null;
}>();
const host = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined, resize: ResizeObserver | undefined;
async function render() {
  await nextTick();
  if (!host.value) return;
  chart ||= echarts.init(host.value);
  const style = getComputedStyle(document.documentElement),
    text = style.getPropertyValue("--text").trim();
  chart.setOption(
    {
      color: ["#4c82ee", "#3dbf8f", "#f09a45", "#af78dd", "#f2557f", "#4ebbd5"],
      textStyle: { color: text },
      tooltip: {
        renderMode: "richText",
        trigger: "item",
        formatter: props.slices
          ? (p: any) =>
              `${p.name}\n${props.unit === "bytes" ? (p.value / 1024 ** 3).toFixed(2) + " GiB" : p.value.toFixed(2) + "%"} · ${p.percent}%`
          : undefined,
      },
      legend: props.slices
        ? { type: "scroll", bottom: 0, textStyle: { color: text } }
        : undefined,
      series: props.slices
        ? [
            {
              type: "pie",
              radius: ["42%", "67%"],
              center: ["50%", "43%"],
              label: { show: false },
              data: props.slices.map((s) => ({
                ...s,
                itemStyle:
                  s.name === "剩余资源"
                    ? { color: "#d0d5dd" }
                    : s.name.startsWith("系统") || s.name.startsWith("其他")
                      ? { color: "#8993a4" }
                      : undefined,
              })),
            },
          ]
        : [
            {
              type: "graph",
              layout: "circular",
              roam: true,
              symbolSize: 48,
              edgeSymbol: ["none", "arrow"],
              edgeSymbolSize: 9,
              label: {
                show: true,
                position: "bottom",
                color: text,
                width: 120,
                overflow: "truncate",
              },
              lineStyle: { width: 2, color: "#8993a4", curveness: 0.12 },
              data: props.nodes,
              links: props.links,
              emphasis: { focus: "adjacency" },
            },
          ],
    },
    true,
  );
}
onMounted(() => {
  void render();
  resize = new ResizeObserver(() => chart?.resize());
  if (host.value) resize.observe(host.value);
});
watch(
  () => [props.slices, props.nodes, props.links, theme.value],
  () => void render(),
  { deep: true },
);
onUnmounted(() => {
  resize?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div class="service-chart-wrapper">
    <div
      ref="host"
      class="service-chart"
      role="img"
      :aria-label="slices ? '服务器资源占用分布' : '服务依赖关系图'"
    ></div>
    <div v-if="slices && usedPercent != null" class="chart-center">
      <span>主机已用</span><strong>{{ usedPercent.toFixed(1) }}%</strong>
    </div>
  </div>
</template>
<style scoped>
.service-chart {
  height: 300px;
  width: 100%;
  min-width: 0;
}
.service-chart-wrapper {
  position: relative;
}
.chart-center {
  position: absolute;
  top: 43%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: grid;
  gap: 4px;
  text-align: center;
  pointer-events: none;
}
.chart-center span {
  color: var(--muted);
  font-size: 12px;
}
.chart-center strong {
  font-size: 22px;
}
</style>
