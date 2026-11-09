<script setup lang="ts">
export type PageMetric = {
  label: string;
  value: number | string | null | undefined;
  tone?: "success" | "warning" | "danger";
};
withDefaults(
  defineProps<{
    items: PageMetric[];
    ready?: boolean;
    loading?: boolean;
    error?: boolean;
  }>(),
  { ready: true },
);
</script>
<template>
  <dl
    class="page-metrics"
    :style="{ '--metric-count': items.length }"
    aria-label="本页关键指标"
    :aria-busy="loading || (!ready && !error)"
  >
    <div v-for="item in items" :key="item.label" :class="item.tone">
      <dt>{{ item.label }}</dt>
      <dd
        :class="{
          'metric-text': typeof item.value === 'string',
        }"
        :title="error ? '指标加载失败' : undefined"
      >
        {{
          ready &&
          !error &&
          item.value !== null &&
          item.value !== undefined &&
          (typeof item.value !== "number" || Number.isFinite(item.value))
            ? typeof item.value === "number"
              ? item.value.toLocaleString("zh-CN")
              : item.value
            : "—"
        }}
      </dd>
    </div>
  </dl>
</template>
<style scoped>
.page-metrics {
  display: grid;
  grid-template-columns: repeat(var(--metric-count), minmax(0, 1fr));
  gap: 12px;
  width: calc(var(--metric-count) * 168px + (var(--metric-count) - 1) * 12px);
  max-width: 100%;
  margin: 0;
  min-width: 0;
}
.page-metrics:empty {
  display: none;
}
.page-metrics > div {
  --metric-rgb: 77, 133, 255;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  min-height: 84px;
  padding: 12px 16px;
  border: 1px solid rgba(var(--metric-rgb), 0.32);
  border-radius: 12px;
  background: linear-gradient(
    115deg,
    rgba(var(--metric-rgb), 0.12),
    rgba(var(--metric-rgb), 0.035)
  );
}
dt {
  min-width: 0;
  font-size: 16px;
  font-weight: 500;
  line-height: 1.4;
  color: color-mix(in srgb, rgb(var(--metric-rgb)) 55%, var(--text));
  overflow-wrap: anywhere;
}
dd {
  max-width: 100%;
  margin: 0;
  font-size: 26px;
  font-weight: 650;
  line-height: 1.1;
  letter-spacing: -0.7px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.metric-text {
  font-size: 20px;
  letter-spacing: 0;
  white-space: normal;
  overflow-wrap: anywhere;
}
.page-metrics > .success {
  --metric-rgb: 49, 194, 155;
}
.page-metrics > .warning {
  --metric-rgb: 229, 174, 72;
}
.page-metrics > .danger {
  --metric-rgb: 239, 104, 128;
}
@media (max-width: 680px) {
  .page-metrics {
    gap: 10px;
  }
  .page-metrics > div {
    padding: 10px;
    min-height: 84px;
    gap: 6px;
  }
  .page-metrics:has(> div:nth-child(4)) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  dt {
    max-width: none;
    min-height: 2.8em;
    font-size: 16px;
  }
  dd {
    max-width: 100%;
    font-size: 26px;
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .metric-text {
    font-size: 20px;
  }
}
</style>
