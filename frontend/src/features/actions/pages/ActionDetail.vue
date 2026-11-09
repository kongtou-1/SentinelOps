<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, type Models } from "@/api/client";
import {
  activeTask,
  date,
  effectiveApproval,
  label,
  short,
} from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import { useAuth } from "@/features/auth/store";
import type { Row } from "@/types/records";
import StatusTag from "@/components/common/StatusTag.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
const props = defineProps<{ kind: string }>(),
  id = String(useRoute().params.id),
  router = useRouter(),
  auth = useAuth(),
  row = ref<Row>(),
  error = ref(""),
  busy = ref(false),
  attempt = ref(0);
const approval = computed(() => props.kind === "approvals"),
  state = computed(() =>
    row.value
      ? approval.value
        ? effectiveApproval(row.value as { status: string; expires_at: string })
        : row.value.status
      : "unknown",
  ),
  proposal = computed(() => row.value?.binding?.proposal);
async function load() {
  try {
    row.value = await api("/" + props.kind + "/" + id);
    error.value = "";
    attempt.value++;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  !approval.value && row.value && activeTask(row.value.status)
    ? Math.min(10000, 2000 * Math.max(attempt.value, 1))
    : approval.value && ["pending", "approved"].includes(state.value)
      ? 10000
      : null,
);
async function decide(which: string) {
  if (busy.value) return;
  busy.value = true;
  try {
    const { value } = await ElMessageBox.prompt(
      "请填写审批依据。批准后仍需单独发起执行。",
      which === "approve" ? "批准审批" : "拒绝审批",
      {
        inputType: "textarea",
        inputValidator: (v) =>
          (!!v?.trim() && v.length <= 2000) || "请填写 1–2000 字说明",
      },
    );
    await api(`/approvals/${id}/${which}`, "POST", { comment: value });
    await refresh();
    ElMessage.success(
      which === "approve" ? "已批准，等待人工发起执行" : "已拒绝",
    );
  } catch (e) {
    if (e !== "cancel" && e !== "close") {
      ElMessage.error(e instanceof Error ? e.message : "审批失败");
      await refresh();
    }
  } finally {
    busy.value = false;
  }
}
async function execute() {
  if (busy.value) return;
  busy.value = true;
  try {
    await ElMessageBox.confirm(
      `将执行审批绑定的 restart_container v1，目标容器 ${short(row.value?.container_id)}。执行将经过前置检查与恢复验证。`,
      "发起受控执行",
      { confirmButtonText: "发起执行", type: "warning" },
    );
    const r = await api<Models["ExecutionResponse"]>(
      `/approvals/${id}/execute`,
      "POST",
      {},
    );
    await router.push("/executions/" + r.id);
  } catch (e) {
    if (e !== "cancel" && e !== "close") {
      ElMessage.error(e instanceof Error ? e.message : "执行申请失败");
      await refresh();
    }
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link :to="'/' + kind" class="subtle-link"
        >← 返回{{ approval ? "审批" : "执行" }}列表</router-link
      >
      <div class="heading-copy">
        <h1>
          restart_container
          <span class="muted">· {{ approval ? "审批" : "执行" }}</span>
        </h1>
        <PageIntro />
        <p>{{ id }}</p>
      </div>
    </div>

    <div class="actions">
      <el-button @click="refresh">刷新</el-button
      ><template v-if="approval"
        ><el-button
          v-if="auth.can('approvals:decide') && state === 'pending'"
          :loading="busy"
          @click="decide('reject')"
          >拒绝</el-button
        ><el-button
          v-if="auth.can('approvals:decide') && state === 'pending'"
          type="primary"
          :loading="busy"
          @click="decide('approve')"
          >批准</el-button
        ><el-button
          v-if="auth.can('executions:run') && state === 'approved'"
          type="primary"
          :loading="busy"
          @click="execute"
          >发起执行</el-button
        ></template
      ><router-link
        v-if="row?.incident_id"
        :to="'/incidents/' + row.incident_id"
        ><el-button>关联事故</el-button></router-link
      ><router-link
        v-if="!approval && auth.can('audit:read')"
        :to="'/audit-logs?execution_id=' + id"
        ><el-button>查看审计</el-button></router-link
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
      <StatusTag :value="state" /><el-tag type="warning">medium 风险</el-tag
      ><el-tag type="info">restart_container v1</el-tag>
    </div>
    <el-alert
      v-if="row.error_code"
      :title="row.error_code"
      type="error"
      :closable="false"
      class="error-box"
    />
    <dl class="detail-grid">
      <div>
        <dt>目标容器</dt>
        <dd>
          <router-link :to="'/containers/' + row.container_id">{{
            row.container_id
          }}</router-link>
        </dd>
      </div>
      <div>
        <dt>申请 / 发起时间</dt>
        <dd>{{ date(row.created_at) }}</dd>
      </div>
      <div>
        <dt>{{ approval ? "有效期至" : "完成时间" }}</dt>
        <dd>{{ date(approval ? row.expires_at : row.finished_at) }}</dd>
      </div>
      <div v-if="approval">
        <dt>审批说明</dt>
        <dd>{{ row.decision_comment || "尚未审批" }}</dd>
      </div>
      <div v-else>
        <dt>关联审批</dt>
        <dd>
          <router-link :to="'/approvals/' + row.approval_id">{{
            short(row.approval_id)
          }}</router-link>
        </dd>
      </div>
    </dl>
  </section>
  <template v-if="approval && row"
    ><section class="panel">
      <h2>动作提案与冻结绑定</h2>
      <p>{{ proposal?.rationale }}</p>
      <p>预计影响：{{ proposal?.impact || "—" }}</p>
      <el-alert
        title="参数在申请时冻结，执行时重新校验，页面不能修改目标或动作参数。"
        type="info"
        :closable="false"
        class="error-box"
      /><JsonEvidence :value="row.binding" />
      <div class="form-hint">绑定摘要 {{ row.binding_hash }}</div>
    </section>
    <section class="panel">
      <h2>执行前置检查</h2>
      <p>
        审批有效性、固定 SSH
        主机公钥、容器身份和状态将在人工发起执行后检查。实际结果在执行详情中显示。
      </p>
    </section></template
  ><template v-else-if="row"
    ><div class="two-equal">
      <section class="panel">
        <h2>前置检查结果</h2>
        <JsonEvidence :value="row.precheck" />
      </section>
      <section class="panel">
        <h2>恢复验证</h2>
        <JsonEvidence :value="row.verification" />
      </section>
    </div>
    <section class="panel">
      <h2>执行输出</h2>
      <JsonEvidence :value="row.output" />
    </section>
    <el-alert
      v-if="row.status === 'succeeded'"
      title="固定动作与容器验证成功。事故恢复还需结合监控证据核验。"
      type="success"
      :closable="false" /><el-alert
      v-if="row.status === 'unknown'"
      title="执行派发后结果无法确认，请先核对现场和审计记录，避免重复操作。"
      type="warning"
      :closable="false"
  /></template>
</template>
