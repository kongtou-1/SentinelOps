<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, query } from "@/api/client";
import { configs } from "@/config/lists";
import { metricLabels } from "@/config/metrics";
import { type Row } from "@/types/records";
import {
  date,
  label,
  metricUnit,
  metricValue,
  number,
  short,
} from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import { useAuth } from "@/features/auth/store";
import ServerOnboarding from "@/features/resources/components/ServerOnboarding.vue";
import ContainerServices from "@/features/resources/components/ContainerServices.vue";
import LLMServiceBindings from "@/features/applications/components/LLMServiceBindings.vue";
import ContainerDiagnostics from "@/features/resources/components/ContainerDiagnostics.vue";
import ContainerSourceBinding from "@/features/applications/components/ContainerSourceBinding.vue";
import ServiceObservability from "@/features/applications/components/ServiceObservability.vue";
import ContainerMetrics from "@/features/resources/components/ContainerMetrics.vue";
import ResourceBreakdown from "@/features/resources/components/ResourceBreakdown.vue";
import LLMStatus from "@/features/resources/components/LLMStatus.vue";
import { monitoringReason, parseTimeWindow } from "@/utils/monitoring";
import MetricsPanel from "@/components/charts/MetricsPanel.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import RecordForm from "@/components/records/RecordForm.vue";
const props = defineProps<{ kind: string }>(),
  route = useRoute(),
  auth = useAuth(),
  id = String(route.params.id),
  row = ref<Row>(),
  containerOverview = ref<Row>(),
  alerts = ref<Row[]>([]),
  incidents = ref<Row[]>([]),
  error = ref(""),
  busy = ref(false),
  formOpen = ref(false),
  values = ref<Record<string, { value: number | null; reason?: string }>>({});
const scopedPermissions = ref<Record<string, boolean>>({});
const can = (permission: string) =>
  auth.can(permission) && !!scopedPermissions.value[permission];
const metadataOpen = ref(false),
  metadataName = ref(""),
  metadataPurpose = ref("");
function editMetadata() {
  metadataName.value = row.value?.service_name || "";
  metadataPurpose.value = row.value?.purpose || "";
  metadataOpen.value = true;
}
async function saveMetadata() {
  if (!row.value) return;
  busy.value = true;
  try {
    await api(`/containers/${id}/service`, "PUT", {
      service_name: metadataName.value,
      purpose: metadataPurpose.value,
      depends_on: row.value.depends_on || [],
    });
    metadataOpen.value = false;
    await refresh();
    ElMessage.success("资料已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
const metrics = computed(() =>
  props.kind === "servers"
    ? [
        "host_cpu_percent",
        "host_memory_percent",
        "host_disk_percent",
        "http_latency_seconds",
      ]
    : props.kind === "containers"
      ? [
          "container_cpu_percent",
          "container_cpu_host_percent",
          "container_cpu_quota_percent",
          "container_memory_bytes",
          "container_restarts_5m",
          "container_oom_5m",
          "container_network_receive_bytes",
          "container_network_transmit_bytes",
        ]
      : [
          "llm_latency_avg_seconds",
          "llm_latency_p95_seconds",
          "llm_error_rate",
        ],
);
const timeWindow = computed(() =>
  parseTimeWindow(route.query.start, route.query.end),
);
async function load() {
  error.value = "";
  try {
    row.value = await api<Row>(`/${props.kind}/${id}`);
    const resourceKind =
      props.kind === "servers"
        ? "server"
        : props.kind === "containers"
          ? "container"
          : "llm_endpoint";
    const checks = await api<{ permission: string; allowed: boolean }[]>(
      "/access/check",
      "POST",
      {
        items: (auth.user?.permissions || []).map((permission) => ({
          permission,
          resource_kind: resourceKind,
          resource_id: id,
        })),
      },
    );
    scopedPermissions.value = Object.fromEntries(
      checks.map((check) => [check.permission, check.allowed]),
    );
    const source =
      props.kind === "llm-endpoints"
        ? { llm_endpoint_id: id }
        : { server_id: props.kind === "servers" ? id : row.value.server_id };
    const result = await Promise.allSettled([
      api<Row[]>("/alerts" + query({ ...source, limit: 10 })),
      api<Row[]>("/incidents" + query({ ...source, limit: 10 })),
    ]);
    if (result[0]?.status === "fulfilled") alerts.value = result[0].value;
    if (result[1]?.status === "fulfilled") incidents.value = result[1].value;
    await Promise.all(
      metrics.value.map(async (m) => {
        try {
          const d = await api<Row>(
            `/${props.kind}/${id}/metrics` + query({ metric: m }),
          );
          const valid = (d.result || [])
            .map((s: Row) => metricValue(m, s.value?.[1]))
            .filter((v: unknown) => v !== null);
          values.value[m] = {
            value: valid.length
              ? m.startsWith("container_network_")
                ? valid.reduce((sum: number, value: number) => sum + value, 0)
                : Math.max(...valid)
              : null,
            reason: d.reason,
          };
        } catch {
          values.value[m] = { value: null, reason: "指标查询失败" };
        }
      }),
    );
  } catch (e) {
    scopedPermissions.value = {};
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => (formOpen.value ? null : 30000));
async function operate(which: string) {
  if (busy.value) return;
  busy.value = true;
  try {
    if (which === "probe")
      await ElMessageBox.confirm(
        "会发送真实推理请求，可能消耗 Provider token。",
        "手动探测",
      );
    const r = await api<Row>(`/${props.kind}/${id}/${which}`, "POST");
    if ((which === "test-ssh" || which === "probe") && !r.success)
      throw new Error(label(r.error_type || r.status));
    ElMessage.success(
      which === "sync-containers" ? "容器清单已同步" : "操作成功",
    );
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link :to="'/' + kind" class="subtle-link">← 返回列表</router-link>
      <div class="heading-copy">
        <h1>{{ row?.name || "资源详情" }}</h1>
        <PageIntro />
        <p>
          {{
            kind === "containers"
              ? row?.image
              : kind === "servers"
                ? row?.host
                : row?.model
          }}
          · {{ short(id) }}
        </p>
      </div>
    </div>
    <div class="actions">
      <el-button
        v-if="kind === 'containers' && can('resources:edit')"
        @click="editMetadata"
        >编辑资料</el-button
      >
      <el-button @click="refresh">刷新</el-button
      ><el-button
        v-if="configs[kind]?.fields && can(configs[kind]!.manage!)"
        @click="formOpen = true"
        >编辑</el-button
      ><el-button
        v-if="kind === 'servers' && can('resources:manage')"
        :loading="busy"
        @click="operate('test-ssh')"
        >SSH 测试</el-button
      ><el-button
        v-if="kind === 'servers' && can('containers:sync')"
        :loading="busy"
        @click="operate('sync-containers')"
        >同步容器</el-button
      ><el-button
        v-if="kind === 'llm-endpoints' && can('llm:probe')"
        type="primary"
        :loading="busy"
        @click="operate('probe')"
        >手动探测</el-button
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
  <LLMStatus
    v-if="kind === 'llm-endpoints' && row"
    :key="id"
    :id="id"
    :name="row.name"
    :model="row.model"
    :base-url="row.base_url"
    :refresh-key="row.updated_at"
  />
  <div v-if="kind === 'containers' && row" class="container-monitoring">
    <section class="panel container-overview" aria-label="容器概况">
      <ContainerDiagnostics :id="id" />
      <ContainerSourceBinding
        :container-id="id"
        :image-id="row.image_id || undefined"
        :observed-at="row.identity_observed_at || undefined"
      />
      <section class="container-info">
        <h3>容器信息</h3>
        <dl class="detail-grid">
          <div>
            <dt>所属服务器</dt>
            <dd>
              <router-link :to="'/servers/' + row.server_id">{{
                short(row.server_id)
              }}</router-link>
            </dd>
          </div>
          <div>
            <dt>Docker 标识</dt>
            <dd>{{ row.docker_id }}</dd>
          </div>
          <div>
            <dt>最近清单采样</dt>
            <dd>{{ date(row.last_seen_at) }}</dd>
          </div>
        </dl>
      </section>
    </section>
    <ServiceObservability
      v-if="row.application_id && can('resources:read')"
      :key="row.application_id + ':' + id"
      :application-id="row.application_id"
      :container-id="id"
      :container-name="row.name"
    />
    <section v-else-if="can('resources:read')" class="panel">
      <div class="panel-heading">
        <h2>主动监测</h2>
        <router-link
          v-if="can('resources:manage')"
          :to="{ path: '/containers', query: { server_id: row.server_id } }"
          >关联服务</router-link
        >
      </div>
      <StatusTag value="unconfigured" text="未关联服务" />
    </section>
    <ContainerMetrics
      :id="id"
      :container="row"
      :values="values"
      :time-window="timeWindow"
    />
  </div>
  <section v-if="row && kind !== 'containers'" class="panel">
    <div class="status-row">
      <StatusTag v-if="row.status" :value="row.status" /><span class="muted"
        >更新于 {{ date(row.updated_at) }}</span
      >
    </div>
    <dl class="detail-grid">
      <template v-if="kind === 'servers'"
        ><div>
          <dt>环境</dt>
          <dd>{{ row.environment }}</dd>
        </div>
        <div>
          <dt>SSH</dt>
          <dd>{{ row.ssh_user }}@{{ row.host }}:{{ row.ssh_port }}</dd>
        </div>
        <div>
          <dt>最近 SSH 测试</dt>
          <dd>
            {{ row.last_ssh_test_status || "未测试" }} ·
            {{ date(row.last_ssh_test_at) }}
          </dd>
        </div>
        <div>
          <dt>node_exporter</dt>
          <dd>{{ row.node_exporter_target }}</dd>
        </div>
        <div>
          <dt>cAdvisor</dt>
          <dd>{{ row.cadvisor_target }}</dd>
        </div>
        <div>
          <dt>Docker 状态目标</dt>
          <dd>{{ row.docker_state_target || "未配置" }}</dd>
        </div>
        <div>
          <dt>HTTP 健康检查</dt>
          <dd>{{ row.health_url || "未配置" }}</dd>
        </div></template
      ><template v-else
        ><div>
          <dt>API 基础地址</dt>
          <dd>{{ row.base_url }}</dd>
        </div>
        <div>
          <dt>Provider</dt>
          <dd>{{ row.provider }}</dd>
        </div>
        <div>
          <dt>探测间隔</dt>
          <dd>{{ row.probe_interval_seconds }} 秒</dd>
        </div>
        <div>
          <dt>最近探测</dt>
          <dd>{{ date(row.last_probe_at) }}</dd>
        </div>
        <div>
          <dt>错误分类</dt>
          <dd>{{ row.last_error_type ? label(row.last_error_type) : "无" }}</dd>
        </div>
        <div>
          <dt>HTTP 状态</dt>
          <dd>{{ row.last_http_status ?? "—" }}</dd>
        </div></template
      >
    </dl>
  </section>
  <div v-if="kind !== 'containers'" class="cards">
    <div v-for="m in metrics" :key="m" class="stat-card">
      <div class="stat-title">{{ metricLabels[m] }}</div>
      <div class="stat-value">
        {{ number(values[m]?.value)
        }}<small style="font-size: 14px"> {{ metricUnit(m) }}</small>
      </div>
      <div class="stat-foot">
        {{
          monitoringReason(values[m]?.reason) ||
          (m === "host_disk_percent"
            ? "所有有效分区的最高使用率"
            : m.startsWith("llm_")
              ? "当前 5 分钟滑动窗口"
              : "最近有效采样")
        }}
      </div>
    </div>
  </div>
  <ServerOnboarding v-if="kind === 'servers' && row" :server="row" />
  <ContainerServices
    v-if="row && kind === 'servers'"
    :server-id="id"
    @observed="containerOverview = $event"
  />
  <LLMServiceBindings
    v-if="kind === 'llm-endpoints' && can('resources:read')"
    :key="id"
    :endpoint-id="id"
  />
  <ResourceBreakdown
    v-if="kind === 'servers'"
    :server-id="id"
    :time-window="timeWindow"
    :inventory="containerOverview"
  />
  <MetricsPanel
    v-if="kind !== 'containers'"
    :kind="kind"
    :id="id"
    :time-window="timeWindow"
  />
  <div class="two-equal">
    <section class="panel">
      <h2>{{ kind === "containers" ? "同服务器告警" : "关联告警" }}</h2>
      <div v-if="!alerts.length" class="empty-hint">暂无记录</div>
      <div v-for="a in alerts" :key="a.id" class="summary-list">
        <router-link :to="'/alerts/' + a.id">{{
          a.annotations?.summary || a.labels?.alertname
        }}</router-link
        ><StatusTag :value="a.status" />
      </div>
    </section>
    <section class="panel">
      <h2>{{ kind === "containers" ? "同服务器事故" : "关联事故" }}</h2>
      <div v-if="!incidents.length" class="empty-hint">暂无记录</div>
      <div v-for="i in incidents" :key="i.id" class="summary-list">
        <router-link :to="'/incidents/' + i.id">{{ i.title }}</router-link
        ><StatusTag :value="i.status" />
      </div>
    </section>
  </div>
  <RecordForm
    v-if="configs[kind]?.fields"
    v-model="formOpen"
    :kind="kind"
    :row="row"
    @saved="refresh"
  />
  <el-dialog
    v-model="metadataOpen"
    title="编辑容器资料"
    width="min(520px, 94vw)"
    ><el-form label-width="90px"
      ><el-form-item label="容器别名"
        ><el-input v-model="metadataName" maxlength="100" /></el-form-item
      ><el-form-item label="容器备注"
        ><el-input
          v-model="metadataPurpose"
          type="textarea"
          maxlength="2000" /></el-form-item></el-form
    ><template #footer
      ><el-button @click="metadataOpen = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="saveMetadata"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.container-monitoring {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  align-items: start;
  margin-bottom: 20px;
}
.container-overview {
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 24px;
  min-width: 0;
  margin-bottom: 0;
}
.container-overview > :deep(section) {
  min-width: 0;
}
.container-overview > :deep(section + section) {
  padding-left: 24px;
  border-left: 1px solid var(--border);
}
.container-overview :deep(h3) {
  font-size: 14px;
  margin-bottom: 14px;
}
.container-info .detail-grid {
  grid-template-columns: 1fr;
}
.container-info dd {
  overflow-wrap: anywhere;
}
@media (max-width: 1050px) {
  .container-overview {
    grid-template-columns: 1fr;
  }
  .container-overview > :deep(section + section) {
    padding-left: 0;
    padding-top: 24px;
    border-left: 0;
    border-top: 1px solid var(--border);
  }
}
</style>
