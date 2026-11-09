<script setup lang="ts">
import { saveBlob } from "@/platform/desktop";
import { computed, ref, watch } from "vue";
import { api, query, request, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import {
  errorNames,
  errorText,
  stateNames,
  tone,
  type Item,
  type Run,
} from "@/features/inspections/ui";
import { itemTitle } from "@/features/inspections/display";
import { date } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import InspectionChecks from "./InspectionChecks.vue";

type Check = Models["InspectionCheckResultResponse"];
type Trace = Pick<
  Models["InspectionTraceResponse"],
  | "id"
  | "tool"
  | "success"
  | "data"
  | "created_at"
  | "object_key"
  | "check_name"
  | "check_id"
>;
const props = defineProps<{ run: Run; selectedCheck?: Check }>();
const rows = ref<Trace[]>([]),
  objects = ref<Item[]>([]),
  checks = ref<Check[]>([]);
const objectId = ref(
  props.selectedCheck?.item_id || (props.selectedCheck ? "@collection" : ""),
);
const checkId = ref(props.selectedCheck?.id || "");
const selectedItem = ref<Item>(),
  selected = ref<Check | undefined>(props.selectedCheck);
const objectSearch = ref("");
const page = ref(1),
  total = ref(0),
  error = ref(""),
  loading = ref(false);
const active = computed(() =>
  ["queued", "collecting", "evaluating"].includes(props.run.status),
);
const unified = computed(() => !!props.run.snapshot.checks);
const phase = computed(
  () =>
    ({
      queued: "等待执行",
      collecting: "正在获取检查对象",
      evaluating: "正在执行检查",
      completed: "巡检已完成",
      partial: "巡检部分完成",
      failed: "巡检执行失败",
      cancelled: "本次巡检已停止",
    })[props.run.status],
);
const step = computed(() =>
  props.run.status === "queued"
    ? 0
    : props.run.status === "collecting"
      ? 1
      : active.value
        ? 2
        : 3,
);
const checkStates: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  error: "执行错误",
};
const toolNames: Record<string, string> = {
  read_inspection_skill: "读取检查方法",
  get_inspection_object: "读取对象数据",
  list_inspection_queries: "查看授权查询",
  request_inspection_api: "查询业务接口",
  query_inspection_database: "查询只读数据库",
  execute_inspection_python: "执行隔离 Python",
  search_agent_history: "检索历史结果",
  read_agent_history: "读取历史结果",
  search_knowledge: "检索知识",
};
const options = computed(() => {
  const values = objects.value.map((item, index) => ({
    id: item.id,
    label: itemTitle(item, index + 1),
  }));
  if (
    objectId.value &&
    objectId.value !== "@collection" &&
    !values.some((v) => v.id === objectId.value)
  )
    values.unshift({
      id: objectId.value,
      label: selectedItem.value
        ? itemTitle(selectedItem.value, 1)
        : props.selectedCheck?.object_key || "已选对象",
    });
  return values;
});
let revision = 0,
  objectRevision = 0,
  initialized = !!props.selectedCheck;
async function searchObjects(search = "") {
  const current = ++objectRevision;
  const result = await request<Item[]>(
    "/inspection-runs/" +
      props.run.id +
      "/items" +
      query({ search, limit: 20 }),
  );
  if (current === objectRevision) objects.value = result.data;
}
async function findObjects(search: string) {
  objectSearch.value = search;
  try {
    await searchObjects(search);
  } catch (e) {
    error.value = errorText(e);
  }
}
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    await searchObjects(objectSearch.value);
    if (current !== revision) return;
    if (!initialized && objects.value.length) {
      objectId.value = objects.value[0]!.id;
      initialized = true;
    }
    const item = objects.value.find((item) => item.id === objectId.value);
    if (item || selectedItem.value?.id !== objectId.value)
      selectedItem.value = item;
    if (!unified.value && objectId.value) {
      const item = await api<Item>("/inspection-items/" + objectId.value);
      if (current !== revision) return;
      selectedItem.value = item;
    }
    if (unified.value && objectId.value) {
      const result = await request<Check[]>(
        "/inspection-runs/" +
          props.run.id +
          "/check-results" +
          query({
            item_id:
              objectId.value === "@collection" ? undefined : objectId.value,
            scope: objectId.value === "@collection" ? "collection" : "object",
            limit: 100,
          }),
      );
      if (current !== revision) return;
      checks.value = result.data;
      if (!checkId.value)
        checkId.value =
          (
            checks.value.find((c) => c.execution_status === "running") ||
            checks.value[0]
          )?.id || "";
      selected.value = checks.value.find((c) => c.id === checkId.value);
    }
    let evidence: Trace[] = [],
      count = 0;
    if (!unified.value && selectedItem.value) {
      const calls = (selectedItem.value.evidence.tool_calls || []) as Record<
        string,
        unknown
      >[];
      const entries = calls.map((call) => ({
        id: String(call.id),
        tool: String(call.tool),
        success: call.success === true,
        created_at: String(call.observed_at),
        object_key: selectedItem.value!.record_id,
        data: {
          arguments: call.arguments,
          value: call.data,
          code_sha256: call.code_sha256,
        },
      }));
      evidence = entries.slice((page.value - 1) * 20, page.value * 20);
      count = entries.length;
    } else if (!objectId.value || selected.value?.kind === "agent") {
      const result = await request<Trace[]>(
        "/inspection-runs/" +
          props.run.id +
          "/traces" +
          query({
            check_result_id: selected.value?.id,
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
      );
      evidence = result.data;
      count = result.total || 0;
    }
    if (current !== revision) return;
    rows.value = evidence;
    total.value = count;
    error.value = "";
  } catch (e) {
    if (current === revision) error.value = errorText(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
function reload() {
  revision++;
  rows.value = [];
  total.value = 0;
  if (page.value !== 1) page.value = 1;
  else void load();
}
function selectObject(value: string) {
  initialized = true;
  objectId.value = value;
  checkId.value = "";
  selected.value = undefined;
  selectedItem.value = undefined;
  checks.value = [];
  reload();
}
function selectCheck(value: string) {
  checkId.value = value;
  selected.value = checks.value.find((c) => c.id === value);
  reload();
}
watch(
  () => props.selectedCheck,
  (value) => {
    if (!value) return;
    initialized = true;
    objectId.value = value.item_id || "@collection";
    checkId.value = value.id;
    selected.value = value;
    reload();
  },
);
watch(
  () => props.run.status,
  () => void load(),
);
watch(page, () => void load());
usePoll(load, () => (active.value ? 3000 : null));
function download(row: Trace) {
  return saveBlob(
    new Blob([JSON.stringify(row, null, 2)], { type: "application/json" }),
    "inspection-evidence-" + row.id + ".json",
  );
}
</script>
<template>
  <div class="execution-evidence">
    <section class="inspection-panel process-status">
      <div class="inspection-heading">
        <h3>{{ phase }}</h3>
        <el-button :loading="loading" @click="load">刷新过程</el-button>
      </div>
      <el-steps
        v-if="active || run.status === 'completed'"
        :active="run.status === 'completed' ? 4 : step"
        simple
        :process-status="run.status === 'failed' ? 'error' : 'process'"
      >
        <el-step title="等待执行" /><el-step title="获取对象" /><el-step
          title="逐项检查"
        /><el-step title="结束" />
      </el-steps>
      <div class="process-counts">
        <span>已发现 {{ run.summary.objects ?? run.collected }} 个对象</span
        ><span>已发起 {{ run.agent_calls }} 次 Agent 检查</span>
      </div>
    </section>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="process-filters">
      <label
        >检查对象<el-select
          :model-value="objectId"
          filterable
          remote
          clearable
          placeholder="全部对象"
          :remote-method="findObjects"
          aria-label="选择检查对象"
          @change="selectObject($event || '')"
        >
          <el-option v-if="unified" label="本次对象集合" value="@collection" />
          <el-option
            v-for="item in options"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          /> </el-select
      ></label>
      <label v-if="unified"
        >检查项<el-select
          :model-value="checkId"
          :disabled="!objectId || !checks.length"
          placeholder="选择检查项"
          aria-label="选择检查项"
          @change="selectCheck"
        >
          <el-option
            v-for="check in checks"
            :key="check.id"
            :value="check.id"
            :label="`${check.result.name || check.check_id} · ${checkStates[check.execution_status]}`"
          /> </el-select
      ></label>
    </div>
    <section v-if="selected" class="inspection-panel selected-check">
      <div class="inspection-heading">
        <h3>{{ selected.result.name || selected.check_id }}</h3>
        <el-tag
          :type="
            selected.execution_status === 'error'
              ? 'danger'
              : selected.execution_status === 'completed'
                ? tone(selected.status)
                : 'info'
          "
          >{{ checkStates[selected.execution_status] }}</el-tag
        >
      </div>
      <p v-if="selected.execution_status === 'running'">
        正在执行{{ selected.kind === "agent" ? " Agent" : "规则" }}检查
      </p>
      <p v-else-if="selected.execution_status === 'pending'">
        等待执行此检查项
      </p>
      <p v-else>
        {{ errorNames[selected.error_code || ""] || selected.summary }}
      </p>
      <InspectionChecks
        v-if="selected.kind === 'rule' && selected.result.conditions"
        :checks="selected.result.conditions as Record<string, unknown>[]"
      />
    </section>
    <section v-if="!unified && selectedItem" class="inspection-panel">
      <div class="inspection-heading">
        <h3>{{ itemTitle(selectedItem, 1) }}</h3>
        <el-tag :type="tone(selectedItem.agent_status)">{{
          stateNames[selectedItem.agent_status] || selectedItem.agent_status
        }}</el-tag>
      </div>
      <p>{{ selectedItem.explanation }}</p>
      <InspectionChecks
        v-if="selectedItem.checks.length"
        :checks="selectedItem.checks"
      />
    </section>
    <el-empty
      v-if="
        !rows.length &&
        !loading &&
        !selected?.started_at &&
        !selectedItem?.checks.length
      "
      :description="active ? '等待产生执行记录' : '此范围没有工具调用记录'"
      :image-size="70"
    />
    <el-timeline>
      <el-timeline-item
        v-if="selected?.started_at && page === 1"
        :timestamp="date(selected.started_at)"
        ><strong>开始检查</strong></el-timeline-item
      >
      <el-timeline-item
        v-for="row in rows"
        :key="row.id"
        :timestamp="date(row.created_at)"
        :type="row.success ? 'success' : 'danger'"
      >
        <section class="evidence-card">
          <header class="inspection-heading">
            <strong>{{ toolNames[row.tool] || row.tool }}</strong>
            <div>
              <el-tag :type="row.success ? 'success' : 'danger'">{{
                row.success ? "成功" : "失败"
              }}</el-tag
              ><el-button link type="primary" @click="download(row)"
                >下载证据</el-button
              >
            </div>
          </header>
          <p v-if="!objectId && row.object_key">
            {{
              row.object_key === "@collection"
                ? "本次对象集合"
                : `对象 ${row.object_key}`
            }}
            · {{ row.check_name || row.check_id }}
          </p>
          <el-collapse
            ><el-collapse-item
              :title="
                row.data.code ? 'Python 代码与执行结果' : '查看输入与输出'
              "
              :name="row.id"
            >
              <pre v-if="row.data.code" class="evidence">{{
                row.data.code
              }}</pre>
              <JsonEvidence :value="row.data" /></el-collapse-item
          ></el-collapse>
        </section>
      </el-timeline-item>
      <el-timeline-item
        v-if="selected?.finished_at && page * 20 >= total"
        :timestamp="date(selected.finished_at)"
        :type="selected.execution_status === 'error' ? 'danger' : 'success'"
        ><strong>{{
          selected.execution_status === "error"
            ? "检查执行错误"
            : `检查结束 · ${stateNames[selected.status]}`
        }}</strong></el-timeline-item
      >
    </el-timeline>
    <el-pagination
      v-if="total > 20"
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="total, prev, pager, next"
    />
  </div>
</template>
<style scoped>
.execution-evidence {
  display: grid;
  gap: 18px;
}
.process-status {
  display: grid;
  gap: 18px;
}
.process-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
}
.process-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.process-filters label {
  display: grid;
  gap: 8px;
  width: min(360px, 100%);
}
.selected-check p {
  margin: 12px 0;
}
.evidence-card {
  padding: 16px 20px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
}
.evidence-card header {
  margin-bottom: 12px;
}
.evidence-card .el-button {
  margin-left: 12px;
}
@media (max-width: 600px) {
  .process-status :deep(.el-step__title) {
    font-size: 12px;
  }
}
</style>
