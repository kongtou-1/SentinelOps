<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, type Models } from "@/api/client";
import { date, recoveryLabel, short } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import StatusTag from "@/components/common/StatusTag.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import MetricsPanel from "@/components/charts/MetricsPanel.vue";
const id = String(useRoute().params.id),
  auth = useAuth(),
  row = ref<Models["AlertResponse"]>(),
  monitor = ref<Models["MonitorResponse"]>(),
  error = ref(""),
  busy = ref(false);
async function load() {
  try {
    row.value = await api("/alerts/" + id);
    monitor.value = undefined;
    if (row.value?.service_monitor_id) {
      try {
        monitor.value = await api(
          `/service-monitors/${row.value.service_monitor_id}`,
        );
      } catch {
        /* Retired monitor references remain in the alert evidence. */
      }
    }
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => 30000);
async function ack() {
  if (busy.value) return;
  busy.value = true;
  try {
    const { value } = await ElMessageBox.prompt(
      "记录确认说明。确认告警不会改变资源恢复状态。",
      "确认告警",
      {
        inputType: "textarea",
        inputValidator: (v) => !!v?.trim() || "请填写说明",
      },
    );
    await api("/alerts/" + id + "/ack", "POST", { comment: value });
    await refresh();
    ElMessage.success("已确认");
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "确认失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/alerts" class="subtle-link">← 返回告警列表</router-link>
      <div class="heading-copy">
        <h1>
          {{
            row?.annotations?.summary || row?.labels?.alertname || "告警详情"
          }}
        </h1>
        <PageIntro />
        <p>{{ id }}</p>
      </div>
    </div>

    <div class="actions">
      <el-button @click="refresh">刷新</el-button
      ><el-button
        v-if="auth.can('alerts:ack') && !row?.acknowledged_at"
        type="primary"
        :loading="busy"
        @click="ack"
        >确认告警</el-button
      ><router-link
        v-if="row?.incident_id"
        :to="'/incidents/' + row.incident_id"
        ><el-button>关联事故</el-button></router-link
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
  <section v-if="row" class="panel">
    <div class="badge-line">
      <StatusTag :value="row.severity" /><StatusTag
        :value="row.status"
      /><el-tag type="info">{{
        row.acknowledged_at ? "已确认" : "未确认"
      }}</el-tag
      ><StatusTag
        :value="row.recovery_status"
        :text="recoveryLabel(row.recovery_status)"
      />
    </div>
    <dl class="detail-grid">
      <div>
        <dt>触发时间</dt>
        <dd>{{ date(row.starts_at) }}</dd>
      </div>
      <div>
        <dt>结束时间</dt>
        <dd>{{ date(row.ends_at) }}</dd>
      </div>
      <div>
        <dt>确认时间</dt>
        <dd>{{ date(row.acknowledged_at) }}</dd>
      </div>
      <div>
        <dt>恢复核验时间</dt>
        <dd>{{ date(row.recovery_checked_at) }}</dd>
      </div>
      <div>
        <dt>恢复证据时间</dt>
        <dd>{{ date(row.recovery_evidence_at) }}</dd>
      </div>
      <div>
        <dt>核验原因</dt>
        <dd>{{ row.recovery_reason || "—" }}</dd>
      </div>
      <div>
        <dt>人工说明</dt>
        <dd>{{ row.comment || "—" }}</dd>
      </div>
      <div>
        <dt>关联规则</dt>
        <dd>{{ short(row.rule_id) }}</dd>
      </div>
      <div>
        <dt>关联资源</dt>
        <dd>
          <router-link
            v-if="monitor"
            :to="{
              path: '/applications/' + monitor.application_id,
              query: {
                monitor: monitor.id,
                start: row.starts_at,
                end: row.ends_at || new Date().toISOString(),
              },
            }"
            >服务监测 · {{ monitor.name }}</router-link
          >
          <router-link v-if="row.server_id" :to="'/servers/' + row.server_id"
            >服务器 {{ short(row.server_id) }}</router-link
          ><router-link
            v-if="row.llm_endpoint_id"
            :to="'/llm-endpoints/' + row.llm_endpoint_id"
            >LLM {{ short(row.llm_endpoint_id) }}</router-link
          >
        </dd>
      </div>
    </dl>
    <el-alert
      title="告警结束仅表示通知结束；恢复以核验状态和证据时间为准。"
      type="info"
      :closable="false"
    />
  </section>
  <MetricsPanel
    v-if="row?.server_id || row?.llm_endpoint_id"
    :kind="row.llm_endpoint_id ? 'llm-endpoints' : 'servers'"
    :id="row.llm_endpoint_id || row.server_id || undefined"
  />
  <section v-if="row" class="panel">
    <h2>触发与恢复依据</h2>
    <JsonEvidence
      :value="{
        rule_snapshot: row.rule_snapshot,
        labels: row.labels,
        annotations: row.annotations,
      }"
    />
  </section>
</template>
