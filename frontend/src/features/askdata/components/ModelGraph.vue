<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { AskDataRelationship } from "@/features/askdata/api";

echarts.use([GraphChart, TooltipComponent, CanvasRenderer]);

const props = defineProps<{ relationships: AskDataRelationship[] }>();
const host = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;
let resize: ResizeObserver | undefined;

const nodes = computed(() => {
  const unique = new Map<string, { id: string; name: string; value: number }>();
  for (const item of props.relationships) {
    for (const key of [
      `${item.source_schema}.${item.source_table}`,
      `${item.target_schema}.${item.target_table}`,
    ]) {
      const entry = unique.get(key) || { id: key, name: key, value: 0 };
      entry.value += 1;
      unique.set(key, entry);
    }
  }
  return [...unique.values()];
});
type GraphLink = {
  source: string;
  target: string;
  value: string;
  lineStyle: { type: "solid" | "dashed" };
};

const links = computed<GraphLink[]>(() =>
  props.relationships.map((item) => ({
    source: `${item.source_schema}.${item.source_table}`,
    target: `${item.target_schema}.${item.target_table}`,
    value: `${item.source_column} → ${item.target_column}`,
    lineStyle: { type: item.kind === "semantic" ? "dashed" : "solid" },
  })),
);

async function render() {
  await nextTick();
  if (!host.value || !props.relationships.length) return;
  chart ||= echarts.init(host.value);
  const text = getComputedStyle(document.documentElement)
    .getPropertyValue("--text")
    .trim();
  chart.setOption(
    {
      textStyle: { color: text },
      tooltip: {
        trigger: "item",
        formatter: (params: any) =>
          params.dataType === "edge"
            ? String(params.data?.value ?? "")
            : String(params.data && "name" in params.data ? params.data.name : ""),
      },
      series: [
        {
          type: "graph",
          layout: "circular",
          roam: true,
          symbolSize: (value: number) => Math.min(28 + value * 3, 60),
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 8,
          label: { show: true, position: "right", color: text, width: 140, overflow: "truncate" },
          lineStyle: { width: 1.6, color: "#8993a4", curveness: 0.12 },
          data: nodes.value,
          links: links.value,
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
watch(() => props.relationships, () => void render(), { deep: true });
onUnmounted(() => {
  resize?.disconnect();
  chart?.dispose();
});
</script>

<template>
  <div v-if="relationships.length" ref="host" class="model-graph" role="img" aria-label="业务模型关系图谱"></div>
  <el-empty v-else description="当前版本没有关系数据" :image-size="60" />
</template>

<style scoped>
.model-graph {
  width: 100%;
  height: 460px;
}
</style>
