<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { chartPoints, monitoringReason } from "@/utils/monitoring";
import { theme } from "@/composables/useAppearance";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  MarkAreaComponent,
  MarkPointComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { date, number, metricValue, metricUnit } from "@/utils/display";
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  MarkAreaComponent,
  MarkPointComponent,
  DataZoomComponent,
  CanvasRenderer,
]);
export type MetricData = {
  resultType?: string;
  step?: number;
  result?: {
    metric: Record<string, string>;
    values?: [number, string | null][];
    value?: [number, string | null];
  }[];
  availability?: string;
  reason?: string;
  status?: string;
};
const props = defineProps<{
  data?: MetricData | null;
  metric: string;
  color?: string;
  compact?: boolean;
  forecast?: {
    timestamp: number;
    value: number;
    lower?: number;
    upper?: number;
  }[];
  historicalForecast?: {
    timestamp: number;
    value: number;
    issuedAt?: number;
  }[];
  currentTime?: number;
  threshold?: number;
  anomalies?: { timestamp: number; value: number }[];
}>();
const choices = computed(() =>
  (props.data?.result || []).map((s, i) => ({
    key: JSON.stringify(Object.entries(s.metric).sort()),
    label:
      Object.entries(s.metric)
        .filter(
          ([k]) =>
            ![
              "__name__",
              "job",
              "server_id",
              "container_id",
              "endpoint_id",
              "source_epoch",
            ].includes(k),
        )
        .map(([, v]) => v)
        .join(" / ") || `实测 ${i + 1}`,
    series: s,
  })),
);
const selected = ref<string[]>([]);
watch(
  () => [props.metric, ...choices.value.map((c) => c.key)].join("|"),
  () => {
    const retained = selected.value.filter((k) =>
      choices.value.some((c) => c.key === k),
    );
    selected.value = retained.length
      ? retained
      : choices.value.slice(0, 6).map((c) => c.key);
  },
  { immediate: true },
);
const host = ref<HTMLDivElement>(),
  message = computed(() =>
    props.data?.availability === "paused"
      ? "监控已暂停"
      : props.data?.availability === "unconfigured"
        ? "此指标尚未配置采集"
        : props.data?.availability === "unavailable"
          ? "没有有效采样数据"
          : !props.data?.result?.some((s) => s.values?.length || s.value)
            ? "暂无监控数据"
            : null,
  );
let chart: echarts.ECharts | undefined, resize: ResizeObserver | undefined;
async function render() {
  await nextTick();
  if (!host.value) return;
  const style = getComputedStyle(document.documentElement);
  const muted = style.getPropertyValue("--muted").trim();
  const border = style.getPropertyValue("--border").trim();
  const text = style.getPropertyValue("--text").trim();
  const surface = style.getPropertyValue("--surface-overlay").trim();
  if (!chart) chart = echarts.init(host.value);
  const series: any[] = choices.value
    .filter((c) => selected.value.includes(c.key))
    .map((c, i) => ({
      type: "line",
      name:
        props.currentTime !== undefined && choices.value.length === 1
          ? "历史实测"
          : c.label,
      showSymbol: false,
      connectNulls: false,
      data: chartPoints(
        c.series.values ||
          ([c.series.value].filter(Boolean) as [number, string | null][]),
        props.metric,
        props.data?.step,
      ),
      lineStyle: { width: 2 },
      areaStyle: { opacity: 0.1 },
      markPoint:
        i === 0 && props.anomalies?.length
          ? {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color: "#d92d20" },
              label: { show: false },
              data: props.anomalies.map((p) => ({
                name: "残差异常",
                coord: [p.timestamp * 1000, metricValue(props.metric, p.value)],
              })),
            }
          : undefined,
      markLine:
        props.threshold === undefined
          ? undefined
          : {
              symbol: "none",
              data: [
                {
                  yAxis: metricValue(props.metric, props.threshold),
                  name: "阈值",
                },
              ],
              label: { formatter: "阈值", fontSize: 14 },
              lineStyle: { color: "#ef8b27", type: "dashed" },
            },
    }));
  if (props.historicalForecast?.length) {
    series.push({
      name: "历史预测",
      type: "line",
      showSymbol: false,
      connectNulls: false,
      lineStyle: { type: "dashed", width: 2, color: "#9b7de3" },
      itemStyle: { color: "#9b7de3" },
      data: chartPoints(
        props.historicalForecast.map((p) => [p.timestamp, p.value]),
        props.metric,
        300,
      ),
    });
  }
  if (props.forecast?.length) {
    const f = props.forecast;
    series.push({
      name: props.currentTime === undefined ? "预测" : "未来预测",
      type: "line",
      showSymbol: false,
      lineStyle: { type: "dashed", width: 2, color: "#f09a45" },
      itemStyle: { color: "#f09a45" },
      data: f.map((p) => [
        p.timestamp * 1000,
        metricValue(props.metric, p.value),
      ]),
    });
    if (
      f.every(
        (p) =>
          metricValue(props.metric, p.lower) !== null &&
          metricValue(props.metric, p.upper) !== null,
      )
    ) {
      series.push(
        {
          name: "区间下界",
          type: "line",
          showSymbol: false,
          lineStyle: { opacity: 0 },
          data: f.map((p) => [
            p.timestamp * 1000,
            metricValue(props.metric, p.lower),
          ]),
          stack: "interval",
          stackStrategy: "all",
          areaStyle: { opacity: 0 },
        },
        {
          name: "预测区间",
          type: "line",
          showSymbol: false,
          lineStyle: { opacity: 0 },
          data: f.map((p) => [
            p.timestamp * 1000,
            metricValue(props.metric, p.upper)! -
              metricValue(props.metric, p.lower)!,
          ]),
          stack: "interval",
          stackStrategy: "all",
          areaStyle: { color: "#4c82ee", opacity: 0.12 },
        },
      );
    }
  }
  if (props.currentTime !== undefined) {
    series.push({
      type: "line",
      data: [],
      markLine: {
        symbol: "none",
        silent: true,
        lineStyle: { color: muted, type: "dashed" },
        label: { formatter: "当前时间", position: "insideEndTop", rotate: 0 },
        data: [{ xAxis: props.currentTime * 1000 }],
      },
    });
  }
  chart.setOption(
    {
      color: [props.color || "#4c82ee", "#3dbf8f", "#f09a45", "#f2557f"],
      textStyle: {
        fontSize: 12,
        color: muted,
        fontFamily: "Noto Sans SC, Segoe UI, sans-serif",
      },
      tooltip: {
        trigger: "axis",
        renderMode: "richText",
        backgroundColor: surface,
        borderColor: border,
        textStyle: { fontSize: 14, color: text },
        formatter: (params: any[]) => {
          const time = Number(params[0]?.axisValue);
          const lines = params
            .filter((p) => !["区间下界", "预测区间"].includes(p.seriesName))
            .map(
              (p) =>
                `${p.seriesName}: ${number(p.value?.[1], 3)} ${metricUnit(props.metric)}`,
            );
          const p = props.forecast?.find((p) => p.timestamp * 1000 === time);
          const historical = props.historicalForecast?.find(
            (p) => p.timestamp * 1000 === time,
          );
          if (historical?.issuedAt)
            lines.push(`预测生成时间: ${date(historical.issuedAt)}`);
          if (
            p &&
            metricValue(props.metric, p.lower) !== null &&
            metricValue(props.metric, p.upper) !== null
          )
            lines.push(
              `95% 区间: ${number(metricValue(props.metric, p.lower), 3)} – ${number(metricValue(props.metric, p.upper), 3)} ${metricUnit(props.metric)}`,
            );
          return [date(time / 1000), ...lines].join("\n");
        },
      },
      legend: {
        show: !props.compact,
        type: "scroll",
        data:
          props.currentTime === undefined
            ? undefined
            : series
                .filter(
                  (s) => s.name && !["区间下界", "预测区间"].includes(s.name),
                )
                .map((s) => s.name),
        bottom: 0,
        textStyle: { fontSize: 12, color: muted },
      },
      grid: {
        left: props.compact ? 8 : 35,
        right: 15,
        top: 28,
        bottom: props.compact ? 25 : props.currentTime === undefined ? 55 : 90,
        containLabel: true,
      },
      dataZoom:
        props.currentTime === undefined
          ? []
          : [
              { type: "inside", filterMode: "none" },
              {
                type: "slider",
                bottom: 28,
                height: 20,
                filterMode: "none",
                borderColor: border,
                textStyle: { color: muted },
              },
            ],
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: border } },
        axisLabel: { color: muted, fontSize: props.compact ? 10 : 12 },
      },
      yAxis: {
        type: "value",
        name: metricUnit(props.metric),
        nameTextStyle: { fontSize: 14 },
        axisLabel: { color: muted, fontSize: props.compact ? 10 : 12 },
        splitLine: { lineStyle: { color: border, type: "dashed" } },
      },
      series,
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
  () => [
    theme.value,
    selected.value,
    props.data,
    props.forecast,
    props.historicalForecast,
    props.currentTime,
    props.metric,
    props.threshold,
    props.anomalies,
  ],
  () => void render(),
  { deep: true },
);
onUnmounted(() => {
  resize?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <el-select
    v-if="choices.length > 1 && !compact"
    v-model="selected"
    multiple
    collapse-tags
    collapse-tags-tooltip
    placeholder="选择磁盘、网卡或容器序列"
    aria-label="选择监控序列"
    style="width: 100%; margin: 8px 0"
  >
    <el-option
      v-for="c in choices"
      :key="c.key"
      :label="c.label"
      :value="c.key"
    />
  </el-select>
  <div v-if="choices.length > 6" class="form-hint">
    共 {{ choices.length }} 条序列，当前显示
    {{ selected.length }} 条；默认选择前 6 条。
  </div>
  <div v-if="data?.availability === 'stale'" class="form-hint">
    {{ monitoringReason(data.reason) }}
  </div>
  <div
    v-if="message && !forecast?.length && !historicalForecast?.length"
    class="empty-hint"
  >
    {{ message }}
    <div v-if="data?.reason" class="form-hint">
      {{ monitoringReason(data.reason) }}
    </div>
  </div>
  <div
    ref="host"
    class="chart"
    :style="
      message && !forecast?.length && !historicalForecast?.length
        ? { height: '1px', visibility: 'hidden' }
        : {}
    "
  ></div>
</template>
