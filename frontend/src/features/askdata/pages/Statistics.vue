<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, onMounted, ref, watch } from "vue";
import {
  askdata,
  runErrorLabel,
  type AskDataConnection,
  type AskDataStatistics,
} from "@/features/askdata/api";

const connections = ref<AskDataConnection[]>([]);
const connectionId = ref("");
const days = ref(30);
const data = ref<AskDataStatistics | null>(null);
const busy = ref(false);
const error = ref("");

const maxDaily = computed(() =>
  Math.max(1, ...(data.value?.daily ?? []).map((point) => point.runs)),
);
const coverage = computed(() => Math.round((data.value?.certification.coverage ?? 0) * 100));
const successRate = computed(() => Math.round((data.value?.totals.success_rate ?? 0) * 100));

function connectionLabel(row: AskDataConnection) {
  return `${row.name} · ${row.database}（${row.environment}）`;
}
function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}
function seconds(value: number) {
  return value ? `${value.toFixed(2)}s` : "—";
}
function tokens(value: number) {
  return value ? Math.round(value).toLocaleString() : "0";
}

async function loadConnections() {
  try {
    connections.value = await askdata.connections();
  } catch {
    connections.value = [];
  }
}

async function load() {
  busy.value = true;
  error.value = "";
  try {
    data.value = await askdata.statistics({
      connection_id: connectionId.value || undefined,
      days: days.value,
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : "统计加载失败";
  } finally {
    busy.value = false;
  }
}

watch([connectionId, days], () => void load());
onMounted(async () => {
  await loadConnections();
  await load();
});
</script>

<template>
  <section v-loading="busy" class="statistics">
    <div class="page-heading">
      <h1>使用统计</h1>
      <PageIntro />
      <div class="filters">
        <el-select v-model="connectionId" clearable placeholder="全部数据库连接" class="filter">
          <el-option v-for="row in connections" :key="row.id" :label="connectionLabel(row)" :value="row.id" />
        </el-select>
        <el-select v-model="days" class="days">
          <el-option :value="7" label="近 7 天" />
          <el-option :value="30" label="近 30 天" />
          <el-option :value="90" label="近 90 天" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      title="统计仅包含聚合指标，不展示任何用户的提问与查询结果。"
      type="info"
      :closable="false"
    />

    <template v-if="data">
      <div class="cards">
        <div class="card">
          <span class="label">问数次数</span>
          <strong>{{ data.totals.runs }}</strong>
          <span class="muted">成功 {{ data.totals.completed }} · 失败 {{ data.totals.failed }}</span>
        </div>
        <div class="card">
          <span class="label">成功率</span>
          <strong>{{ successRate }}%</strong>
          <span class="muted">需澄清 {{ data.totals.clarification }} · 取消 {{ data.totals.cancelled }}</span>
        </div>
        <div class="card">
          <span class="label">平均耗时</span>
          <strong>{{ seconds(data.totals.average_seconds) }}</strong>
          <span class="muted">P50 {{ seconds(data.totals.p50_seconds) }} · P95 {{ seconds(data.totals.p95_seconds) }}</span>
        </div>
        <div class="card">
          <span class="label">模型调用</span>
          <strong>{{ tokens(data.models.calls) }}</strong>
          <span class="muted">输入 {{ tokens(data.models.input_tokens) }} · 输出 {{ tokens(data.models.output_tokens) }} tokens</span>
        </div>
        <div class="card">
          <span class="label">口径认证覆盖</span>
          <strong>{{ coverage }}%</strong>
          <span class="muted">已认证 {{ data.certification.certified }} · 草稿 {{ data.certification.draft }} · 待复核 {{ data.certification.pending_review }}</span>
        </div>
      </div>

      <section class="panel">
        <h2>每日问数</h2>
        <div class="bars">
          <div v-for="point in data.daily" :key="point.label" class="bar-column" :title="`${point.label}：${point.runs} 次，失败 ${point.failed}`">
            <div class="bar-track">
              <div class="bar-failed" :style="{ height: `${(point.failed / maxDaily) * 100}%` }" />
              <div class="bar-ok" :style="{ height: `${((point.runs - point.failed) / maxDaily) * 100}%` }" />
            </div>
            <span class="bar-label">{{ point.label.slice(5) }}</span>
          </div>
        </div>
        <div class="legend">
          <span><i class="dot ok" />成功</span>
          <span><i class="dot failed" />失败</span>
        </div>
      </section>

      <section class="panel">
        <h2>失败原因</h2>
        <el-table :data="data.failures" empty-text="所选范围内没有失败记录" border size="small">
          <el-table-column label="原因" min-width="240">
            <template #default="{ row }">{{ runErrorLabel(row.code) || row.code }}</template>
          </el-table-column>
          <el-table-column prop="count" label="次数" width="120" />
          <el-table-column label="占比" width="140">
            <template #default="{ row }">{{ percent(data.totals.failed ? row.count / data.totals.failed : 0) }}</template>
          </el-table-column>
        </el-table>
      </section>
    </template>
    <el-empty v-else-if="!busy" description="所选范围内暂无统计数据" />
  </section>
</template>

<style scoped>
.statistics { display: grid; gap: 12px; min-width: 0; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h1 { margin: 0; font-size: 24px; }
.filters { display: flex; gap: 8px; align-items: center; }
.filter { width: 220px; }
.days { width: 120px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.card { display: grid; gap: 4px; padding: 14px; border: 1px solid var(--border, #303644); border-radius: 8px; }
.card .label { color: var(--muted, #8993a4); font-size: 12px; }
.card strong { font-size: 24px; }
.muted { color: var(--muted, #8993a4); font-size: 12px; }
.panel { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--border, #303644); border-radius: 8px; }
.panel h2 { margin: 0; font-size: 16px; }
.bars { display: flex; gap: 4px; align-items: flex-end; height: 160px; overflow-x: auto; }
.bar-column { display: grid; justify-items: center; gap: 4px; min-width: 18px; flex: 1; }
.bar-track { display: flex; flex-direction: column; justify-content: flex-end; width: 12px; height: 140px; }
.bar-ok { background: #3f9f6f; }
.bar-failed { background: #f2557f; }
.bar-label { color: var(--muted, #8993a4); font-size: 10px; writing-mode: vertical-rl; }
.legend { display: flex; gap: 16px; color: var(--muted, #8993a4); font-size: 12px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
.dot.ok { background: #3f9f6f; }
.dot.failed { background: #f2557f; }
</style>
