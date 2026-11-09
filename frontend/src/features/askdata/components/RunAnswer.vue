<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { date } from "@/utils/display";
import {
  certificationLabels,
  runErrorLabel,
  runStatusLabel,
  runStepLabel,
  type AskDataColumnMeta,
  type AskDataEvidence,
  type ChatTurn,
} from "@/features/askdata/api";

const ResultChart = defineAsyncComponent(
  () => import("@/features/askdata/components/ResultChart.vue"),
);

const props = defineProps<{ run: ChatTurn }>();

const sqlOpen = ref(false);
const result = computed(() => props.run.result);
const columns = computed<AskDataColumnMeta[]>(() => result.value?.columns ?? []);
const tableRows = computed(() =>
  (result.value?.rows ?? []).map((row) => {
    const item: Record<string, unknown> = {};
    columns.value.forEach((_, index) => {
      item[`c${index}`] = row[index] ?? null;
    });
    return item;
  }),
);
const hasChart = computed(() => {
  const kind = result.value?.chart?.kind;
  return kind === "bar" || kind === "line";
});
const certLabel = computed(
  () => certificationLabels[result.value?.certification ?? "none"] || "无认证口径",
);
const certType = computed<"success" | "warning" | "info">(() => {
  if (result.value?.certification === "certified") return "success";
  if (result.value?.certification === "draft") return "warning";
  return "info";
});
const clarificationText = computed(
  () => props.run.clarification || result.value?.clarification || "需要补充信息",
);
const failureText = computed(() => runErrorLabel(props.run.errorCode) || "执行失败");

function statusTagType(status: string): "success" | "warning" | "info" | "danger" {
  if (status === "completed") return "success";
  if (status === "failed" || status === "interrupted") return "danger";
  if (status === "queued" || status === "running" || status === "needs_clarification")
    return "warning";
  return "info";
}
function dotClass(status: string) {
  if (["completed", "success", "done"].includes(status)) return "done";
  if (["failed", "error"].includes(status)) return "failed";
  return "running";
}
function formatCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function evidenceLabel(item: AskDataEvidence) {
  if (item.kind === "code_rule" && item.path) {
    return `${item.path.split("/").pop()}:${item.line ?? ""}`;
  }
  return item.name || item.kind;
}
function evidenceTip(item: AskDataEvidence) {
  const parts = [
    item.description,
    item.path ? `代码出处 ${item.path}:${item.line ?? ""}` : "",
    item.commit ? `提交 ${item.commit.slice(0, 12)}` : "",
    item.parse_state === "dynamic" ? "该片段无法静态解析，仅作口径依据" : "",
  ].filter(Boolean);
  return parts.join(" · ") || item.name;
}
</script>

<template>
  <div class="answer">
    <div class="answer-head">
      <el-tag :type="statusTagType(run.status)" size="small" effect="plain">
        {{ runStatusLabel(run.status) }}
      </el-tag>
      <span v-if="run.streaming" class="streaming">执行中…</span>
    </div>

    <div v-if="run.steps.length" class="steps">
      <div v-for="step in run.steps" :key="step.seq" class="step">
        <span class="step-dot" :class="dotClass(step.status)"></span>
        <span class="step-label">{{ step.summary || runStepLabel(step.step) }}</span>
      </div>
    </div>
    <div v-else-if="run.streaming" class="steps">
      <div class="step">
        <span class="step-dot running"></span>
        <span class="step-label">正在建立连接</span>
      </div>
    </div>

    <el-alert
      v-if="run.status === 'needs_clarification'"
      :title="clarificationText"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="run.status === 'failed'"
      :title="failureText"
      type="error"
      :closable="false"
      show-icon
    />
    <p v-if="run.status === 'cancelled'" class="hint">该问题已取消</p>
    <p v-if="run.status === 'interrupted'" class="hint">执行已中断，请重新提问</p>

    <template v-if="result">
      <p v-if="result.answer" class="answer-text">{{ result.answer }}</p>
      <div class="chips">
        <el-tag :type="certType" size="small" effect="plain">{{ certLabel }}</el-tag>
        <el-tooltip
          v-for="(item, index) in result.evidence || []"
          :key="index"
          :content="evidenceTip(item)"
          placement="top"
        >
          <el-tag :type="item.certified ? 'success' : 'info'" size="small" effect="plain">
            {{ item.kind === "code_rule" ? "代码 · " : "" }}{{ evidenceLabel(item) }}
          </el-tag>
        </el-tooltip>
      </div>

      <el-alert
        v-if="result.truncated"
        title="结果已截断"
        type="warning"
        :closable="false"
        show-icon
      />

      <div v-if="result.sql" class="sql-block">
        <button type="button" class="sql-toggle" @click="sqlOpen = !sqlOpen">
          SQL {{ sqlOpen ? "收起" : "展开" }}
        </button>
        <template v-if="sqlOpen">
          <pre class="sql-code">{{ result.sql }}</pre>
          <template v-if="result.executed_sql && result.executed_sql !== result.sql">
            <div class="sql-label">实际执行</div>
            <pre class="sql-code">{{ result.executed_sql }}</pre>
          </template>
        </template>
      </div>

      <el-table
        v-if="tableRows.length"
        :data="tableRows"
        class="result-table"
        size="small"
        border
        max-height="320"
        empty-text="没有结果数据"
      >
        <el-table-column
          v-for="(column, index) in columns"
          :key="index"
          :prop="`c${index}`"
          :label="column.name"
          min-width="140"
          show-overflow-tooltip
        >
          <template #default="{ row }">{{ formatCell(row[`c${index}`]) }}</template>
        </el-table-column>
      </el-table>

      <ResultChart v-if="hasChart" :result="result" />

      <p class="meta">
        返回 {{ result.row_count }} 行<span v-if="result.query_time"> · 查询时间 {{ date(result.query_time) }}</span>
      </p>
    </template>
  </div>
</template>

<style scoped>
.answer {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.answer-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.streaming {
  font-size: 12px;
  color: var(--muted, #8993a4);
}
.steps {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--border, #303644);
  border-radius: 8px;
}
.step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.step-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
.step-dot.running {
  background: #4c82ee;
}
.step-dot.done {
  background: #3fb27f;
}
.step-dot.failed {
  background: #f2557f;
}
.step-label {
  color: var(--text, #e6e9ef);
}
.answer-text {
  margin: 0;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sql-block {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.sql-toggle {
  justify-self: start;
  padding: 0;
  border: 0;
  background: none;
  color: #7aa7ff;
  font-size: 13px;
  cursor: pointer;
}
.sql-code {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  border: 1px solid var(--border, #303644);
  border-radius: 8px;
  background: rgba(20, 26, 36, 0.6);
  color: var(--text, #e6e9ef);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.sql-label {
  font-size: 12px;
  color: var(--muted, #8993a4);
}
.result-table {
  width: 100%;
}
.meta {
  margin: 0;
  font-size: 12px;
  color: var(--muted, #8993a4);
}
.hint {
  margin: 0;
  font-size: 13px;
  color: var(--muted, #8993a4);
}
</style>
