<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, all, query, request, type Models } from "@/api/client";
import { configs } from "@/config/lists";
import { metricLabels, predictionMetrics } from "@/config/metrics";
import { type Row } from "@/types/records";
import { date, effectiveApproval, label, short } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import StatusTag from "@/components/common/StatusTag.vue";
import AuditRecordDetails from "@/features/system/components/AuditRecordDetails.vue";
import {
  auditActions,
  auditIncidentName,
  type AuditRecord,
} from "@/features/system/auditDisplay";
import RecordForm from "@/components/records/RecordForm.vue";
import ExportCsvButton from "@/components/records/ExportCsvButton.vue";
import LLMStatusOverview from "@/features/resources/components/LLMStatusOverview.vue";
import ContainerServices from "@/features/resources/components/ContainerServices.vue";
import LLMServiceBindings from "@/features/applications/components/LLMServiceBindings.vue";
import IncidentBoard from "@/features/incidents/components/IncidentBoard.vue";
import ApprovalOverview from "@/features/actions/components/ApprovalOverview.vue";
import PredictionOverview from "@/features/predictions/components/PredictionOverview.vue";
import { readPredictionTargets } from "@/features/predictions/api";
import { predictionConclusions } from "@/features/predictions/model/results";
import MetricBar from "@/components/charts/MetricBar.vue";
import {
  readServerSamples,
  type ServerSamples,
} from "@/features/resources/model/serverMetrics";
const props = defineProps<{ kind: string }>(),
  route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const config = computed(() => configs[props.kind]!),
  rows = ref<Row[]>([]),
  total = ref(0),
  page = ref(1),
  size = ref(10),
  loading = ref(false),
  error = ref(""),
  busy = ref(false),
  editing = ref<Row | null>(null),
  formOpen = ref(false),
  inspect = ref<Row | null>(null),
  dates = ref<string[]>([]),
  filters = ref<Row>({}),
  servers = ref<Row[]>([]),
  assignees = ref<Row[]>([]),
  endpoints = ref<Row[]>([]),
  incidentOptions = ref<Row[]>([]),
  generateOpen = ref(false),
  chosenIncident = ref(""),
  knowledgeQuery = ref(""),
  knowledgeTag = ref(""),
  searchResults = ref<Row[]>([]),
  knowledgeSearched = ref(false),
  knowledgeSearching = ref(false);
const knowledgeSearch = computed(
  () => props.kind === "knowledge" && route.path === "/knowledge/search",
);
const resourcePage = computed(() =>
  ["servers", "containers", "llm-endpoints"].includes(props.kind),
);
const pageTitle = computed(() =>
  props.kind === "incidents"
    ? "告警与事件"
    : props.kind === "approvals"
      ? "诊断Agent"
      : props.kind === "predictions"
        ? "预测中心"
        : knowledgeSearch.value
          ? "知识检索"
          : config.value.title,
);
const view = ref(
  props.kind === "incidents"
    ? "board"
    : props.kind === "approvals"
      ? "workspace"
      : "table",
);
const containerWorkspace = ref<InstanceType<typeof ContainerServices>>();
const llmBindings = ref<Models["LLMBindingResponse"][]>([]),
  bindingEndpoint = ref<Row>();
const hasListFilters = computed(
  () =>
    config.value.search ||
    config.value.states ||
    config.value.dateFilter ||
    servers.value.length ||
    endpoints.value.length ||
    assignees.value.length ||
    [
      "prediction-tasks",
      "alerts",
      "incidents",
      "approvals",
      "executions",
      "reports",
      "audit-logs",
    ].includes(props.kind),
);
const samples = ref<ServerSamples>({});
const predictionRefresh = ref(0);
let sampleController: AbortController | undefined;
let predictionController: AbortController | undefined;
onUnmounted(() => {
  sampleController?.abort();
  predictionController?.abort();
});
watch(
  () => route.query.task_id,
  (id) => {
    if (props.kind !== "predictions" || !id) return;
    filters.value.task_id = String(id);
    rows.value = [];
    total.value = 0;
    search();
  },
);
function selectPredictionTask(id: string) {
  if (!id) {
    predictionController?.abort();
    rows.value = [];
    total.value = 0;
    loading.value = false;
    delete filters.value.task_id;
    if (route.query.task_id) {
      const nextQuery = { ...route.query };
      delete nextQuery.task_id;
      void router.replace({ query: nextQuery });
    }
    return;
  }
  if (id === String(route.query.task_id || "")) {
    if (filters.value.task_id !== id) {
      filters.value.task_id = id;
      search();
    }
    return;
  }
  const target = { query: { ...route.query, task_id: id } };
  if (filters.value.task_id) void router.push(target);
  else void router.replace(target);
}
function refresh() {
  if (props.kind === "predictions") predictionRefresh.value++;
  if (props.kind === "servers") void containerWorkspace.value?.refresh();
  void load();
}
function resetFilters() {
  filters.value =
    props.kind === "predictions" ? { task_id: route.query.task_id } : {};
  dates.value = [];
  search();
}
watch(
  () => route.query.create,
  (value) => {
    if (value && props.kind === "servers" && auth.can("resources:manage")) {
      edit(null);
      const nextQuery = { ...route.query };
      delete nextQuery.create;
      void router.replace({ path: route.path, query: nextQuery });
    }
  },
  { immediate: true },
);
const displayColumns = computed(() =>
  props.kind === "servers"
    ? config.value.columns.filter((c) =>
        ["name", "monitoring_enabled"].includes(c.key),
      )
    : config.value.columns,
);
const actionColumnWidth = computed(() => {
  const labels = [
    config.value.detail
      ? props.kind === "predictions"
        ? "查看预测"
        : "详情"
      : "",
    config.value.fields && auth.can(config.value.manage!) ? "编辑" : "",
    props.kind === "servers" && auth.can("resources:manage") ? "SSH 测试" : "",
    props.kind === "llm-endpoints" && auth.can("llm:probe") ? "探测" : "",
    props.kind === "llm-endpoints" && auth.can("llm:manage") ? "关联服务" : "",
    props.kind === "prediction-tasks" && auth.can("predictions:run")
      ? "运行"
      : "",
    config.value.manage && auth.can(config.value.manage) ? "删除" : "",
    props.kind === "containers" && auth.can("resources:manage") ? "退役" : "",
    props.kind === "audit-logs" ? "记录详情" : "",
  ].filter(Boolean);
  return Math.max(
    100,
    24 +
      labels.reduce((width, text) => width + text.length * 16 + 16, 0) +
      Math.max(0, labels.length - 1) * 8,
  );
});
function currentFilters() {
  const values = { ...filters.value };
  if (dates.value?.length === 2) {
    values.start = new Date(dates.value[0]!).toISOString();
    values.end = new Date(dates.value[1]!).toISOString();
  }
  return values;
}
async function exportRows(signal: AbortSignal): Promise<Row[]> {
  const kind = props.kind;
  const values = currentFilters();
  if (kind === "predictions" && !values.task_id) return [];
  const names = new Map(
    assignees.value.map((user) => [user.id, user.username]),
  );
  const records = await all<Row>("/" + kind + query(values), signal);
  if (kind === "predictions")
    return readPredictionTargets(records, auth.can, signal);
  return kind === "incidents"
    ? records.map((row) => ({
        ...row,
        assignee_name: names.get(row.assignee_id),
      }))
    : records;
}
async function load() {
  let signal: AbortSignal | undefined;
  if (props.kind === "predictions") {
    predictionController?.abort();
    predictionController = new AbortController();
    signal = predictionController.signal;
    if (!filters.value.task_id) return;
  }
  loading.value = true;
  error.value = "";
  try {
    const f = currentFilters();
    if (props.kind === "prediction-tasks") {
      const tasks = (await all<Row>("/prediction-tasks")).filter(
        (task) =>
          (!f.q ||
            task.name
              .toLowerCase()
              .includes(String(f.q).trim().toLowerCase())) &&
          (!f.metric || task.metric === f.metric) &&
          (typeof f.enabled !== "boolean" || task.enabled === f.enabled),
      );
      total.value = tasks.length;
      rows.value = tasks.slice(
        (page.value - 1) * size.value,
        page.value * size.value,
      );
      return;
    }
    const r = await request<Row[]>(
      "/" +
        props.kind +
        query({
          ...f,
          offset: (page.value - 1) * size.value,
          limit: size.value,
        }),
      { signal },
    );
    const nextRows =
      props.kind === "predictions"
        ? await readPredictionTargets(r.data, auth.can, signal!)
        : r.data;
    if (signal?.aborted) return;
    rows.value = nextRows;
    if (props.kind === "llm-endpoints" && auth.can("resources:read"))
      llmBindings.value =
        await all<Models["LLMBindingResponse"]>("/llm-bindings");
    if (props.kind === "servers") {
      sampleController?.abort();
      sampleController = new AbortController();
      const signal = sampleController.signal;
      samples.value = {};
      void readServerSamples(r.data, signal).then((next) => {
        if (!signal.aborted) samples.value = next;
      });
    }
    total.value = r.total ?? r.data.length;
    if (props.kind === "containers") await containerWorkspace.value?.refresh();
  } catch (e) {
    if (signal?.aborted) return;
    error.value = e instanceof Error ? e.message : "加载失败";
    rows.value = [];
  } finally {
    if (!signal?.aborted) loading.value = false;
  }
}
function search() {
  page.value = 1;
  void load();
}
function edit(row: Row | null) {
  editing.value = row;
  formOpen.value = true;
}
async function action(work: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  try {
    await work();
    await load();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    busy.value = false;
  }
}
async function remove(row: Row) {
  await action(async () => {
    await ElMessageBox.confirm(
      props.kind === "containers"
        ? "仅退役平台资产记录；该操作不会停止或删除真实容器。"
        : props.kind === "reports"
          ? "删除后报告将从列表和事故报告记录中移除，修订、发送记录及审计仍会保留。"
          : "删除后记录将从普通列表移除，名称可能仍被保留。",
      "确认删除",
      { type: "warning" },
    );
    await api("/" + props.kind + "/" + row.id, "DELETE");
    ElMessage.success("已删除");
  });
}
async function apply() {
  await action(async () => {
    await ElMessageBox.confirm(
      "将保存的规则写入配置并重载本地 Prometheus。",
      "应用告警规则",
    );
    const r = await api<Row>("/monitoring/apply-rules", "POST");
    ElMessage.success(`已应用 ${r.applied} 条规则`);
  });
}
async function testSSH(row: Row) {
  await action(async () => {
    const r = await api<Row>(`/servers/${row.id}/test-ssh`, "POST");
    if (r.status !== "ok" && r.status !== "success")
      throw new Error(r.error_code || r.reason || r.status || "SSH 测试失败");
    ElMessage.success("SSH 连接正常");
  });
}
async function probe(row: Row) {
  await action(async () => {
    await ElMessageBox.confirm(
      "将发送真实推理探测请求，可能消耗 Provider token。",
      "手动探测",
    );
    const r = await api<Row>(`/llm-endpoints/${row.id}/probe`, "POST");
    if (!r.success) throw new Error(label(r.error_type));
    ElMessage.success("探测成功");
  });
}
async function run(row: Row) {
  await action(async () => {
    if (
      !(await confirmOperation(`手动运行预测任务“${row.name}”？`, "运行预测"))
    )
      return;
    const r = await api<Row>(`/prediction-tasks/${row.id}/run`, "POST");
    ElMessage.success("预测任务已提交，可查看运行进度");
    await router.push("/predictions/" + r.id);
  });
}
async function generate() {
  await action(async () => {
    if (!chosenIncident.value) throw new Error("请选择已关闭事故");
    const r = await api<Row>(
      `/incidents/${chosenIncident.value}/reports`,
      "POST",
      { force: false },
    );
    generateOpen.value = false;
    await router.push("/reports/" + r.id);
  });
}
async function searchKnowledge() {
  if (!knowledgeQuery.value.trim() || knowledgeSearching.value) return;
  knowledgeSearching.value = true;
  knowledgeSearched.value = false;
  searchResults.value = [];
  try {
    searchResults.value = await api<Row[]>(
      "/knowledge/search" +
        query({
          q: knowledgeQuery.value.trim(),
          tag: knowledgeTag.value.trim(),
          limit: 10,
        }),
    );
    knowledgeSearched.value = true;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "知识检索失败");
  } finally {
    knowledgeSearching.value = false;
  }
}
function nameOf(id: string) {
  return assignees.value.find((u) => u.id === id)?.username || short(id);
}
onMounted(async () => {
  if (knowledgeSearch.value) return;
  for (const key of [
    "incident_id",
    "approval_id",
    "execution_id",
    "report_id",
    "user_id",
    "task_id",
    "server_id",
    "llm_endpoint_id",
  ])
    if (route.query[key]) filters.value[key] = route.query[key];
  if (props.kind !== "predictions") await load();
  else if (filters.value.task_id) await load();
  try {
    if (["containers", "alerts", "incidents"].includes(props.kind))
      servers.value = await all("/servers");
    if (
      ["incidents", "approvals", "executions", "reports"].includes(props.kind)
    )
      assignees.value = await all("/incidents/assignees");
    if (["alerts", "incidents"].includes(props.kind))
      endpoints.value = await all("/llm-endpoints");
    if (props.kind === "reports" && auth.can("reports:run"))
      incidentOptions.value = await all("/incidents?status=closed");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "筛选选项加载失败";
  }
});
</script>
<template>
  <div class="list-workspace">
    <section class="page-header">
      <div class="page-heading">
        <div>
          <div class="heading-copy">
            <h1>{{ pageTitle }}</h1>
            <PageIntro />
          </div>
        </div>

        <div class="actions">
          <ExportCsvButton
            v-if="config.exportColumns && auth.can(config.read)"
            :filename="config.title"
            :columns="config.exportColumns"
            :load="exportRows"
            :disabled="
              loading || !!error || (kind === 'predictions' && !filters.task_id)
            "
          />
          <router-link
            v-if="kind === 'approvals' && auth.can('audit:read')"
            to="/audit-logs"
            ><el-button>审计日志</el-button></router-link
          >
          <router-link v-if="kind === 'predictions'" to="/prediction-tasks"
            ><el-button type="primary">管理预测任务</el-button></router-link
          >
          <div
            v-if="kind === 'incidents' || kind === 'approvals'"
            class="view-switch"
            aria-label="页面视图"
          >
            <button
              :class="{ active: view !== 'table' }"
              @click="view = kind === 'incidents' ? 'board' : 'workspace'"
            >
              {{ kind === "incidents" ? "看板" : "工作台" }}</button
            ><button
              :class="{ active: view === 'table' }"
              @click="view = 'table'"
            >
              列表
            </button>
          </div>
          <el-button
            v-if="!knowledgeSearch"
            :loading="loading || !!containerWorkspace?.loading"
            @click="refresh"
            >刷新</el-button
          ><el-button
            v-if="kind === 'alert-rules' && auth.can('rules:manage')"
            type="primary"
            :loading="busy"
            @click="apply"
            >应用规则</el-button
          ><el-button
            v-if="kind === 'reports' && auth.can('reports:run')"
            type="primary"
            @click="generateOpen = true"
            >生成报告</el-button
          ><el-button
            v-if="
              kind !== 'servers' &&
              !knowledgeSearch &&
              config.fields &&
              auth.can(config.manage!)
            "
            type="primary"
            @click="edit(null)"
            >新增</el-button
          >
          <el-dropdown
            v-if="kind === 'servers' && auth.can('resources:manage')"
            trigger="click"
          >
            <el-button type="primary">新增 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="edit(null)">
                  新增服务器
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="
                    !containerWorkspace?.canCreate || containerWorkspace?.loading
                  "
                  @click="containerWorkspace?.openCreate()"
                >
                  新建服务
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            v-if="kind === 'containers' && containerWorkspace?.canCreate"
            type="primary"
            :disabled="containerWorkspace?.loading"
            @click="containerWorkspace?.openCreate()"
            >新建服务</el-button
          >
        </div>
      </div>
    </section>
    <div class="workspace-content">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="error-box"
      />
      <ContainerServices
        v-if="kind === 'servers' || kind === 'containers'"
        ref="containerWorkspace"
        hide-refresh
        hide-create
        @synced="load"
        :view="kind === 'containers' ? 'containers' : 'services'"
        :server-id="
          kind === 'containers'
            ? String(filters.server_id || '') || undefined
            : undefined
        "
        :key="String(filters.server_id || '')"
      />
      <LLMStatusOverview
        v-if="kind === 'llm-endpoints'"
        :resources="rows"
      />
      <PredictionOverview
        v-if="kind === 'predictions'"
        :refresh-key="predictionRefresh"
        :task-id="String(route.query.task_id || '')"
        @task-change="selectPredictionTask"
      />
      <section v-if="knowledgeSearch" class="panel">
        <div class="filter-bar knowledge-search-bar">
          <el-input
            v-model="knowledgeQuery"
            class="knowledge-query"
            aria-label="知识检索问题"
            :maxlength="1000"
            clearable
            placeholder="搜索运维知识、SOP 或故障案例"
            @keyup.enter="searchKnowledge"
          /><el-input
            v-model="knowledgeTag"
            class="knowledge-tag"
            aria-label="知识标签"
            :maxlength="50"
            @keyup.enter="searchKnowledge"
            clearable
            placeholder="标签（选填）"
          />
          <div class="filter-actions">
            <el-button
              type="primary"
              :loading="knowledgeSearching"
              :disabled="!knowledgeQuery.trim()"
              @click="searchKnowledge"
              >检索</el-button
            >
          </div>
        </div>
        <div v-if="knowledgeSearched" class="panel-heading">
          <h2>检索结果</h2>
        </div>
        <div
          v-if="knowledgeSearched && !searchResults.length"
          class="empty-hint"
        >
          没有找到匹配的就绪知识片段
        </div>
        <div v-for="s in searchResults" :key="s.id" class="text-block">
          <router-link :to="'/knowledge/' + s.document_id"
            >{{ s.title }} · v{{ s.version }}</router-link
          >
          <p>{{ s.content }}</p>
          <div class="form-hint">
            来源 {{ s.source }} · 相似度 {{ Number(s.similarity).toFixed(3) }}
          </div>
        </div>
      </section>
      <component
        v-else
        :is="kind === 'containers' ? 'details' : 'section'"
        class="panel"
      >
        <summary
          v-if="kind === 'containers'"
          class="container-management-toggle"
        >
          容器清单管理 · 查看同步记录与退役容器
        </summary>
        <div v-if="kind === 'predictions'" class="panel-heading">
          <h2>历史预测记录</h2>
        </div>
        <div v-if="hasListFilters" class="filter-bar">
          <el-input
            v-if="config.search || kind === 'prediction-tasks'"
            v-model="filters.q"
            clearable
            :placeholder="
              kind === 'prediction-tasks'
                ? '搜索任务名称'
                : kind === 'audit-logs'
                  ? '搜索事故标题'
                  : '搜索名称 / 地址 / 镜像'
            "
            @keyup.enter="search"
          /><el-select
            v-if="kind === 'prediction-tasks'"
            v-model="filters.metric"
            clearable
            filterable
            placeholder="全部预测指标"
            ><el-option
              v-for="m in predictionMetrics"
              :key="m"
              :label="metricLabels[m] || m"
              :value="m"
          /></el-select>
          <el-select
            v-if="kind === 'prediction-tasks'"
            v-model="filters.enabled"
            clearable
            placeholder="全部调度状态"
            ><el-option label="已启用" :value="true" /><el-option
              label="已停用"
              :value="false"
          /></el-select>
          <el-select
            v-if="config.states"
            v-model="filters.status"
            clearable
            placeholder="全部状态"
            ><el-option
              v-for="s in config.states"
              :key="s"
              :label="label(s)"
              :value="s" /></el-select
          ><el-select
            v-if="servers.length"
            v-model="filters.server_id"
            clearable
            filterable
            placeholder="全部服务器"
            ><el-option
              v-for="s in servers"
              :key="s.id"
              :label="s.name"
              :value="s.id" /></el-select
          ><el-select
            v-if="endpoints.length"
            v-model="filters.llm_endpoint_id"
            clearable
            filterable
            placeholder="全部 LLM 服务"
            ><el-option
              v-for="s in endpoints"
              :key="s.id"
              :label="s.name"
              :value="s.id" /></el-select
          ><el-select
            v-if="['alerts', 'incidents'].includes(kind)"
            v-model="filters.severity"
            clearable
            placeholder="全部级别"
            ><el-option label="严重" value="critical" /><el-option
              label="警告"
              value="warning" /></el-select
          ><el-select
            v-if="assignees.length"
            v-model="filters[kind === 'incidents' ? 'assignee_id' : 'actor_id']"
            clearable
            filterable
            :placeholder="kind === 'incidents' ? '全部负责人' : '全部发起人'"
            ><el-option
              v-for="u in assignees"
              :key="u.id"
              :label="u.username"
              :value="u.id" /></el-select
          ><el-checkbox v-if="kind === 'incidents'" v-model="filters.unassigned"
            >未分配</el-checkbox
          ><el-select
            v-if="kind === 'alerts'"
            v-model="filters.acknowledged"
            clearable
            placeholder="全部确认状态"
            ><el-option label="已确认" :value="true" /><el-option
              label="未确认"
              :value="false" /></el-select
          ><el-select
            v-if="kind === 'audit-logs'"
            v-model="filters.action"
            clearable
            filterable
            placeholder="全部操作类型"
            ><el-option
              v-for="(name, action) in auditActions"
              :key="action"
              :value="action"
              :label="name" /></el-select
          ><el-input
            v-if="['approvals', 'executions', 'reports'].includes(kind)"
            v-model="filters.incident_id"
            clearable
            placeholder="事故 UUID（可从详情复制）"
          /><el-date-picker
            v-if="config.dateFilter"
            v-model="dates"
            type="datetimerange"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
          />
          <div class="filter-actions">
            <el-button type="primary" @click="search">查询</el-button
            ><el-button @click="resetFilters">重置</el-button>
          </div>
        </div>
        <IncidentBoard
          v-if="kind === 'incidents' && view === 'board'"
          :rows="rows"
          :assignees="assignees"
        />
        <ApprovalOverview
          v-if="kind === 'approvals' && view === 'workspace'"
          :rows="rows"
        />
        <p
          v-if="view === 'table' && kind !== 'audit-logs'"
          class="table-scroll-hint"
        >
          横向滚动可查看完整表格
        </p>
        <el-table
          v-if="view === 'table'"
          v-loading="loading"
          :data="rows"
          empty-text="暂无符合条件的记录"
          style="width: 100%"
          row-key="id"
          ><el-table-column
            v-if="kind === 'servers'"
            label="采集状态"
            width="100"
            ><template #default="{ row }"
              ><StatusTag
                :value="
                  samples[row.id]?.target_up === null ||
                  samples[row.id]?.target_up === undefined
                    ? 'unknown'
                    : samples[row.id]?.target_up === 1
                      ? 'healthy'
                      : 'unavailable'
                " /></template
          ></el-table-column>
          <el-table-column
            v-for="c in displayColumns"
            :key="c.key"
            :label="c.label"
            :min-width="c.width || 150"
            ><template #default="{ row }"
              ><router-link
                v-if="
                  kind === 'audit-logs' &&
                  c.key === 'incident_title' &&
                  auth.can('incidents:read')
                "
                :to="'/incidents/' + row.incident_id"
                >{{
                  auditIncidentName({ incident_title: row.incident_title })
                }}</router-link
              >
              <div
                v-else-if="kind === 'predictions' && c.key === 'target_name'"
              >
                <div>{{ row.target_name }}</div>
                <div class="muted">
                  任务：{{ row.config?.name || "未记录任务名称" }}
                </div>
              </div>
              <div
                v-else-if="kind === 'predictions' && c.key === 'conclusions'"
              >
                <div
                  v-for="outcome in predictionConclusions(row)"
                  :key="outcome.hours"
                >
                  <span>{{ outcome.hours }} 小时 · </span>
                  <StatusTag :value="outcome.value" :text="outcome.text" />
                  <div v-if="outcome.breachAt">
                    {{ date(outcome.breachAt) }}
                  </div>
                </div>
              </div>
              <StatusTag
                v-else-if="c.status"
                :text="c.format?.(row)"
                :value="
                  kind === 'approvals' && c.key === 'status'
                    ? effectiveApproval(row)
                    : row[c.key]
                "
              /><router-link
                v-else-if="config.detail && c.key === 'name'"
                :to="'/' + kind + '/' + row.id"
                class="resource-name"
                >{{ row.name
                }}<small v-if="kind === 'servers'"
                  >{{ row.host }} · {{ row.environment }}</small
                ></router-link
              ><template v-else>{{
                c.key === "assignee_id"
                  ? nameOf(row[c.key])
                  : c.format
                    ? c.format(row)
                    : (row[c.key] ?? "—")
              }}</template></template
            ></el-table-column
          ><el-table-column
            v-for="m in kind === 'servers'
              ? [
                  ['host_cpu_percent', 'CPU'],
                  ['host_memory_percent', '内存'],
                  ['host_disk_percent', '磁盘'],
                ]
              : []"
            :key="m[0]"
            :label="m[1]"
            min-width="150"
            ><template #default="{ row }"
              ><MetricBar :value="samples[row.id]?.[m[0]!]" /></template
          ></el-table-column>
          <el-table-column
            v-if="kind === 'llm-endpoints' && auth.can('resources:read')"
            label="关联业务服务 / 用途"
            min-width="240"
          >
            <template #default="{ row }">
              <div
                v-for="binding in llmBindings.filter(
                  (b) => b.llm_endpoint_id === row.id,
                )"
                :key="binding.id"
              >
                <router-link
                  v-if="auth.can('sources:read')"
                  :to="'/applications/' + binding.application_id"
                  >{{ binding.application_name }}</router-link
                >
                <span v-else>{{ binding.application_name }}</span>
                <div class="form-hint" style="white-space: pre-wrap">
                  {{ binding.purpose || "未填写用途" }}
                </div>
              </div>
              <span
                v-if="!llmBindings.some((b) => b.llm_endpoint_id === row.id)"
                class="muted"
                >未关联</span
              >
            </template>
          </el-table-column>
          <el-table-column label="操作" fixed="right" :width="actionColumnWidth"
            ><template #default="{ row }"
              ><div class="table-actions">
                <el-button
                  v-if="config.detail"
                  text
                  type="primary"
                  @click="router.push('/' + kind + '/' + row.id)"
                  >{{ kind === "predictions" ? "查看预测" : "详情" }}</el-button
                ><el-button
                  v-if="config.fields && auth.can(config.manage!)"
                  text
                  type="primary"
                  @click="edit(row)"
                  >编辑</el-button
                ><el-button
                  v-if="kind === 'servers' && auth.can('resources:manage')"
                  text
                  type="primary"
                  :disabled="busy"
                  @click="testSSH(row)"
                  >SSH 测试</el-button
                ><el-button
                  v-if="kind === 'llm-endpoints' && auth.can('llm:manage')"
                  text
                  type="primary"
                  @click="bindingEndpoint = row"
                  >关联服务</el-button
                ><el-button
                  v-if="kind === 'llm-endpoints' && auth.can('llm:probe')"
                  text
                  type="primary"
                  :disabled="busy"
                  @click="probe(row)"
                  >探测</el-button
                ><el-button
                  v-if="
                    kind === 'prediction-tasks' && auth.can('predictions:run')
                  "
                  text
                  type="primary"
                  :disabled="busy"
                  @click="run(row)"
                  >运行</el-button
                ><el-button
                  v-if="config.manage && auth.can(config.manage)"
                  text
                  type="danger"
                  :disabled="busy"
                  @click="remove(row)"
                  >删除</el-button
                ><el-button
                  v-if="kind === 'containers' && auth.can('resources:manage')"
                  text
                  type="danger"
                  @click="remove(row)"
                  >退役</el-button
                ><el-button
                  v-if="kind === 'audit-logs'"
                  text
                  type="primary"
                  @click="inspect = row"
                  >记录详情</el-button
                >
              </div></template
            ></el-table-column
          ></el-table
        >
        <div class="pagination">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="size"
            :total="total"
            :page-sizes="[10, 20, 50, 100]"
            layout="sizes, prev, pager, next"
            @current-change="load"
            @size-change="search"
          />
        </div>
      </component>
    </div>
  </div>
  <RecordForm
    v-if="config.fields"
    v-model="formOpen"
    :kind="kind"
    :row="editing"
    @saved="load"
  />
  <el-drawer
    :model-value="!!bindingEndpoint"
    :title="(bindingEndpoint?.name || '') + ' · 关联业务服务'"
    size="min(900px, 100%)"
    @update:model-value="bindingEndpoint = undefined"
  >
    <LLMServiceBindings
      v-if="bindingEndpoint"
      :key="bindingEndpoint.id"
      :endpoint-id="bindingEndpoint.id"
      @saved="load"
    />
  </el-drawer>
  <el-dialog
    :model-value="!!inspect"
    title="审计记录"
    width="760px"
    @update:model-value="inspect = null"
    ><AuditRecordDetails
      v-if="inspect"
      :key="inspect.id"
      :record="inspect as AuditRecord" /></el-dialog
  ><el-dialog v-model="generateOpen" title="生成事故复盘" width="520px"
    ><p class="muted">
      仅可为已关闭事故生成报告。存在运行中的诊断或执行时，后端会拒绝生成。
    </p>
    <el-select
      v-model="chosenIncident"
      filterable
      style="width: 100%"
      placeholder="选择已关闭事故"
      ><el-option
        v-for="i in incidentOptions"
        :key="i.id"
        :label="i.title + ' · ' + short(i.id)"
        :value="i.id" /></el-select
    ><template #footer
      ><el-button @click="generateOpen = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="generate"
        >生成报告</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.container-management-toggle {
  cursor: pointer;
  font-weight: 600;
}
details[open] > .container-management-toggle {
  margin-bottom: 20px;
}
</style>
