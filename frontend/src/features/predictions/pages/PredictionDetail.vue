<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import PredictionOutcomes from "@/features/operations/components/PredictionOutcomes.vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { api, query, type Models } from "@/api/client";
import {
  activeTask,
  date,
  label,
  metricUnit,
  metricValue,
  number,
  short,
} from "@/utils/display";
import { metricLabels } from "@/config/metrics";
import { type Row } from "@/types/records";
import { usePoll } from "@/composables/usePoll";
import {
  predictionConclusion,
  predictionDuration,
  predictionSummary,
} from "@/features/predictions/model/results";
import StatusTag from "@/components/common/StatusTag.vue";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";
const id = String(useRoute().params.id),
  row = ref<Models["ResultResponse"]>(),
  error = ref(""),
  history = ref<MetricData>(),
  chartError = ref(""),
  horizon = ref("24"),
  attempt = ref(0);
const gapPage = ref(1),
  anomalyPage = ref(1);
const config = computed(() => (row.value?.config || {}) as Row),
  quality = computed(() => (row.value?.quality || {}) as Row),
  summary = computed(() =>
    row.value ? predictionSummary(row.value) : undefined,
  ),
  gaps = computed<Row[]>(() =>
    [...(quality.value.excluded_gaps || [])].sort((a, b) => b.start - a.start),
  ),
  anomalies = computed<Row[]>(() =>
    [...((row.value?.anomalies || []) as Row[])].sort(
      (a, b) => b.timestamp - a.timestamp,
    ),
  ),
  visibleGaps = computed(() =>
    gaps.value.slice((gapPage.value - 1) * 5, gapPage.value * 5),
  ),
  visibleAnomalies = computed(() =>
    anomalies.value.slice((anomalyPage.value - 1) * 10, anomalyPage.value * 10),
  ),
  selected = computed(
    () => (row.value?.horizons?.[horizon.value] || {}) as Row,
  );
const qualityReasons: Record<string, string> = {
  no_samples: "尚无有效采样数据",
  latest_bin_missing: "最近一个采样时段缺少数据",
  too_many_missing_samples: "缺失采样比例超过 10%",
  probe_interval_too_large: "探测间隔超过 5 分钟",
};
async function load() {
  try {
    row.value = await api("/predictions/" + id);
    error.value = "";
    attempt.value++;
    if (
      row.value?.status === "completed" &&
      !history.value &&
      row.value.data_end_at
    ) {
      const c = config.value,
        end = new Date(row.value.data_end_at),
        start = new Date(end.getTime() - 86400000);
      const path = c.container_id
        ? "containers/" + c.container_id
        : c.llm_endpoint_id
          ? "llm-endpoints/" + c.llm_endpoint_id
          : "servers/" + c.server_id;
      try {
        const data = await api<MetricData>(
          "/" +
            path +
            "/metrics" +
            query({
              metric: c.metric,
              start: start.toISOString(),
              end: end.toISOString(),
              step: 300,
            }),
        );
        data.result = data.result?.filter((s) =>
          Object.entries(c.selector || {}).every(([k, v]) => s.metric[k] === v),
        );
        history.value = data;
        chartError.value = "";
      } catch (e) {
        chartError.value = e instanceof Error ? e.message : "历史数据不可用";
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  row.value && activeTask(row.value.status)
    ? Math.min(10000, 2000 * Math.max(1, attempt.value))
    : null,
);
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/predictions" class="subtle-link"
        >← 返回预测列表</router-link
      >
      <div class="heading-copy">
        <h1>{{ config.name || "预测结果" }}</h1>
        <PageIntro />
        <p>
          {{ metricLabels[config.metric] || config.metric }} · 结果
          {{ short(id) }} · 数据截至 {{ date(row?.data_end_at) }}
        </p>
      </div>
    </div>

    <div class="actions">
      <el-button @click="refresh">刷新</el-button>
      <router-link v-if="row" :to="'/prediction-tasks/' + row.task_id"
        ><el-button>任务详情</el-button></router-link
      >
    </div>
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <section v-if="row && summary" class="panel prediction-verdict">
    <div class="panel-heading">
      <h2>预测结论</h2>
      <div class="verdict-states">
        <StatusTag
          :value="row.status"
          :text="row.status === 'completed' ? '运行结束' : label(row.status)"
        />
        <StatusTag
          v-if="row.status === 'completed'"
          :value="row.fresh ? 'ready' : 'stale'"
          :text="row.fresh ? '时效内' : '历史结果'"
        />
      </div>
    </div>
    <div class="verdict-title">
      <StatusTag :value="summary.value" :text="summary.text" />
    </div>
    <el-alert
      v-if="row.status === 'completed' && !row.fresh"
      title="该结果已过期或资源配置发生变化，不能作为当前风险结论。"
      type="warning"
      :closable="false"
      class="error-box"
    />
    <div v-if="row.error_code" class="run-error">{{ row.error_code }}</div>
    <div class="forecast-windows">
      <div v-for="h in ['1', '6', '24']" :key="h" class="forecast-window">
        <div class="window-heading">
          <h3>{{ h }} 小时预测</h3>
          <StatusTag
            :value="predictionConclusion(row, h).value"
            :text="predictionConclusion(row, h).text"
          />
        </div>
        <div
          v-if="(row.horizons[h] as any)?.points?.length"
          class="window-value"
        >
          预测末值
          {{
            number(
              metricValue(
                config.metric,
                (row.horizons[h] as any).points.at(-1)?.value,
              ),
              4,
            )
          }}
          {{ metricUnit(config.metric) }}
        </div>
        <div v-if="predictionConclusion(row, h).breachAt">
          首次超限 {{ date(predictionConclusion(row, h).breachAt) }}
        </div>
        <div v-if="(row.horizons[h] as any)?.model" class="window-model">
          模型 {{ (row.horizons[h] as any).model }} ·
          {{ label((row.horizons[h] as any).quality) }}
        </div>
      </div>
    </div>
  </section>
  <section class="panel">
    <div class="panel-heading">
      <h2>历史与预测</h2>
      <el-radio-group v-model="horizon"
        ><el-radio-button value="1">1h</el-radio-button
        ><el-radio-button value="6">6h</el-radio-button
        ><el-radio-button value="24">24h</el-radio-button></el-radio-group
      >
    </div>
    <el-alert
      v-if="chartError"
      :title="chartError"
      type="warning"
      :closable="false"
    />
    <MetricChart
      :data="history"
      :metric="config.metric || ''"
      :forecast="selected.points"
      :threshold="config.threshold"
      :anomalies="
        row?.anomalies.map((a: any) => ({
          timestamp: a.timestamp,
          value: a.actual,
        }))
      "
    />
    <div
      v-if="selected.interval_level || selected.risk?.first_breach_at"
      class="chart-facts"
    >
      <span v-if="selected.interval_level"
        >预测区间置信水平 {{ selected.interval_level * 100 }}%</span
      >
      <span v-if="selected.risk?.first_breach_at"
        >首次预计超限 {{ date(selected.risk.first_breach_at) }}</span
      >
    </div>
  </section>
  <section class="panel quality-panel">
    <div class="panel-heading"><h2>数据质量</h2></div>
    <p v-if="!Object.keys(quality).length" class="compact-empty">
      暂无数据质量记录
    </p>
    <template v-else>
      <dl class="quality-summary">
        <div>
          <dt>有效样本</dt>
          <dd>
            {{ number(quality.observed_samples, 0) }} /
            {{ number(quality.samples, 0) }}
          </dd>
        </div>
        <div>
          <dt>当前连续段缺失率</dt>
          <dd>
            {{
              quality.missing_fraction == null
                ? "—"
                : number(quality.missing_fraction * 100, 2) + "%"
            }}
          </dd>
        </div>
        <div>
          <dt>连续历史长度</dt>
          <dd>
            {{
              predictionDuration(
                quality.history_hours == null
                  ? null
                  : quality.history_hours * 60,
              )
            }}
          </dd>
        </div>
        <div>
          <dt>采样完整性</dt>
          <dd>
            <StatusTag
              v-if="quality.status"
              :value="
                quality.status === 'usable' ? 'validated' : quality.status
              "
              :text="
                quality.status === 'usable' ? '采样完整' : label(quality.status)
              "
            /><template v-else>—</template>
          </dd>
        </div>
        <div>
          <dt>历史起点</dt>
          <dd>{{ date(quality.history_start) }}</dd>
        </div>
        <div>
          <dt>数据截至</dt>
          <dd>{{ date(quality.data_end || row?.data_end_at) }}</dd>
        </div>
        <div
          v-for="period in [
            { key: 'daily_eligible', label: '日周期建模' },
            { key: 'weekly_eligible', label: '周周期建模' },
          ]"
          :key="period.key"
        >
          <dt>{{ period.label }}</dt>
          <dd>
            <StatusTag
              v-if="typeof quality[period.key] === 'boolean'"
              :value="quality[period.key] ? 'validated' : 'insufficient_data'"
              :text="quality[period.key] ? '满足数据要求' : '历史不足'"
            /><template v-else>—</template>
          </dd>
        </div>
      </dl>
      <div v-if="quality.reason" class="quality-reason">
        <span>不足原因</span
        ><strong>{{ qualityReasons[quality.reason] || quality.reason }}</strong>
      </div>
    </template>
  </section>
  <section
    class="panel anomaly-panel"
    :class="{ 'is-empty': !anomalies.length }"
  >
    <div class="panel-heading">
      <h2>残差异常</h2>
      <span v-if="!anomalies.length" class="compact-empty">暂无异常记录</span>
    </div>
    <template v-if="anomalies.length">
      <el-table :data="visibleAnomalies">
        <el-table-column type="expand" width="48"
          ><template #default="{ row: anomaly }"
            ><dl class="anomaly-metrics">
              <div>
                <dt>标准化偏差</dt>
                <dd>{{ number(anomaly.robust_z) }}</dd>
              </div>
              <div>
                <dt>平滑偏差</dt>
                <dd>{{ number(anomaly.ewma) }}</dd>
              </div>
            </dl></template
          ></el-table-column
        >
        <el-table-column label="异常时间" min-width="180"
          ><template #default="{ row: anomaly }">{{
            date(anomaly.timestamp)
          }}</template></el-table-column
        >
        <el-table-column label="异常类型" min-width="110"
          ><template #default="{ row: anomaly }">{{
            anomaly.kind === "spike"
              ? "突增 / 突降"
              : anomaly.kind === "drift"
                ? "持续偏移"
                : anomaly.kind
          }}</template></el-table-column
        >
        <el-table-column
          v-for="field in [
            { key: 'actual', label: '实测值' },
            { key: 'expected', label: '预测值' },
            { key: 'residual', label: '预测偏差' },
          ]"
          :key="field.key"
          :label="field.label + '（' + metricUnit(config.metric) + '）'"
          min-width="140"
          ><template #default="{ row: anomaly }">{{
            number(metricValue(config.metric, anomaly[field.key]), 4)
          }}</template></el-table-column
        >
      </el-table>
      <div v-if="anomalies.length > 10" class="pagination">
        <el-pagination
          v-model:current-page="anomalyPage"
          :page-size="10"
          :total="anomalies.length"
          layout="prev, pager, next"
          aria-label="残差异常分页"
        />
      </div>
    </template>
  </section>
  <section v-if="Array.isArray(quality.excluded_gaps)" class="panel gap-panel">
    <div class="panel-heading">
      <h2>已排除的历史缺口</h2>
    </div>
    <p v-if="!gaps.length" class="compact-empty">未排除连续缺口</p>
    <template v-else>
      <el-table :data="visibleGaps">
        <el-table-column label="开始时间" min-width="180"
          ><template #default="{ row: gap }">{{
            date(gap.start)
          }}</template></el-table-column
        >
        <el-table-column label="结束时间" min-width="180"
          ><template #default="{ row: gap }">{{
            date(gap.end)
          }}</template></el-table-column
        >
        <el-table-column label="持续时长" min-width="140"
          ><template #default="{ row: gap }">{{
            predictionDuration(
              gap.start == null || gap.end == null
                ? null
                : (gap.end - gap.start) / 60,
            )
          }}</template></el-table-column
        >
        <el-table-column label="缺失采样点" min-width="120"
          ><template #default="{ row: gap }">{{
            number(gap.bins, 0)
          }}</template></el-table-column
        >
      </el-table>
      <div v-if="gaps.length > 5" class="pagination">
        <el-pagination
          v-model:current-page="gapPage"
          :page-size="5"
          :total="gaps.length"
          layout="prev, pager, next"
          aria-label="历史缺口分页"
        />
      </div>
    </template>
  </section>
  <PredictionOutcomes :result-id="id" />
</template>
<style scoped>
.verdict-states,
.window-heading,
.chart-facts {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.verdict-title :deep(.el-tag) {
  height: auto;
  padding: 8px 12px;
  font-size: 18px;
  line-height: 1.4;
  white-space: normal;
}
.forecast-windows {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin-top: 18px;
}
.forecast-window {
  min-width: 0;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.window-heading h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.window-value,
.window-model {
  margin-top: 8px;
  font-size: 14px;
}
.chart-facts {
  margin-top: 12px;
  font-size: 14px;
}
.quality-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  column-gap: 24px;
  row-gap: 14px;
  margin: 0;
}
.quality-summary dt,
.anomaly-metrics dt {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 6px;
}
.quality-summary dd,
.anomaly-metrics dd {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.quality-reason {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 14px;
  font-size: 14px;
}
.quality-reason span {
  color: var(--muted);
}
.compact-empty {
  padding: 4px 0;
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}
.anomaly-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 32px;
  margin: 12px 24px;
}
.run-error {
  margin-top: 12px;
}
.pagination {
  margin-top: 12px;
}
.anomaly-panel.is-empty .panel-heading {
  margin-bottom: 0;
}
@media (max-width: 900px) {
  .quality-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .forecast-windows {
    grid-template-columns: 1fr;
    gap: 12px;
  }
}
@media (max-width: 500px) {
  .quality-summary {
    grid-template-columns: 1fr;
  }
}
</style>
