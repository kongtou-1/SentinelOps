<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { all, api, query, type Models } from "@/api/client";
import { date, metricValue, metricUnit, number } from "@/utils/display";
import { metricLabels } from "@/config/metrics";
import { readPredictionRuns } from "@/features/predictions/api";
import { predictionConclusion } from "@/features/predictions/model/results";
import {
  predictionTimeline,
  type PredictionRun,
} from "@/features/predictions/model/timeline";
import StatusTag from "@/components/common/StatusTag.vue";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";
import { DataLine } from "@element-plus/icons-vue";
const props = defineProps<{ refreshKey: number; taskId?: string }>();
const emit = defineEmits<{ "task-change": [id: string] }>();
const selectedId = ref(""),
  tasks = ref<Models["TaskResponse"][]>([]),
  runs = ref<PredictionRun[]>([]),
  now = ref(Date.now() / 1000),
  loading = ref(false),
  horizon = ref("24"),
  history = ref<MetricData>(),
  error = ref(""),
  taskError = ref("");
const task = computed(() => tasks.value.find((t) => t.id === selectedId.value));
const config = computed(() => task.value);
const timeline = computed(() =>
  task.value
    ? predictionTimeline(
        runs.value,
        task.value,
        horizon.value,
        now.value - 86400,
        now.value,
      )
    : { latest: undefined, history: [], future: [] },
);
const selected = computed(() => timeline.value.latest);
const selectedHorizon = computed(() => selected.value?.horizons[horizon.value]);
const modelNames: Record<string, string> = {
  naive: "朴素预测",
  auto_ets: "AutoETS",
  seasonal_naive_daily: "日周期基线",
  seasonal_naive_weekly: "周周期基线",
  mstl_daily: "MSTL + AutoETS（日周期）",
  mstl_daily_weekly: "MSTL + AutoETS（日 / 周周期）",
};
let controller: AbortController | undefined,
  taskController: AbortController | undefined;
watch(
  () => props.refreshKey,
  async () => {
    taskController?.abort();
    taskController = new AbortController();
    const signal = taskController.signal;
    taskError.value = "";
    try {
      const next = await all<Models["TaskResponse"]>(
        "/prediction-tasks",
        signal,
      );
      if (signal.aborted) return;
      if (!next.some((t) => t.id === selectedId.value))
        selectedId.value =
          next.find((t) => t.id === props.taskId)?.id ||
          next.find((t) => t.latest_result?.status === "completed")?.id ||
          next[0]?.id ||
          "";
      tasks.value = next;
    } catch (e) {
      if (!signal.aborted)
        taskError.value = e instanceof Error ? e.message : "预测任务加载失败";
    }
  },
  { immediate: true },
);
watch(
  () => props.taskId,
  (id) => {
    selectedId.value =
      tasks.value.find((t) => t.id === id)?.id ||
      tasks.value.find((t) => t.latest_result?.status === "completed")?.id ||
      tasks.value[0]?.id ||
      "";
    if (selectedId.value) emit("task-change", selectedId.value);
  },
);
watch(selectedId, (id) => emit("task-change", id));
watch(task, async (current) => {
  controller?.abort();
  runs.value = [];
  history.value = undefined;
  error.value = "";
  loading.value = false;
  if (!current) return;
  controller = new AbortController();
  const signal = controller.signal,
    c = current,
    end = new Date(),
    start = new Date(Math.floor(end.getTime() / 300000) * 300000 - 86400000);
  now.value = end.getTime() / 1000;
  loading.value = true;
  const resource = c.container_id
    ? "containers/" + c.container_id
    : c.llm_endpoint_id
      ? "llm-endpoints/" + c.llm_endpoint_id
      : "servers/" + c.server_id;
  await Promise.all([
    (async () => {
      try {
        const next = await readPredictionRuns(
          c.id,
          start.getTime() / 1000,
          now.value,
          signal,
        );
        if (!signal.aborted) {
          runs.value = next;
          const latest = predictionTimeline(
            next,
            c,
            horizon.value,
            now.value - 86400,
            now.value,
          ).latest;
          if (!latest?.horizons[horizon.value]?.points?.length)
            horizon.value =
              ["24", "6", "1"].find(
                (h) => latest?.horizons[h]?.points?.length,
              ) || "24";
        }
      } catch (e) {
        if (!signal.aborted)
          error.value = e instanceof Error ? e.message : "预测历史加载失败";
      }
    })(),
    (async () => {
      try {
        const data = await api<MetricData>(
          "/" +
            resource +
            "/metrics" +
            query({
              metric: c.metric,
              start: start.toISOString(),
              end: end.toISOString(),
              step: 300,
            }),
          "GET",
          undefined,
          signal,
        );
        data.result = data.result?.filter((s) =>
          Object.entries(c.selector || {}).every(([k, v]) => s.metric[k] === v),
        );
        if (!signal.aborted) history.value = data;
      } catch (e) {
        if (!signal.aborted)
          error.value = e instanceof Error ? e.message : "历史数据不可用";
      }
    })(),
  ]);
  if (!signal.aborted) loading.value = false;
});
onUnmounted(() => {
  controller?.abort();
  taskController?.abort();
});
</script>
<template>
  <div class="panel-heading">
    <el-select
      v-model="selectedId"
      id="prediction-task"
      placeholder="选择预测任务"
      aria-label="选择预测任务"
      filterable
      style="width: min(100%, 420px)"
      ><el-option
        v-for="r in tasks"
        :key="r.id"
        :label="r.name + ' · ' + (metricLabels[r.metric] || r.metric)"
        :value="r.id"
    /></el-select>
  </div>
  <el-alert
    v-if="taskError"
    :title="taskError"
    type="error"
    :closable="false"
  />
  <div class="prediction-layout">
    <section v-loading="loading" class="panel">
      <div class="panel-heading">
        <div>
          <h2>
            {{ metricLabels[config?.metric || ""] || "资源指标预测"
            }}<span v-if="config?.name"> · {{ config.name }}</span>
          </h2>
        </div>
        <el-radio-group v-model="horizon"
          ><el-radio-button value="1">1h</el-radio-button
          ><el-radio-button value="6">6h</el-radio-button
          ><el-radio-button value="24">24h</el-radio-button></el-radio-group
        >
      </div>
      <dl v-if="selected" class="prediction-summary">
        <div>
          <dt>预测质量</dt>
          <dd>
            <StatusTag
              :value="selectedHorizon?.quality || 'insufficient_data'"
            />
          </dd>
        </div>
        <div>
          <dt>预测模型</dt>
          <dd>
            {{
              modelNames[selectedHorizon?.model || ""] ||
              selectedHorizon?.model ||
              "—"
            }}
          </dd>
        </div>
        <div>
          <dt>生成时间</dt>
          <dd>{{ date(selected.finished_at) }}</dd>
        </div>
        <div>
          <dt>数据截至</dt>
          <dd>{{ date(selected.data_end_at) }}</dd>
        </div>
      </dl>
      <el-alert
        v-if="error"
        :title="error"
        type="warning"
        :closable="false"
      /><MetricChart
        v-if="task"
        :data="history"
        :metric="config?.metric || ''"
        :historical-forecast="timeline.history"
        :forecast="timeline.future"
        :current-time="now"
        :threshold="config?.threshold"
      />
      <div v-else class="placeholder-state">
        <el-icon><DataLine /></el-icon><strong>尚无预测任务</strong
        ><span>创建预测任务，积累历史数据后运行预测</span
        ><router-link to="/prediction-tasks">管理预测任务 →</router-link>
      </div>
      <el-tag v-if="task && !loading && !timeline.future.length" type="info"
        >暂无未来预测</el-tag
      >
    </section>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>未来资源风险</h2>
        </div>
        <StatusTag
          v-if="selected"
          :value="selected.fresh ? 'ready' : 'stale'"
          :text="selected.fresh ? '时效内' : '已失效'"
        />
      </div>
      <div v-for="h in ['1', '6', '24']" :key="h" class="risk-item">
        <StatusTag
          :value="
            selected
              ? selected.fresh
                ? predictionConclusion(selected, h).value
                : 'stale'
              : 'unknown'
          "
          :text="
            selected
              ? selected.fresh
                ? predictionConclusion(selected, h).text
                : '结果已失效'
              : '暂无预测结果'
          "
        />
        <div>
          <strong>未来 {{ h }} 小时</strong>
          <div
            v-if="selected?.fresh && predictionConclusion(selected, h).breachAt"
          >
            {{ date(predictionConclusion(selected, h).breachAt) }}
          </div>
          <small
            v-if="selected?.fresh && selected.horizons?.[h]?.points?.length"
            style="display: block; margin-top: 6px"
            >预测末值
            {{
              number(
                metricValue(
                  config?.metric || "",
                  selected?.horizons?.[h]?.points?.at(-1)?.value,
                ),
              )
            }}
            {{ metricUnit(config?.metric || "") }}</small
          >
        </div>
      </div>
      <div v-if="selected" class="prediction-quality">
        <h2>数据质量</h2>
        <dl>
          <div>
            <dt>有效样本</dt>
            <dd>
              {{ number(selected.quality.observed_samples, 0) }} /
              {{ number(selected.quality.samples, 0) }}
            </dd>
          </div>
          <div>
            <dt>历史长度</dt>
            <dd>{{ number(selected.quality.history_hours, 1) }} 小时</dd>
          </div>
          <div>
            <dt>缺失比例</dt>
            <dd>
              {{
                number(
                  selected.quality.missing_fraction == null
                    ? null
                    : Number(selected.quality.missing_fraction) * 100,
                  2,
                )
              }}
              %
            </dd>
          </div>
        </dl>
      </div>
    </section>
  </div>
</template>
<style scoped>
.prediction-layout .panel-heading {
  flex-wrap: wrap;
}
.prediction-layout :deep(.chart) {
  height: 340px;
}
.prediction-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 0 0 20px;
}
.prediction-summary dt,
.prediction-quality dt {
  color: var(--muted);
  font-size: 13px;
}
.prediction-summary dd {
  margin: 8px 0 0;
  font-size: 14px;
}
.prediction-quality {
  margin-top: 24px;
}
.prediction-quality dl {
  margin-bottom: 0;
}
.prediction-quality dl > div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-top: 14px;
}
.prediction-quality dd {
  margin: 0;
  font-size: 14px;
}
@media (max-width: 1400px) {
  .prediction-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 1100px) {
  .prediction-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
