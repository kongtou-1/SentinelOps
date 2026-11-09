<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, onUnmounted, ref } from "vue";
import { api, all, query, type Models } from "@/api/client";
import { date, label, short } from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import {
  readServerSamples,
  type ServerSamples,
} from "@/features/resources/model/serverMetrics";
import type { Observation } from "@/features/applications/model/businessServices";
import {
  summarizeRuntime,
  summarizeDockerHealth,
  summarizeMonitors,
} from "@/features/applications/model/serviceHealth";
import { useAuth } from "@/features/auth/store";
import type { Row } from "@/types/records";
import StatusTag from "@/components/common/StatusTag.vue";
import MetricsPanel from "@/components/charts/MetricsPanel.vue";
import MetricBar from "@/components/charts/MetricBar.vue";
import AlertDistribution from "@/features/alerts/components/AlertDistribution.vue";
import {
  Refresh,
  DataLine,
  DocumentChecked,
  Clock,
} from "@element-plus/icons-vue";
const summary = ref<Models["DashboardSummary"]>(),
  alerts = ref<Row[]>([]),
  history = ref<Row[]>([]),
  incidents = ref<Row[]>([]),
  approvals = ref<Row[]>([]),
  risks = ref<Row[]>([]),
  servers = ref<Row[]>([]),
  samples = ref<ServerSamples>({}),
  error = ref(""),
  updated = ref(""),
  loading = ref(false),
  serverQuery = ref("");
const auth = useAuth();
const inspectionSummary = ref<Models["InspectionSummary"]>(),
  inspectionError = ref("");
const inspectionCards = [
  { key: "runs_total", label: "累计巡检", unit: "次" },
  { key: "items_total", label: "检查记录", unit: "条" },
  { key: "issues_total", label: "发现问题", unit: "条" },
  { key: "services_covered", label: "覆盖服务", unit: "个" },
  { key: "plans_enabled", label: "启用计划", unit: "个" },
  { key: "runs_active", label: "进行中巡检", unit: "次" },
] as const;
const applications = ref<Models["BusinessServiceResponse"][]>(),
  applicationContainers = ref<Models["ContainerResponse"][]>(),
  applicationObservations = ref<Observation[]>([]),
  applicationMonitors = ref<Models["MonitorResponse"][]>(),
  applicationError = ref(""),
  applicationsLoading = ref(false);
const applicationRows = computed(() =>
  (applications.value || [])
    .map((application) => {
      const ids = (applicationContainers.value || [])
        .filter((container) => container.application_id === application.id)
        .map((container) => container.id);
      const stats = summarizeRuntime(ids, applicationObservations.value);
      const docker = summarizeDockerHealth(ids, applicationObservations.value);
      const monitoring = summarizeMonitors(
        applicationMonitors.value?.filter(
          (monitor) => monitor.application_id === application.id,
        ),
      );
      const state =
        stats.status === "healthy"
          ? "healthy"
          : stats.status === "unknown"
            ? "unknown"
            : "unavailable";
      return { ...application, stats, state, docker, monitoring };
    })
    .sort(
      (a, b) =>
        ["unavailable", "unknown", "healthy"].indexOf(a.state) -
          ["unavailable", "unknown", "healthy"].indexOf(b.state) ||
        a.name.localeCompare(b.name),
    ),
);
const applicationStates = computed(() => ({
  healthy: applicationRows.value.filter((row) => row.state === "healthy")
    .length,
  unavailable: applicationRows.value.filter(
    (row) => row.state === "unavailable",
  ).length,
  unknown: applicationRows.value.filter((row) => row.state === "unknown")
    .length,
}));
const serverRows = computed(() =>
  servers.value.filter((r) =>
    (r.name + " " + r.host)
      .toLowerCase()
      .includes(serverQuery.value.toLowerCase()),
  ),
);
const riskCount = computed(
  () =>
    risks.value.filter(
      (r) =>
        r.fresh &&
        Object.values(r.horizons || {}).some((h: any) =>
          ["possible", "expected"].includes(h.risk?.level),
        ),
    ).length,
);
let controller: AbortController | undefined;
async function load() {
  loading.value = true;
  error.value = "";
  inspectionError.value = "";
  applicationError.value = "";
  applicationsLoading.value = true;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - 6);
  const result = await Promise.allSettled([
    api<Models["DashboardSummary"]>(
      "/dashboard/summary",
      "GET",
      undefined,
      signal,
    ),
    api<Row[]>("/alerts?status=firing&limit=10", "GET", undefined, signal),
    api<Row[]>("/incidents?limit=5", "GET", undefined, signal),
    api<Row[]>("/approvals?status=pending&limit=5", "GET", undefined, signal),
    api<Row[]>(
      "/predictions?status=completed&limit=10",
      "GET",
      undefined,
      signal,
    ),
    api<Row[]>("/servers?limit=5", "GET", undefined, signal),
    all<Row>(
      "/alerts" +
        query({ start: start.toISOString(), end: new Date().toISOString() }),
    ),
    all<Models["BusinessServiceResponse"]>("/business-services"),
    all<Models["ContainerResponse"]>("/containers"),
    api<Models["MonitorResponse"][]>(
      "/service-monitors",
      "GET",
      undefined,
      signal,
    ),
    auth.can("inspections:read")
      ? api<Models["InspectionSummary"]>(
          "/inspections/summary",
          "GET",
          undefined,
          signal,
        )
      : Promise.resolve(undefined),
  ]);
  if (signal.aborted) return;
  const setters = [
    (v: any) => (summary.value = v),
    (v: any) => (alerts.value = v),
    (v: any) => (incidents.value = v),
    (v: any) =>
      (approvals.value = v.filter(
        (a: Row) => new Date(a.expires_at).getTime() > Date.now(),
      )),
    (v: any) => (risks.value = v),
    (v: any) => (servers.value = v),
    (v: any) => (history.value = v),
    (v: any) => (applications.value = v),
    (v: any) => (applicationContainers.value = v),
    (v: any) => (applicationMonitors.value = v),
    (v: any) => (inspectionSummary.value = v),
  ];
  result.forEach((r, i) => {
    if (r.status === "fulfilled") setters[i]!(r.value);
    else if (i === 10) {
      inspectionSummary.value = undefined;
      inspectionError.value =
        r.reason instanceof Error ? r.reason.message : "巡检数据加载失败";
    } else if (i >= 7) {
      if (i === 7) applications.value = undefined;
      if (i === 8) applicationContainers.value = undefined;
      if (i === 9) applicationMonitors.value = undefined;
      applicationError.value =
        r.reason instanceof Error ? r.reason.message : "应用数据加载失败";
    } else
      error.value =
        r.reason instanceof Error ? r.reason.message : "部分数据加载失败";
  });
  updated.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const serverIds = [
    ...new Set(
      (applicationContainers.value || [])
        .filter((container) =>
          applications.value?.some(
            (app) => app.id === container.application_id,
          ),
        )
        .map((container) => container.server_id),
    ),
  ];
  const [next, overviews] = await Promise.all([
    readServerSamples(servers.value, signal),
    Promise.allSettled(
      serverIds.map((id) =>
        api<Models["ServerContainerOverview"]>(
          `/servers/${id}/container-overview`,
          "GET",
          undefined,
          signal,
        ),
      ),
    ),
  ]);
  if (signal.aborted) return;
  samples.value = next;
  const observedServers = new Set(
    overviews.flatMap((result, index) =>
      result.status === "fulfilled" ? [serverIds[index]!] : [],
    ),
  );
  if (applicationContainers.value)
    applicationContainers.value = [
      ...applicationContainers.value.filter(
        (container) => !observedServers.has(container.server_id),
      ),
      ...overviews.flatMap((result) =>
        result.status === "fulfilled" ? result.value.containers : [],
      ),
    ];
  applicationObservations.value = overviews.flatMap((result) =>
    result.status === "fulfilled" ? result.value.observations : [],
  );
  if (overviews.some((result) => result.status === "rejected"))
    applicationError.value =
      "部分服务器的实时状态读取失败，缺失状态的容器按未知处理。";
  applicationsLoading.value = false;
  loading.value = false;
}
const { refresh } = usePoll(load, () => 30000);
onUnmounted(() => controller?.abort());
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="heading-copy">
        <h1>总览</h1>
        <PageIntro />
      </div>
    </div>

    <div class="actions">
      <el-button :icon="Refresh" :loading="loading" @click="refresh"
        >刷新数据</el-button
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
  <section class="panel application-status" v-loading="applicationsLoading">
    <div class="panel-heading">
      <div>
        <h2>应用运行状态</h2>
      </div>
      <div class="actions">
        <template v-if="applications">
          <StatusTag
            :value="'healthy'"
            :text="`运行中 ${applicationStates.healthy}`"
          />
          <StatusTag
            :value="'unavailable'"
            :text="`运行异常 ${applicationStates.unavailable}`"
          />
          <StatusTag
            :value="'unknown'"
            :text="`运行未知 ${applicationStates.unknown}`"
          />
        </template>
        <router-link
          v-if="auth.can('sources:read')"
          to="/applications"
          class="subtle-link"
          >全部应用 →</router-link
        >
      </div>
    </div>
    <el-alert
      v-if="applicationError"
      :title="applicationError"
      type="warning"
      :closable="false"
      class="error-box"
    />
    <el-table
      :data="applicationRows"
      max-height="360"
      :empty-text="applications ? '尚未登记应用' : '应用数据暂不可用'"
    >
      <el-table-column label="应用" min-width="180">
        <template #default="{ row }">
          <router-link
            v-if="auth.can('sources:read')"
            :to="'/applications/' + row.id"
          >
            {{ row.name }}
          </router-link>
          <span v-else>{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="容器运行" min-width="150">
        <template #default="{ row }">
          <StatusTag
            :value="row.state"
            :text="
              row.state === 'healthy'
                ? '运行中'
                : row.state === 'unknown'
                  ? '状态未知'
                  : '运行异常'
            "
          />
          <div v-if="row.stats.reason">{{ row.stats.reason }}</div>
        </template>
      </el-table-column>
      <el-table-column label="运行数量" min-width="160">
        <template #default="{ row }">
          <template v-if="applicationContainers">
            {{ row.stats.running }}/{{ row.stats.total }} 个运行
            <span v-if="row.stats.unknown" class="muted">
              · {{ row.stats.unknown }} 个未知</span
            >
          </template>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="服务监测" min-width="220">
        <template #default="{ row }">
          <StatusTag
            :value="row.monitoring.status"
            :text="row.monitoring.text"
          />
          <div v-if="row.monitoring.reason">{{ row.monitoring.reason }}</div>
        </template>
      </el-table-column>
      <el-table-column label="Docker 检查" min-width="200">
        <template #default="{ row }">
          <StatusTag :value="row.docker.status" :text="row.docker.text" />
          <div v-if="row.docker.reason">{{ row.docker.reason }}</div>
        </template>
      </el-table-column>
      <el-table-column label="负责人" min-width="120">
        <template #default="{ row }">{{ row.owner || "—" }}</template>
      </el-table-column>
    </el-table>
  </section>
  <div class="two-columns dashboard-charts">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>系统健康度</h2>
          <small>近 24 小时综合评分</small>
        </div>
        <span class="placeholder-label">待接入</span>
      </div>
      <div class="placeholder-state placeholder-grid">
        <el-icon><DataLine /></el-icon><strong>综合健康评分待接入</strong
        ><span>资源监控已就绪，评分规则配置后展示健康趋势</span
        ><router-link to="/servers">查看实时资源 →</router-link>
      </div>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>告警等级分布</h2>
          <small>近 7 天新增 · {{ history.length }} 条</small>
        </div>
        <router-link to="/alerts" class="subtle-link">查看全部 →</router-link>
      </div>
      <AlertDistribution :rows="history" />
    </section>
  </div>
  <div class="two-columns dashboard-columns">
    <div class="dashboard-column">
      <section
        v-if="auth.can('inspections:read')"
        class="panel inspection-panel"
      >
        <div class="panel-heading">
          <h2>巡检Agent</h2>
          <router-link to="/inspections" class="subtle-link"
            >查看巡检 →</router-link
          >
        </div>
        <el-alert
          v-if="inspectionError"
          :title="inspectionError"
          type="warning"
          :closable="false"
          class="error-box"
        />
        <div class="inspection-summary" role="region" aria-label="巡检统计">
          <div
            v-for="card in inspectionCards"
            :key="card.key"
            class="inspection-stat"
            :class="{
              'inspection-issues': card.key === 'issues_total',
              'inspection-active': card.key === 'runs_active',
            }"
          >
            <div class="stat-title">{{ card.label }}</div>
            <div class="stat-value">
              {{ inspectionSummary?.[card.key]?.toLocaleString() ?? "—" }}
              <small>{{ card.unit }}</small>
            </div>
          </div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>系统状态</h2>
            <small>最近创建的 {{ servers.length }} 台服务器 · 即时指标</small>
          </div>
          <el-input
            v-model="serverQuery"
            placeholder="搜索名称 / IP"
            clearable
            style="width: 220px"
          />
        </div>
        <el-table :data="serverRows" empty-text="尚未接入服务器"
          ><el-table-column label="状态" width="110"
            ><template #default="{ row }"
              ><StatusTag
                :value="
                  samples[row.id]?.target_up === null ||
                  samples[row.id]?.target_up === undefined
                    ? 'unknown'
                    : samples[row.id]?.target_up === 1
                      ? 'healthy'
                      : 'unavailable'
                " /></template></el-table-column
          ><el-table-column label="名称" min-width="180"
            ><template #default="{ row }"
              ><router-link :to="'/servers/' + row.id" class="resource-name"
                >{{ row.name
                }}<small
                  >{{ row.host }} · {{ row.environment }}</small
                ></router-link
              ></template
            ></el-table-column
          ><el-table-column
            v-for="m in [
              ['host_cpu_percent', 'CPU'],
              ['host_memory_percent', '内存'],
              ['host_disk_percent', '磁盘'],
            ]"
            :key="m[0]"
            :label="m[1]"
            min-width="140"
            ><template #default="{ row }"
              ><MetricBar
                :value="samples[row.id]?.[m[0]!]" /></template></el-table-column
        ></el-table>
        <div class="panel-heading" style="margin: 16px 0 0">
          <small>更新于 {{ updated || "—" }} · 每 30 秒刷新</small
          ><router-link to="/servers" class="subtle-link"
            >全部资源 →</router-link
          >
        </div>
      </section>
      <MetricsPanel kind="servers" />
      <section class="panel">
        <div class="panel-heading">
          <h2>当前告警</h2>
          <router-link to="/alerts" class="subtle-link">全部告警 →</router-link>
        </div>
        <div v-if="!alerts.length" class="empty-hint">当前没有触发中的告警</div>
        <div v-for="a in alerts" :key="a.id" class="summary-list">
          <router-link :to="'/alerts/' + a.id">{{
            a.annotations?.summary || a.labels?.alertname
          }}</router-link
          ><StatusTag :value="a.severity" />
        </div>
      </section>
    </div>
    <div class="dashboard-column">
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>待办事项</h2>
            <small>有效审批与待跟进事项</small>
          </div>
        </div>
        <div v-for="a in approvals" :key="a.id" class="todo-item">
          <el-icon><DocumentChecked /></el-icon>
          <div>
            <router-link :to="'/approvals/' + a.id"
              >审批重启 ·
              {{ a.container_name || short(a.container_id) }}</router-link
            ><small
              >{{ a.actor_username }} 申请 ·
              {{ date(a.expires_at) }} 到期</small
            >
          </div>
        </div>
        <div v-if="!approvals.length" class="empty-hint">
          <el-icon><DocumentChecked /></el-icon>暂无有效待审批事项
        </div>
        <router-link to="/approvals" class="subtle-link"
          >查看全部审批 →</router-link
        >
        <div class="todo-placeholder">
          <el-icon><Clock /></el-icon> 复盘改进待办
          <span class="placeholder-label">待接入</span
          ><br />改进任务的负责人、期限与完成状态将在此汇总。
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>事故焦点</h2>
            <small>最近事故与处理进展</small>
          </div>
          <router-link to="/incidents" class="subtle-link"
            >事件看板 →</router-link
          >
        </div>
        <div v-if="!incidents.length" class="empty-hint">暂无事故</div>
        <router-link
          v-for="i in incidents"
          :key="i.id"
          :to="'/incidents/' + i.id"
          class="event-card"
          ><div class="event-meta">
            <StatusTag :value="i.severity" /><StatusTag :value="i.status" />
          </div>
          <h3>{{ i.title }}</h3>
          <small
            >{{ short(i.id) }} · {{ date(i.started_at) }}</small
          ></router-link
        >
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>预测风险</h2>
          <router-link to="/predictions" class="subtle-link"
            >预测中心 →</router-link
          >
        </div>
        <div v-if="!risks.length" class="empty-hint">暂无已完成的预测</div>
        <div v-for="r in risks.slice(0, 3)" :key="r.id" class="text-block">
          <router-link :to="'/predictions/' + r.id"
            >结果 {{ short(r.id) }}</router-link
          >
          <div class="badge-line" style="margin: 10px 0 0">
            <template v-for="(h, k) in r.horizons" :key="k"
              ><small>{{ k }}h</small
              ><StatusTag :value="r.fresh ? h.risk?.level : 'stale'"
            /></template>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.inspection-panel {
  padding: 14px 20px;
}
.inspection-panel .panel-heading {
  margin-bottom: 10px;
}
.inspection-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px 16px;
}
.inspection-stat .stat-title {
  color: var(--muted);
  font-size: 12px;
  line-height: 20px;
}
.inspection-stat .stat-value {
  font-size: 24px;
  font-weight: 500;
  line-height: 32px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.inspection-stat .stat-value small {
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
}
.inspection-issues .stat-value {
  color: var(--el-color-danger);
}
.inspection-active .stat-value {
  color: var(--el-color-primary);
}
@media (max-width: 1200px) {
  .inspection-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 700px) {
  .inspection-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
