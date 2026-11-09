<script setup lang="ts">
import { ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
import ServiceImpact from "./ServiceImpact.vue";

const props = defineProps<{ applicationId: string }>();
const auth = useAuth();
const impactPanel = ref<InstanceType<typeof ServiceImpact>>();
const rows = ref<Row[]>([]),
  monitors = ref<Row[]>([]),
  error = ref("");
const dialog = ref(false),
  busy = ref(false),
  editing = ref("");
const form = ref<Row>({});
const kinds: Record<string, string> = {
  business_success: "业务成功率",
  probe_availability: "探测可用率",
  request_p95: "请求 P95",
  llm_ttft_p95: "LLM 首字 P95",
};
const states: Record<string, string> = {
  met: "达标",
  breached: "未达标",
  provisional: "历史不足",
  paused: "已暂停",
  stale: "采集过期",
  unconfigured: "未接入",
  no_requests: "无请求",
  unknown: "未知",
};
const reasons: Record<string, string> = {
  monitoring_paused: "监测已暂停",
  monitor_unavailable: "监测不可用",
  traffic_unconfigured: "未配置流量采集",
  collector_missing_or_stale: "采集失联或过期",
  metric_not_reported: "指标未上报",
  no_business_samples: "窗口内无请求",
  value_unavailable: "结果不可用",
  insufficient_history: "历史覆盖不足",
  observation_unavailable: "读取观测失败",
  evaluated: "已计算",
};
async function load() {
  const selected = props.applicationId;
  try {
    const result = await Promise.all([
      api<Row[]>(`/business-services/${selected}/objectives?limit=100`),
      api<Row[]>(`/service-monitors?application_id=${selected}`),
    ]);
    if (selected !== props.applicationId) return;
    [rows.value, monitors.value] = result;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => (dialog.value ? null : 30000));
watch(
  () => props.applicationId,
  () => {
    rows.value = [];
    void refresh();
  },
);
function open(row?: Row) {
  editing.value = row?.id || "";
  form.value = row
    ? { ...row, expected_version: row.version }
    : {
        name: "",
        kind: "business_success",
        monitor_id: null,
        operation: "work",
        metric_source: "application",
        target: 0.99,
        window_days: 7,
        enabled: true,
        expected_version: 0,
      };
  dialog.value = true;
}
async function save() {
  busy.value = true;
  try {
    const fields = [
      "name",
      "kind",
      "monitor_id",
      "operation",
      "metric_source",
      "target",
      "window_days",
      "enabled",
      "expected_version",
    ];
    const body = Object.fromEntries(
      fields.map((key) => [key, form.value[key]]),
    );
    await api(
      `/business-services/${props.applicationId}/objectives${editing.value ? "/" + editing.value : ""}`,
      editing.value ? "PUT" : "POST",
      body,
    );
    dialog.value = false;
    ElMessage.success("已保存");
    await refresh();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
async function remove(row: Row) {
  try {
    await ElMessageBox.confirm(`删除目标「${row.name}」？`, "删除目标", {
      type: "warning",
    });
    await api(
      `/service-objectives/${row.id}?expected_version=${row.version}`,
      "DELETE",
    );
    ElMessage.success("业务目标已删除");
    await refresh();
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message);
  }
}
function value(row: Row) {
  const value = row.evaluation?.value;
  if (value === null || value === undefined) return "—";
  return row.kind.endsWith("p95")
    ? `${Number(value).toFixed(3)} 秒`
    : `${(Number(value) * 100).toFixed(2)}%`;
}
async function refreshPage() {
  await Promise.all([refresh(), impactPanel.value?.refresh()]);
}
defineExpose({ refresh: refreshPage });
</script>

<template>
  <ServiceImpact ref="impactPanel" :application-id="applicationId">
    <section class="panel">
      <div class="governance-heading">
        <h2>业务目标</h2>
        <el-button
          v-if="auth.can('resources:manage')"
          type="primary"
          @click="open()"
          >添加目标</el-button
        >
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div v-if="!rows.length" class="governance-empty">暂无业务目标</div>
      <el-table v-else :data="rows" empty-text="未配置业务目标">
        <el-table-column prop="name" label="目标" min-width="140" />
        <el-table-column label="指标" min-width="130"
          ><template #default="s">{{
            kinds[s.row.kind]
          }}</template></el-table-column
        >
        <el-table-column label="目标值"
          ><template #default="s">{{
            s.row.kind.endsWith("p95")
              ? `${s.row.target} 秒`
              : `${(s.row.target * 100).toFixed(2)}%`
          }}</template></el-table-column
        >
        <el-table-column label="窗口"
          ><template #default="s"
            >{{ s.row.window_days }} 天</template
          ></el-table-column
        >
        <el-table-column label="观测值"
          ><template #default="s">{{ value(s.row) }}</template></el-table-column
        >
        <el-table-column label="状态" min-width="130"
          ><template #default="s"
            ><el-tag>{{
              states[s.row.evaluation?.status] || "待计算"
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="观测覆盖"
          ><template #default="s">{{
            s.row.evaluation?.coverage === undefined
              ? "—"
              : (s.row.evaluation.coverage * 100).toFixed(1) + "%"
          }}</template></el-table-column
        >
        <el-table-column label="剩余失败次数"
          ><template #default="s">{{
            s.row.evaluation?.error_budget_remaining == null
              ? "—"
              : s.row.evaluation.error_budget_remaining.toFixed(1)
          }}</template></el-table-column
        >
        <el-table-column label="计算结果" min-width="140"
          ><template #default="s">{{
            reasons[s.row.evaluation?.reason] || "待计算"
          }}</template></el-table-column
        >
        <el-table-column label="更新时间" min-width="170"
          ><template #default="s">{{
            date(s.row.checked_at)
          }}</template></el-table-column
        >
        <el-table-column
          v-if="auth.can('resources:manage')"
          label="操作"
          width="140"
          ><template #default="s"
            ><el-button link @click="open(s.row)">编辑</el-button
            ><el-button link type="danger" @click="remove(s.row)"
              >删除</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <el-dialog
        v-model="dialog"
        :title="editing ? '编辑业务目标' : '添加业务目标'"
        width="560px"
      >
        <el-form label-width="110px">
          <el-form-item label="名称" required
            ><el-input v-model="form.name" maxlength="100"
          /></el-form-item>
          <el-form-item label="指标" required
            ><el-select
              v-model="form.kind"
              @change="
                if (['probe_availability', 'request_p95'].includes(form.kind))
                  form.metric_source = 'application';
              "
              ><el-option
                v-for="(label, kind) in kinds"
                :key="kind"
                :label="label"
                :value="kind" /></el-select
          ></el-form-item>
          <el-form-item
            v-if="['probe_availability', 'request_p95'].includes(form.kind)"
            label="监测项"
            required
            ><el-select v-model="form.monitor_id"
              ><el-option
                v-for="item in monitors"
                :key="item.id"
                :label="item.name"
                :value="item.id" /></el-select
          ></el-form-item>
          <el-form-item
            v-if="['business_success', 'llm_ttft_p95'].includes(form.kind)"
            label="数据源"
            required
            ><el-select
              v-model="form.metric_source"
              @change="
                form.operation =
                  form.metric_source === 'platform' ? 'diagnosis' : 'work'
              "
              ><el-option label="应用埋点" value="application" /><el-option
                label="平台流程"
                value="platform" /></el-select
          ></el-form-item>
          <el-form-item
            v-if="form.metric_source === 'platform'"
            label="平台流程"
            required
            ><el-select v-model="form.operation"
              ><el-option
                v-for="op in form.kind === 'business_success'
                  ? ['diagnosis', 'report', 'execution', 'source_snapshot']
                  : ['diagnosis', 'report', 'evaluation', 'probe']"
                :key="op"
                :value="op"
                :label="op" /></el-select
          ></el-form-item>
          <el-form-item
            v-else-if="['business_success', 'llm_ttft_p95'].includes(form.kind)"
            label="业务操作"
            required
            ><el-input v-model="form.operation" maxlength="100"
          /></el-form-item>
          <el-form-item
            :label="form.kind.endsWith('p95') ? '目标秒数' : '成功比例 0–1'"
            required
            ><el-input-number
              v-model="form.target"
              :min="0.001"
              :max="form.kind.endsWith('p95') ? 3600 : 1"
              :step="form.kind.endsWith('p95') ? 0.1 : 0.001"
          /></el-form-item>
          <el-form-item label="评价窗口" required
            ><el-select v-model="form.window_days"
              ><el-option
                v-for="days in [1, 7, 30]"
                :key="days"
                :label="`${days} 天`"
                :value="days" /></el-select
          ></el-form-item>
          <el-form-item label="启用"
            ><el-switch v-model="form.enabled"
          /></el-form-item>
        </el-form>
        <template #footer
          ><el-button @click="dialog = false">取消</el-button
          ><el-button type="primary" :loading="busy" @click="save"
            >保存</el-button
          ></template
        >
      </el-dialog>
    </section>
  </ServiceImpact>
</template>
<style scoped>
.governance-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 36px;
  margin-bottom: 20px;
}
.governance-heading h2 {
  margin: 0;
}
.governance-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--muted);
  border: 1px dashed var(--border);
  border-radius: 8px;
}
</style>
