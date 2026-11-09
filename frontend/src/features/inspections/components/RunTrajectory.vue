<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { api, query, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import { errorNames, errorText, type Run } from "@/features/inspections/ui";
import {
  activeRun,
  compareEvents,
  eventState,
  eventTitle,
  eventTone,
  mergeEvents,
  type ProgressEvent,
} from "@/features/inspections/progress";
import JsonEvidence from "@/components/common/JsonEvidence.vue";

const props = defineProps<{ run: Run }>();
type Progress = Models["InspectionProgressResponse"];
const rows = ref<ProgressEvent[]>([]),
  current = ref<ProgressEvent | null>(null);
const status = ref(props.run.status),
  error = ref(""),
  olderError = ref("");
const cursor = ref<string | null>(null),
  loading = ref(false),
  loadingOlder = ref(false);
const viewport = ref<HTMLElement>(),
  following = ref(true),
  expanded = ref<string[]>([]);
const active = computed(() => activeRun(status.value));
const phase = computed(() => {
  if (error.value) return "轨迹更新失败";
  if (current.value) return `正在检查 · ${current.value.check_name}`;
  return (
    {
      queued: "等待执行",
      collecting: "正在获取检查对象",
      evaluating: "正在执行检查",
      completed: "巡检已完成",
      partial: "巡检部分完成",
      failed: "巡检执行失败",
      cancelled: "本次巡检已停止",
    }[status.value] || status.value
  );
});
let revision = 0,
  disposed = false;
async function followLatest() {
  following.value = true;
  await nextTick();
  if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight;
}
function onScroll() {
  const el = viewport.value;
  if (el)
    following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
}
async function load() {
  const version = ++revision,
    id = props.run.id;
  loading.value = true;
  try {
    const result = await api<Progress>(`/inspection-runs/${id}/progress`);
    if (disposed || version !== revision || id !== props.run.id) return;
    // If more than a page arrived between refreshes, fill the gap before merging.
    let incoming = result.events,
      next = result.next_cursor;
    const last = rows.value.at(-1);
    while (
      last &&
      next &&
      incoming.length &&
      compareEvents(incoming[0]!, last) >= 0 &&
      !incoming.some((event) => event.id === last.id)
    ) {
      const part = await api<Progress>(
        `/inspection-runs/${id}/progress` + query({ before: next }),
      );
      if (disposed || version !== revision || id !== props.run.id) return;
      incoming = mergeEvents(part.events, incoming);
      next = part.next_cursor;
    }
    if (!rows.value.length) cursor.value = result.next_cursor || null;
    rows.value = mergeEvents(rows.value, incoming);
    current.value = result.current || null;
    status.value = result.status;
    error.value = "";
    if (following.value) await followLatest();
  } catch (e) {
    if (!disposed && version === revision) error.value = errorText(e);
  } finally {
    if (version === revision) loading.value = false;
  }
}
async function loadOlder() {
  if (!cursor.value || loadingOlder.value) return;
  const id = props.run.id,
    before = cursor.value;
  loadingOlder.value = true;
  following.value = false;
  try {
    const result = await api<Progress>(
      `/inspection-runs/${id}/progress` + query({ before }),
    );
    if (disposed || id !== props.run.id) return;
    const el = viewport.value,
      height = el?.scrollHeight || 0,
      top = el?.scrollTop || 0;
    rows.value = mergeEvents(rows.value, result.events);
    cursor.value = result.next_cursor || null;
    olderError.value = "";
    await nextTick();
    if (el) el.scrollTop = top + el.scrollHeight - height;
  } catch (e) {
    if (!disposed && id === props.run.id) olderError.value = errorText(e);
  } finally {
    if (id === props.run.id) loadingOlder.value = false;
  }
}
watch(
  () => props.run.id,
  () => {
    revision++;
    rows.value = [];
    cursor.value = null;
    current.value = null;
    expanded.value = [];
    error.value = olderError.value = "";
    loadingOlder.value = false;
    status.value = props.run.status;
    following.value = true;
    void load();
  },
);
watch(
  () => props.run.status,
  () => void load(),
);
onUnmounted(() => {
  disposed = true;
  revision++;
});
usePoll(load, () => (active.value ? 2000 : null));
</script>
<template>
  <section class="inspection-panel run-trajectory" aria-label="运行轨迹">
    <header class="trajectory-heading">
      <div class="trajectory-title">
        <h3>运行轨迹</h3>
        <span
          :class="[
            'update-state',
            { live: active && !error, interrupted: !!error },
          ]"
        >
          {{ error ? "更新中断" : active ? "实时更新" : "已结束" }}
        </span>
      </div>
      <el-button :loading="loading" @click="load">刷新轨迹</el-button>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div
      ref="viewport"
      class="trajectory-feed"
      tabindex="0"
      aria-label="巡检执行步骤"
      @scroll="onScroll"
    >
      <div v-if="cursor" class="older-events">
        <el-button :loading="loadingOlder" @click="loadOlder"
          >加载更早步骤</el-button
        >
        <p v-if="olderError" role="alert">{{ olderError }}</p>
      </div>
      <ol class="trajectory-events">
        <li
          v-for="event in rows"
          :key="event.id"
          :class="['trajectory-event', eventTone(event)]"
        >
          <div class="event-line">
            <strong>{{ eventTitle(event) }}</strong>
            <span v-if="eventState(event)" class="event-state">{{
              eventState(event)
            }}</span>
            <time :datetime="event.occurred_at">{{
              date(event.occurred_at)
            }}</time>
          </div>
          <p v-if="event.object_key" class="event-context">
            {{
              event.object_key === "@collection"
                ? "本次对象集合"
                : `对象 ${event.object_key}`
            }}
            <template v-if="event.kind === 'tool' && event.check_name">
              · {{ event.check_name }}</template
            >
          </p>
          <p v-if="event.summary" class="event-summary">{{ event.summary }}</p>
          <p v-if="event.error_code" class="event-summary">
            {{ errorNames[event.error_code] || event.error_code }}
          </p>
          <el-collapse
            v-if="event.data && Object.keys(event.data).length"
            v-model="expanded"
          >
            <el-collapse-item
              :name="event.id"
              :title="
                event.data.code ? '查看 Python 代码与结果' : '查看输入与输出'
              "
            >
              <template v-if="expanded.includes(event.id)">
                <pre v-if="event.data.code" class="evidence">{{
                  event.data.code
                }}</pre>
                <JsonEvidence :value="event.data" />
              </template>
            </el-collapse-item>
          </el-collapse>
        </li>
      </ol>
      <div v-if="active || error" class="current-step" role="status">
        <span v-if="active && !error" class="running-dot" aria-hidden="true" />
        <div>
          <strong>{{ phase }}</strong>
          <p v-if="current?.object_key">
            {{
              current.object_key === "@collection"
                ? "本次对象集合"
                : `对象 ${current.object_key}`
            }}
          </p>
        </div>
      </div>
      <el-empty
        v-else-if="!rows.length && !loading"
        description="暂无执行步骤"
        :image-size="70"
      />
    </div>
    <footer class="trajectory-footer">
      <span>{{ phase }}</span>
      <el-button v-if="!following" type="primary" plain @click="followLatest"
        >回到最新步骤 ↓</el-button
      >
    </footer>
  </section>
</template>
<style scoped>
.run-trajectory {
  padding: 0;
  overflow: hidden;
}
.trajectory-heading,
.trajectory-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
}
.trajectory-heading {
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.trajectory-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.trajectory-title h3 {
  margin: 0;
}
.update-state {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.update-state.live {
  color: var(--el-color-primary);
}
.update-state.interrupted {
  color: var(--el-color-danger);
}
.trajectory-feed {
  max-height: 580px;
  min-height: 220px;
  overflow-y: auto;
  padding: 20px 24px;
  overflow-anchor: none;
}
.trajectory-events {
  list-style: none;
  padding: 0 0 0 20px;
  margin: 0;
}
.trajectory-event {
  position: relative;
  padding: 0 0 24px 20px;
  border-left: 1px solid var(--el-border-color);
}
.trajectory-event::before {
  content: "";
  position: absolute;
  left: -5px;
  top: 6px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
}
.trajectory-event.success::before {
  background: var(--el-color-success);
}
.trajectory-event.danger::before {
  background: var(--el-color-danger);
}
.trajectory-event.warning::before {
  background: var(--el-color-warning);
}
.event-line {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px 12px;
}
.event-line strong {
  font-size: 14px;
}
.event-line time {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: auto;
}
.event-state {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.danger .event-state {
  color: var(--el-color-danger);
}
.warning .event-state {
  color: var(--el-color-warning);
}
.event-context,
.event-summary,
.current-step p {
  margin: 7px 0 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.event-context,
.current-step p {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.trajectory-event :deep(.el-collapse) {
  margin-top: 8px;
  border: 0;
}
.trajectory-event :deep(.el-collapse-item__header) {
  height: 32px;
  line-height: 32px;
  border: 0;
  color: var(--el-text-color-secondary);
}
.trajectory-event :deep(.el-collapse-item__wrap) {
  border: 0;
}
.trajectory-event :deep(.evidence) {
  max-width: 100%;
}
.current-step {
  display: flex;
  align-items: baseline;
  gap: 16px;
  padding: 0 16px 6px;
  font-size: 14px;
}
.running-dot {
  width: 9px;
  height: 9px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: pulse 1.5s ease-in-out infinite;
}
.trajectory-footer {
  min-height: 64px;
  border-top: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.older-events {
  text-align: center;
  padding-bottom: 20px;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .running-dot {
    animation: none;
  }
}
@media (max-width: 600px) {
  .trajectory-feed {
    padding: 20px 12px;
  }
  .event-line time {
    margin-left: 0;
    width: 100%;
  }
  .trajectory-heading,
  .trajectory-footer {
    padding: 12px;
  }
}
</style>
