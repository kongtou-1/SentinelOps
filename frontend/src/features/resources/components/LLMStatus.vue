<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import StatusTag from "@/components/common/StatusTag.vue";

type Summary = components["schemas"]["LLMStatusResponse"];
type Bucket = components["schemas"]["LLMStatusBucket"];
const props = defineProps<{
  id: string;
  name: string;
  model: string;
  baseUrl: string;
  refreshKey?: string;
}>();
const summary = ref<Summary>(),
  error = ref(""),
  loading = ref(false);
let controller: AbortController | undefined;
let version = 0;

async function load() {
  if (!props.id) return;
  const current = ++version;
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  try {
    const result = await api<Summary>(
      `/llm-endpoints/${props.id}/status`,
      "GET",
      undefined,
      controller.signal,
    );
    if (current === version) {
      summary.value = result;
      error.value = "";
    }
  } catch (e) {
    if (current === version) {
      summary.value = undefined;
      error.value = e instanceof Error ? e.message : "状态加载失败";
    }
  } finally {
    if (current === version) loading.value = false;
  }
}
const { refresh } = usePoll(load, () => (props.id ? 30000 : null));
watch(
  () => [props.id, props.refreshKey],
  () => {
    summary.value = undefined;
    error.value = "";
    void refresh();
  },
);
onUnmounted(() => {
  version++;
  controller?.abort();
});

function state(bucket: Pick<Bucket, "request_count" | "success_count">) {
  if (!bucket.request_count) return "empty";
  if (!bucket.success_count) return "failed";
  return bucket.success_count === bucket.request_count ? "healthy" : "partial";
}
const overall = computed(() =>
  summary.value ? state(summary.value) : "empty",
);
function tooltip(bucket: Bucket) {
  return `${date(bucket.start)} — ${date(bucket.end)} · ${bucket.request_count ? `${bucket.request_count} 次探测，${bucket.success_count} 次成功` : "无探测记录"}`;
}
</script>

<template>
  <section
    class="panel llm-status"
    v-loading="loading"
    :aria-label="name + ' LLM 状态'"
  >
    <div class="panel-heading">
      <div class="endpoint-identity">
        <h2>{{ name }}</h2>
        <div class="endpoint-model">{{ model }}</div>
        <div class="endpoint-address">{{ baseUrl }}</div>
      </div>
      <div class="status-labels">
        <span class="source-label">主动探测</span>
        <StatusTag v-if="summary" :value="summary.status" />
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-else>
      <div class="rate-label">探测成功率 · 近 30 分钟</div>
      <div class="success-rate" :class="overall">
        {{
          summary?.success_rate == null
            ? "—"
            : (summary.success_rate * 100).toFixed(2) + "%"
        }}
      </div>
      <p class="request-count">
        {{
          summary
            ? summary.request_count
              ? `近 30 分钟 ${summary.request_count} 次探测，${summary.success_count} 次成功`
              : "近 30 分钟暂无探测记录"
            : id
              ? "正在加载"
              : "尚未添加 LLM 服务"
        }}
      </p>
      <div class="status-history" aria-label="近 30 分钟探测历史，每格 30 秒">
        <template v-if="summary">
          <el-tooltip
            v-for="bucket in summary.buckets"
            :key="bucket.start"
            :content="tooltip(bucket)"
            placement="top"
            :show-after="100"
          >
            <button
              type="button"
              class="status-block"
              :class="state(bucket)"
              :aria-label="tooltip(bucket)"
            />
          </el-tooltip>
        </template>
        <span
          v-for="index in summary ? 0 : 60"
          :key="index"
          class="status-block empty"
        />
      </div>
      <div class="time-axis"><span>30 分钟前</span><span>现在</span></div>
      <div class="status-footer">
        <span>{{ summary ? `更新于 ${date(summary.updated_at)}` : "" }}</span>
        <div class="status-legend">
          <span><i class="healthy" />全部成功</span>
          <span><i class="partial" />部分失败</span>
          <span><i class="failed" />全部失败</span>
          <span><i class="empty" />无记录</span>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.llm-status {
  min-width: 0;
}
.panel-heading h2 {
  margin: 0;
}
.llm-status > .panel-heading {
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
.endpoint-identity {
  min-width: 0;
  flex: 1 1 180px;
  overflow-wrap: anywhere;
}
.endpoint-model {
  margin-top: 8px;
}
.endpoint-address {
  margin-top: 4px;
  color: var(--muted);
}
.status-labels,
.status-footer,
.status-legend,
.status-legend span {
  display: flex;
  align-items: center;
}
.status-labels {
  gap: 12px;
  flex-shrink: 0;
}
.source-label,
.rate-label,
.status-footer,
.time-axis {
  color: var(--muted);
}
.rate-label {
  margin-top: 24px;
}
.success-rate {
  margin: 8px 0;
  font-size: clamp(36px, 5vw, 48px);
  font-weight: 650;
  line-height: 1.15;
  letter-spacing: -1px;
  font-variant-numeric: tabular-nums;
}
.success-rate.healthy {
  color: var(--el-color-success);
}
.success-rate.partial {
  color: var(--el-color-warning);
}
.success-rate.failed {
  color: var(--el-color-danger);
}
.success-rate.empty {
  color: var(--muted);
}
.request-count {
  margin: 0 0 26px;
}
.status-history {
  display: grid;
  grid-template-columns: repeat(60, minmax(0, 1fr));
  gap: 2px;
}
.status-block {
  height: 36px;
  min-width: 0;
  width: 100%;
  border: 0;
  padding: 0;
  border-radius: 3px;
  cursor: default;
}
.status-block:hover,
.status-block:focus-visible {
  outline: 2px solid var(--text);
  outline-offset: 2px;
  z-index: 1;
}
.healthy:not(.success-rate) {
  background: var(--el-color-success);
}
.partial:not(.success-rate) {
  background: var(--el-color-warning);
}
.failed:not(.success-rate) {
  background: var(--el-color-danger);
}
.empty:not(.success-rate) {
  background: var(--border);
}
.time-axis {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
}
.status-footer {
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 24px;
}
.status-legend {
  gap: 16px;
  flex-wrap: wrap;
}
.status-legend span {
  gap: 6px;
}
.status-legend i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}
@media (max-width: 600px) {
  .status-history {
    gap: 1px;
  }
  .status-block {
    height: 30px;
    border-radius: 2px;
  }
  .status-labels {
    gap: 8px;
  }
}
</style>
