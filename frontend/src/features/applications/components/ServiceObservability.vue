<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, all, download, query, type Models } from "@/api/client";
import type { Row } from "@/types/records";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import {
  summarizeRuntime,
  summarizeDockerHealth,
  summarizeMonitors,
} from "@/features/applications/model/serviceHealth";
import { date, metricValue, number, short } from "@/utils/display";
import { metricLabels } from "@/config/metrics";
import { monitoringReason, parseTimeWindow } from "@/utils/monitoring";
import StatusTag from "@/components/common/StatusTag.vue";
import LLMServiceBindings from "@/features/applications/components/LLMServiceBindings.vue";
import MonitorTokenAuth from "./MonitorTokenAuth.vue";
import ServiceDatabases from "./ServiceDatabases.vue";
import MetricChart, {
  type MetricData,
} from "@/components/charts/MetricChart.vue";

const props = defineProps<{
  applicationId: string;
  containerId?: string;
  containerName?: string;
  hideRefresh?: boolean;
}>();
const auth = useAuth(),
  route = useRoute(),
  router = useRouter();
const llmBindings = ref<InstanceType<typeof LLMServiceBindings>>();
const data = ref<Models["ObservabilityResponse"]>(),
  error = ref(""),
  busy = ref(false);
const selected = ref(String(route.query.monitor || "")),
  hours = ref(24),
  tab = ref("apis");
const window = ref(parseTimeWindow(route.query.start, route.query.end));
const chartData = ref<Record<string, MetricData>>({}),
  chartErrors = ref<Record<string, string>>({});
const counters = ref<Record<string, number | null>>({});
const metrics = [
  "service_probe_latency_seconds",
  "service_probe_success_rate",
  "service_request_rate",
  "service_error_rate",
  "service_latency_p95_seconds",
];
const monitors = computed(() =>
  props.containerId
    ? data.value?.monitors.filter((m) => m.container_id === props.containerId)
    : data.value?.monitors,
);
const containers = computed(() =>
  (data.value?.containers || []).filter(
    (container) => !props.containerId || container.id === props.containerId,
  ),
);
const current = computed(() =>
  monitors.value?.find((m) => m.id === selected.value),
);
const runtime = computed(() =>
  summarizeRuntime(
    containers.value.map((c) => c.id),
    containers.value.map((c) => ({ ...c, container_id: c.id })),
  ),
);
const docker = computed(() =>
  summarizeDockerHealth(
    containers.value.map((c) => c.id),
    containers.value.map((c) => ({ ...c, container_id: c.id })),
  ),
);
const monitoring = computed(() => summarizeMonitors(monitors.value));
const servers = ref<Row[]>([]),
  dialog = ref(false),
  form = ref<Row>({}),
  editing = ref("");
const headers = ref(""),
  body = ref(""),
  assertions = ref("{}"),
  clearRequest = ref(false);
const tokenAuth = ref<InstanceType<typeof MonitorTokenAuth>>();
const authEditorVersion = ref(0),
  testing = ref(false);
const draftResult = ref<Models["MonitorTestResponse"]>();
watch(
  [form, headers, body, assertions, clearRequest],
  () => {
    draftResult.value = undefined;
  },
  { deep: true },
);
const trafficDialog = ref(false),
  trafficTarget = ref("");
const names = {
  healthy: "正常",
  failed: "异常",
  unknown: "未知",
  stale: "过期",
  paused: "暂停",
} as Record<string, string>;
const sourceName = (id?: string | null) =>
  id
    ? servers.value.find((s) => s.id === id)?.name ||
      data.value?.containers.find((c) => c.server_id === id)?.server_name ||
      "主机视角"
    : "平台视角";
const links = computed(
  () =>
    window.value || {
      start: new Date(Date.now() - hours.value * 3600000).toISOString(),
      end: new Date().toISOString(),
    },
);
let chartVersion = 0;
async function load() {
  try {
    const result = await api<Models["ObservabilityResponse"]>(
      `/business-services/${props.applicationId}/observability`,
    );
    data.value = result;
    if (!monitors.value?.some((m) => m.id === selected.value))
      selected.value = monitors.value?.[0]?.id || "";
    error.value = "";
  } catch (e) {
    data.value = undefined;
    error.value = e instanceof Error ? e.message : "服务监测加载失败";
  }
}
const { refresh } = usePoll(load, () => 15000);
async function charts() {
  const id = selected.value,
    version = ++chartVersion;
  if (!id) {
    chartData.value = {};
    counters.value = {};
    return;
  }
  const range = window.value || {
    start: new Date(Date.now() - hours.value * 3600000).toISOString(),
    end: new Date().toISOString(),
  };
  const step = Math.max(
    15,
    Math.ceil(
      (Date.parse(range.end) - Date.parse(range.start)) / 1000 / 1000 / 15,
    ) * 15,
  );
  const results = await Promise.allSettled(
    metrics.map((metric) =>
      api<MetricData>(
        `/service-monitors/${id}/metrics` + query({ metric, ...range, step }),
      ),
    ),
  );
  const counts = await Promise.allSettled(
    ["service_request_count", "service_probe_success_rate"].map((metric) =>
      api<MetricData>(`/service-monitors/${id}/metrics` + query({ metric })),
    ),
  );
  if (version !== chartVersion || id !== selected.value) return;
  chartData.value = {};
  chartErrors.value = {};
  counters.value = {};
  results.forEach((r, i) => {
    const key = metrics[i]!;
    if (r.status === "fulfilled") chartData.value[key] = r.value;
    else
      chartErrors.value[key] =
        r.reason instanceof Error ? r.reason.message : "查询失败";
  });
  counts.forEach((r, i) => {
    const key = ["service_request_count", "service_probe_success_rate"][i]!;
    counters.value[key] =
      r.status === "fulfilled"
        ? metricValue(key, r.value.result?.[0]?.value?.[1])
        : null;
  });
}
const { refresh: refreshCharts } = usePoll(charts, () =>
  selected.value ? 30000 : null,
);
watch(selected, () => {
  chartData.value = {};
  counters.value = {};
  void refreshCharts();
});
watch(
  () => [route.query.start, route.query.end],
  () => {
    window.value = parseTimeWindow(route.query.start, route.query.end);
    void refreshCharts();
  },
);
async function changeRange() {
  const end = new Date();
  await router.replace({
    query: {
      ...route.query,
      start: new Date(end.getTime() - hours.value * 3600000).toISOString(),
      end: end.toISOString(),
    },
  });
}
async function open(monitor?: Row) {
  editing.value = monitor?.id || "";
  form.value = {
    name: "",
    protocol: "http",
    target: "",
    method: "GET",
    route: "",
    container_id: props.containerId || null,
    probe_server_id: null,
    dns_server: "",
    importance: "critical",
    expected_status: 200,
    interval_seconds: 60,
    timeout_seconds: 5,
    enabled: true,
    safe_post: false,
    ...(monitor || {}),
  };
  headers.value = body.value = "";
  assertions.value = JSON.stringify(monitor?.assertions || {}, null, 2);
  clearRequest.value = false;
  authEditorVersion.value++;
  draftResult.value = undefined;
  dialog.value = true;
  try {
    servers.value = await all<Row>("/servers");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "服务器加载失败");
  }
}
function monitorPayload() {
  const keys = [
    "name",
    "protocol",
    "target",
    "method",
    "route",
    "container_id",
    "probe_server_id",
    "dns_server",
    "importance",
    "expected_status",
    "interval_seconds",
    "timeout_seconds",
    "enabled",
    "safe_post",
  ];
  const payload = Object.fromEntries(
    keys.map((k) => [k, form.value[k] ?? null]),
  );
  payload.container_id = props.containerId || form.value.container_id || null;
  payload.probe_server_id = form.value.probe_server_id || null;
  const platformHttp =
    form.value.protocol === "http" && !payload.probe_server_id;
  payload.assertions = platformHttp ? JSON.parse(assertions.value || "{}") : {};
  if (form.value.protocol !== "http") {
    payload.method = "GET";
    payload.route = "";
    payload.safe_post = false;
  }
  if (payload.probe_server_id && payload.method === "POST") {
    payload.method = "GET";
    payload.safe_post = false;
  }
  if (form.value.protocol !== "dns" || !payload.probe_server_id)
    payload.dns_server = "";
  if (clearRequest.value) {
    payload.headers = {};
    payload.request_body = {};
    payload.token_auth = false;
  } else if (platformHttp) {
    const authConfig = tokenAuth.value?.configuration();
    if (authConfig !== undefined) payload.token_auth = authConfig;
    if (headers.value.trim()) payload.headers = JSON.parse(headers.value);
    if (payload.method === "POST") {
      if (body.value.trim()) payload.request_body = JSON.parse(body.value);
    } else payload.request_body = {};
  }
  return payload;
}
async function testDraft() {
  testing.value = true;
  draftResult.value = undefined;
  try {
    draftResult.value = await api<Models["MonitorTestResponse"]>(
      "/service-monitors/test",
      "POST",
      { ...monitorPayload(), monitor_id: editing.value || null },
    );
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "测试失败");
  } finally {
    testing.value = false;
  }
}
async function save() {
  busy.value = true;
  try {
    const payload = monitorPayload();
    const saved = await api<Models["MonitorResponse"]>(
      editing.value
        ? `/service-monitors/${editing.value}`
        : `/business-services/${props.applicationId}/monitors`,
      editing.value ? "PUT" : "POST",
      payload,
    );
    selected.value = saved.id;
    dialog.value = false;
    await refresh();
    await refreshCharts();
    ElMessage.success("监测配置已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
async function probe(id: string) {
  busy.value = true;
  try {
    await api(`/service-monitors/${id}/probe`, "POST");
    await refresh();
    await refreshCharts();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "探测失败");
  } finally {
    busy.value = false;
  }
}
async function remove(monitor: Row) {
  try {
    await ElMessageBox.confirm(`删除监测“${monitor.name}”？`, "删除监测", {
      type: "warning",
    });
    await api(`/service-monitors/${monitor.id}`, "DELETE");
    ElMessage.success("监测已删除");
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "删除失败");
  }
}
async function saveTraffic() {
  busy.value = true;
  try {
    await api(`/business-services/${props.applicationId}/traffic`, "PUT", {
      traffic_target: trafficTarget.value.trim() || null,
    });
    trafficDialog.value = false;
    await refresh();
    await refreshCharts();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
async function probeConfig(id: string) {
  try {
    await download(`/servers/${id}/probe-config`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "下载失败");
  }
}
async function refreshAll() {
  await refresh();
  await Promise.all([refreshCharts(), llmBindings.value?.refresh()]);
}
defineExpose({ refresh: refreshAll });
</script>

<template>
  <section class="panel service-observability">
    <div
      v-if="containerId || !hideRefresh || auth.can('resources:manage')"
      class="panel-heading"
    >
      <h2 v-if="containerId">主动监测</h2>
      <div class="actions">
        <router-link
          v-if="containerId && auth.can('sources:read')"
          :to="{ path: '/applications/' + applicationId, query: links }"
          >所属服务</router-link
        >
        <el-button v-if="!hideRefresh" @click="refreshAll">刷新</el-button
        ><el-button
          v-if="!containerId && auth.can('resources:manage')"
          @click="
            trafficTarget = data?.traffic_target || '';
            trafficDialog = true;
          "
          >真实流量接入</el-button
        ><el-button
          v-if="auth.can('resources:manage')"
          type="primary"
          @click="open()"
          >添加监测</el-button
        >
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="data" class="cards" :class="{ 'three-cards': containerId }">
      <div class="stat-card">
        <div class="stat-title">
          {{ containerId ? "主动监测" : "服务监测 · 关键 API 与网络" }}
        </div>
        <StatusTag :value="monitoring.status" :text="monitoring.text" />
        <div>{{ monitoring.reason }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">容器运行</div>
        <StatusTag
          :value="runtime.status"
          :text="
            runtime.status === 'healthy' ? '运行中' : names[runtime.status]
          "
        />
        <div>{{ runtime.running }}/{{ runtime.total }} 个运行</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Docker 检查</div>
        <StatusTag :value="docker.status" :text="docker.text" />
        <div>{{ docker.reason }}</div>
      </div>
      <div v-if="!containerId" class="stat-card">
        <div class="stat-title">真实流量采集</div>
        <el-tag :type="data.traffic_target ? 'info' : 'warning'">{{
          data.traffic_target ? "采集已配置" : "未接入"
        }}</el-tag>
      </div>
    </div>
    <el-tabs class="workspace-tabs" v-model="tab">
      <el-tab-pane
        :label="containerId ? '检查项' : '关键 API 与网络'"
        name="apis"
      >
        <el-table
          :data="monitors || []"
          empty-text="未配置主动监测"
          highlight-current-row
          @row-click="(row: Row) => (selected = row.id)"
        >
          <el-table-column label="监测" min-width="160"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="selected = row.id">{{
                row.name
              }}</el-button
              ><el-tag v-if="row.importance === 'critical'" size="small"
                >关键</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column v-if="!containerId" label="目标容器" min-width="170">
            <template #default="{ row }">
              <router-link
                v-if="row.container_id"
                :to="{ path: '/containers/' + row.container_id, query: links }"
              >
                {{
                  data?.containers.find((c) => c.id === row.container_id)
                    ?.name || short(row.container_id)
                }}
              </router-link>
              <span v-else>服务整体</span>
            </template>
          </el-table-column>
          <el-table-column label="目标" min-width="200" show-overflow-tooltip
            ><template #default="{ row }"
              >{{ row.protocol.toUpperCase() }} · {{ row.target }}</template
            ></el-table-column
          >
          <el-table-column label="探测来源" min-width="130"
            ><template #default="{ row }">{{
              sourceName(row.probe_server_id)
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="85"
            ><template #default="{ row }"
              ><StatusTag
                :value="row.status"
                :text="names[row.status]" /></template
          ></el-table-column>
          <el-table-column label="最近耗时" width="110"
            ><template #default="{ row }">{{
              row.last_latency_seconds == null
                ? "—"
                : number(row.last_latency_seconds * 1000) + " ms"
            }}</template></el-table-column
          >
          <el-table-column label="最近探测" width="165"
            ><template #default="{ row }">{{
              date(row.last_probe_at)
            }}</template></el-table-column
          >
          <el-table-column
            v-if="auth.can('containers:diagnose')"
            label="操作"
            width="180"
            ><template #default="{ row }"
              ><el-button
                link
                :disabled="busy || !row.enabled"
                @click.stop="probe(row.id)"
                >探测</el-button
              ><el-button
                v-if="auth.can('resources:manage')"
                link
                @click.stop="open(row)"
                >编辑</el-button
              ><el-button
                v-if="auth.can('resources:manage')"
                link
                type="danger"
                @click.stop="remove(row)"
                >删除</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <template v-if="current">
          <div class="panel-heading metric-toolbar">
            <h3>{{ current.name }}</h3>
            <el-select
              v-model="hours"
              aria-label="监测时间范围"
              style="width: 140px"
              @change="changeRange"
              ><el-option
                v-for="h in [1, 6, 24, 168]"
                :key="h"
                :label="h === 168 ? '近 7 天' : '近 ' + h + ' 小时'"
                :value="h"
            /></el-select>
          </div>
          <dl class="detail-grid">
            <div>
              <dt>最近成功</dt>
              <dd>{{ date(current.last_success_at) }}</dd>
            </div>
            <div>
              <dt>最近失败</dt>
              <dd>{{ date(current.last_failure_at) }}</dd>
            </div>
            <div>
              <dt>失败类型</dt>
              <dd>
                {{
                  monitoringReason(current.last_error_code || undefined) || "—"
                }}
              </dd>
            </div>
            <div>
              <dt>HTTP 状态</dt>
              <dd>{{ current.last_http_status ?? "—" }}</dd>
            </div>
            <div>
              <dt>探测成功率 · 5 分钟</dt>
              <dd>
                {{
                  counters.service_probe_success_rate == null
                    ? "无样本"
                    : number(counters.service_probe_success_rate) + "%"
                }}
              </dd>
            </div>
            <div>
              <dt>真实请求样本 · 5 分钟</dt>
              <dd>
                {{
                  counters.service_request_count == null
                    ? "无样本"
                    : number(counters.service_request_count, 0)
                }}
              </dd>
            </div>
          </dl>
          <div
            v-if="current.probe_server_id && auth.can('resources:manage')"
            class="actions"
          >
            <el-button @click="probeConfig(current.probe_server_id!)"
              >下载主机探测配置</el-button
            >
          </div>
          <h3>主动探测</h3>
          <div class="two-equal">
            <section
              v-for="metric in metrics.slice(0, 2)"
              :key="metric"
              class="monitor-chart"
            >
              <h4>{{ metricLabels[metric] }}</h4>
              <el-alert
                v-if="chartErrors[metric]"
                :title="chartErrors[metric]"
                type="error"
                :closable="false"
              /><MetricChart
                v-else
                :data="chartData[metric]"
                :metric="metric"
                compact
              />
            </section>
          </div>
          <h3>真实业务请求</h3>
          <div class="cards three-cards">
            <section
              v-for="metric in metrics.slice(2)"
              :key="metric"
              class="monitor-chart"
            >
              <h4>{{ metricLabels[metric] }}</h4>
              <el-alert
                v-if="chartErrors[metric]"
                :title="chartErrors[metric]"
                type="error"
                :closable="false"
              /><MetricChart
                v-else
                :data="chartData[metric]"
                :metric="metric"
                compact
              />
            </section>
          </div>
        </template>
      </el-tab-pane>
      <el-tab-pane
        v-if="!containerId && auth.can('llm:read')"
        label="LLM 服务"
        name="llm"
      >
        <LLMServiceBindings
          v-if="tab === 'llm'"
          ref="llmBindings"
          :key="applicationId"
          :application-id="applicationId"
          @saved="refresh"
        />
      </el-tab-pane>
      <el-tab-pane v-if="!containerId" label="运行实例" name="instances"
        ><el-table :data="data?.containers || []"
          ><el-table-column label="容器"
            ><template #default="{ row }"
              ><router-link
                :to="{ path: '/containers/' + row.id, query: links }"
                >{{ row.name }}</router-link
              ></template
            ></el-table-column
          ><el-table-column label="主机"
            ><template #default="{ row }"
              ><router-link
                :to="{ path: '/servers/' + row.server_id, query: links }"
                >{{ row.server_name }}</router-link
              ></template
            ></el-table-column
          ><el-table-column label="运行状态"
            ><template #default="{ row }"
              ><StatusTag
                :value="row.state || 'unknown'" /></template></el-table-column
          ><el-table-column label="Docker 健康检查"
            ><template #default="{ row }"
              ><StatusTag
                :value="row.health || 'unknown'" /></template></el-table-column
          ><el-table-column label="CPU 占主机"
            ><template #default="{ row }">{{
              row.cpu_host_percent == null
                ? "—"
                : number(row.cpu_host_percent) + "%"
            }}</template></el-table-column
          ><el-table-column label="内存"
            ><template #default="{ row }">{{
              row.memory_bytes == null
                ? "—"
                : number(row.memory_bytes / 1024 ** 2) + " MiB"
            }}</template></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane v-if="!containerId" label="依赖与影响" name="dependencies"
        ><div class="two-equal">
          <section
            v-for="group in [
              { title: '依赖服务', items: data?.dependencies },
              { title: '依赖本服务', items: data?.dependents },
            ]"
            :key="group.title"
          >
            <h3>{{ group.title }}</h3>
            <div
              v-for="item in group.items || []"
              :key="item.id"
              class="dependency-row"
            >
              <router-link
                :to="{ path: '/applications/' + item.id, query: links }"
                >{{ item.name }}</router-link
              ><StatusTag :value="item.status" :text="names[item.status]" />
            </div>
            <el-empty
              v-if="!group.items?.length"
              :image-size="48"
              description="未登记依赖"
            />
          </section>
        </div>
        <h3>LLM 依赖</h3>
        <ServiceDatabases :application-id="applicationId" summary />
        <div
          v-for="endpoint in data?.llm_endpoints || []"
          :key="endpoint.id"
          class="dependency-row"
        >
          <router-link
            :to="{ path: '/llm-endpoints/' + endpoint.id, query: links }"
            >{{ endpoint.name }}</router-link
          ><StatusTag :value="endpoint.status" /></div
      ></el-tab-pane>
      <el-tab-pane v-if="!containerId" label="告警与事故" name="incidents"
        ><div class="two-equal">
          <section>
            <h3>关联告警</h3>
            <div
              v-for="alert in data?.alerts || []"
              :key="alert.id"
              class="dependency-row"
            >
              <router-link :to="'/alerts/' + alert.id">{{
                alert.name
              }}</router-link
              ><StatusTag :value="alert.status" />
            </div>
            <el-empty
              v-if="!data?.alerts.length"
              :image-size="48"
              description="暂无告警"
            />
          </section>
          <section>
            <h3>关联事故</h3>
            <div
              v-for="incident in data?.incidents || []"
              :key="incident.id"
              class="dependency-row"
            >
              <router-link :to="'/incidents/' + incident.id">{{
                incident.title
              }}</router-link
              ><StatusTag :value="incident.status" />
            </div>
            <el-empty
              v-if="!data?.incidents.length"
              :image-size="48"
              description="暂无事故"
            />
          </section></div
      ></el-tab-pane>
    </el-tabs>
  </section>
  <el-dialog
    v-model="dialog"
    :title="editing ? '编辑监测' : '添加监测'"
    width="680px"
    destroy-on-close
    ><el-form label-width="145px" :disabled="busy || testing">
      <el-form-item label="名称" required
        ><el-input v-model="form.name" maxlength="100"
      /></el-form-item>
      <div class="two-equal">
        <el-form-item label="协议" required
          ><el-select v-model="form.protocol"
            ><el-option
              v-for="p in ['http', 'tcp', 'dns', 'icmp']"
              :key="p"
              :label="p.toUpperCase()"
              :value="p" /></el-select></el-form-item
        ><el-form-item label="重要性" required
          ><el-select v-model="form.importance"
            ><el-option label="关键" value="critical" /><el-option
              label="辅助"
              value="optional" /></el-select
        ></el-form-item>
      </div>
      <el-form-item label="目标" required
        ><el-input
          v-model="form.target"
          :placeholder="
            form.protocol === 'http'
              ? 'https://service/api/health'
              : form.protocol === 'tcp'
                ? 'service:443'
                : 'service.example.com'
          "
      /></el-form-item>
      <el-form-item label="目标容器"
        ><el-tag v-if="containerId">{{ containerName || containerId }}</el-tag>
        <el-select
          v-else
          v-model="form.container_id"
          clearable
          filterable
          placeholder="服务整体"
          ><el-option
            v-for="c in data?.containers || []"
            :key="c.id"
            :label="c.name"
            :value="c.id" /></el-select
      ></el-form-item>
      <el-form-item label="探测来源"
        ><el-select
          v-model="form.probe_server_id"
          clearable
          placeholder="平台视角"
          ><el-option
            v-for="s in servers"
            :key="s.id"
            :label="s.name"
            :value="s.id"
            :disabled="!s.blackbox_exporter_target" /></el-select
      ></el-form-item>
      <el-form-item
        v-if="form.protocol === 'dns' && form.probe_server_id"
        label="DNS 服务器"
        required
        ><el-input v-model="form.dns_server" placeholder="192.168.1.1:53"
      /></el-form-item>
      <el-form-item v-if="form.request_configured" label="加密请求配置">
        <el-tag>已有配置</el-tag>
        <el-checkbox v-model="clearRequest">清除配置</el-checkbox>
      </el-form-item>
      <template v-if="form.protocol === 'http'"
        ><div class="two-equal">
          <el-form-item label="请求方法" required
            ><el-select v-model="form.method"
              ><el-option
                v-for="m in form.probe_server_id
                  ? ['GET', 'HEAD']
                  : ['GET', 'HEAD', 'POST']"
                :key="m"
                :label="m"
                :value="m" /></el-select></el-form-item
          ><el-form-item label="期望状态码" required
            ><el-input-number
              v-model="form.expected_status"
              :min="100"
              :max="599"
          /></el-form-item>
        </div>
        <el-form-item label="业务路由模板"
          ><el-input v-model="form.route" placeholder="/api/questions/{id}"
        /></el-form-item>
        <template v-if="!form.probe_server_id"
          ><el-form-item label="JSON 字段断言"
            ><el-input
              v-model="assertions"
              type="textarea"
              :rows="3"
              placeholder='{"ok": true}' /></el-form-item
          ><el-form-item label="请求头 JSON"
            ><el-input
              v-model="headers"
              type="textarea"
              :rows="2"
              autocomplete="off" /></el-form-item
          ><MonitorTokenAuth
            v-if="!clearRequest"
            :key="authEditorVersion"
            ref="tokenAuth"
            :configured="!!form.token_auth_configured"
            @change="draftResult = undefined"
          />
          <el-form-item v-if="form.method === 'POST'" label="请求体 JSON"
            ><el-input
              v-model="body"
              type="textarea"
              :rows="3"
              autocomplete="off" /></el-form-item
          ><el-form-item
            v-if="form.method === 'POST'"
            label="POST 探测授权"
            required
            ><el-checkbox v-model="form.safe_post"
              >已确认请求无业务副作用</el-checkbox
            ></el-form-item
          ></template
        >
      </template>
      <div class="two-equal">
        <el-form-item label="间隔（秒）" required
          ><el-input-number
            v-model="form.interval_seconds"
            :min="15"
            :max="3600" /></el-form-item
        ><el-form-item label="超时（秒）" required
          ><el-input-number v-model="form.timeout_seconds" :min="1" :max="30"
        /></el-form-item>
      </div>
      <el-form-item label="启用"
        ><el-switch v-model="form.enabled"
      /></el-form-item>
      <el-alert
        v-if="draftResult"
        :type="draftResult.success ? 'success' : 'error'"
        :title="
          draftResult.success
            ? draftResult.refresh_tested
              ? '登录、刷新及 API 探测通过'
              : 'API 探测通过'
            : monitoringReason(draftResult.error_code || 'probe_failed')
        "
        :closable="false"
        show-icon
      /> </el-form
    ><template #footer
      ><el-button @click="dialog = false">取消</el-button
      ><el-button
        v-if="!form.probe_server_id && form.protocol === 'http'"
        :loading="testing"
        :disabled="busy"
        @click="testDraft"
        >测试探测</el-button
      >
      <el-button
        type="primary"
        :loading="busy"
        :disabled="testing"
        @click="save"
        >保存</el-button
      ></template
    ></el-dialog
  >
  <el-dialog v-model="trafficDialog" title="真实流量采集" width="500px"
    ><el-form label-width="150px"
      ><el-form-item label="Prometheus 目标" required
        ><el-input
          v-model="trafficTarget"
          placeholder="service:8080" /></el-form-item
      ><el-form-item label="采集路径">/metrics</el-form-item
      ><el-form-item label="请求计数器"
        >sentinelops_http_requests_total</el-form-item
      ><el-form-item label="耗时直方图"
        >sentinelops_http_request_duration_seconds</el-form-item
      ></el-form
    ><template #footer
      ><el-button @click="trafficDialog = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="saveTraffic"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.service-observability {
  margin-bottom: 20px;
}
.service-observability :deep(.llm-service-bindings) {
  border: 0;
  padding: 0;
  margin-bottom: 0;
}
.service-observability .stat-card {
  margin: 16px 0;
}
.stat-title {
  margin-bottom: 12px;
}
.metric-toolbar {
  margin-top: 24px;
}
.monitor-chart {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.dependency-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.el-select {
  width: 100%;
}
</style>
