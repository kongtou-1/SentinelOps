<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { confirmOperation } from "@/utils/confirmOperation";
import DiagnosisReview from "@/features/operations/components/DiagnosisReview.vue";
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, ApiError, query, request, type Models } from "@/api/client";
import { activeTask, date, label, short } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import StatusTag from "@/components/common/StatusTag.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import RelationList from "@/components/records/RelationList.vue";
import DiagnosisWorkbench from "@/features/diagnosis/components/DiagnosisWorkbench.vue";
import DiagnosisSources from "@/features/diagnosis/components/DiagnosisSources.vue";
import SourceEvidence from "@/features/diagnosis/components/SourceEvidence.vue";
import RepairHandoffs from "@/features/reports/components/RepairHandoffs.vue";
import IncidentRelations from "@/features/incidents/components/IncidentRelations.vue";
import IncidentResponsePanel from "@/features/incidents/components/IncidentResponsePanel.vue";
import IncidentStability from "@/features/incidents/components/IncidentStability.vue";
import type { Row } from "@/types/records";
import {
  canNormallyCloseIncident,
  incidentCloseReason,
} from "@/utils/workflows";
const id = String(useRoute().params.id),
  router = useRouter(),
  auth = useAuth(),
  row = ref<Models["IncidentDetail"]>(),
  analysis = ref<Models["AnalysisResponse"]>(),
  error = ref(""),
  busy = ref(false),
  tab = ref("overview"),
  timeline = ref<Models["TimelineResponse"][]>([]),
  timelinePage = ref(1),
  timelineTotal = ref(0),
  assignees = ref<Models["AssigneeResponse"][]>([]),
  assignOpen = ref(false),
  assignee = ref<string | null>(null),
  mergeOpen = ref(false),
  mergeTarget = ref(""),
  mergeCandidates = ref<Models["IncidentResponse"][]>([]),
  mergeComment = ref(""),
  revision = ref(0),
  attempt = ref(0),
  history = ref<Models["AnalysisResponse"][]>([]);
const diagnosis = computed(() => (analysis.value?.result || {}) as Row),
  closure = computed(() => (row.value?.closure || {}) as Row),
  canClose = computed(() => canNormallyCloseIncident(row.value)),
  closeReason = computed(() => incidentCloseReason(row.value)),
  writable = computed(
    () => !!row.value && !["closed", "merged"].includes(row.value.status),
  ),
  canRequest = computed(
    () =>
      ["open", "in_progress"].includes(row.value?.status || "") &&
      analysis.value?.status === "completed" &&
      !!diagnosis.value.proposal,
  ),
  assigneeName = computed(
    () =>
      assignees.value.find((a) => a.id === row.value?.assignee_id)?.username ||
      short(row.value?.assignee_id),
  );
async function loadTimeline() {
  const r = await request<Models["TimelineResponse"][]>(
    `/incidents/${id}/timeline` +
      query({ offset: (timelinePage.value - 1) * 10, limit: 10 }),
  );
  timeline.value = r.data;
  timelineTotal.value = r.total ?? 0;
}
async function load() {
  try {
    row.value = await api(`/incidents/${id}`);
    try {
      analysis.value = await api(`/incidents/${id}/analysis`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) analysis.value = undefined;
      else throw e;
    }
    await loadTimeline();
    if (!assignees.value.length)
      assignees.value = await all("/incidents/assignees");
    attempt.value++;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  analysis.value && activeTask(analysis.value.status)
    ? Math.min(10000, 2000 * Math.max(attempt.value, 1))
    : null,
);
async function action(work: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  try {
    await work();
    revision.value++;
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    busy.value = false;
  }
}
async function comment(title: string) {
  return (
    await ElMessageBox.prompt("请填写处理说明。", title, {
      inputType: "textarea",
      inputValidator: (v) =>
        (!!v?.trim() && v.trim().length <= 2000) || "请填写 1–2000 字说明",
    })
  ).value;
}
function changeStatus(status: string) {
  void action(async () => {
    await api(`/incidents/${id}/status`, "POST", {
      status,
      comment: await comment(
        status === "closed"
          ? "关闭事故"
          : status === "open"
            ? "标记待处理"
            : "开始处理",
      ),
    });
    ElMessage.success("状态已更新");
  });
}
function forceClose() {
  void action(async () => {
    const { value } = await ElMessageBox.prompt(
      `当前有 ${row.value?.firing_alert_count || 0} 条告警触发中，${row.value?.recovery_pending_alert_count || 0} 条恢复待核验。强制关闭仅结束事故处理，告警继续监控，恢复状态不变。请填写关闭原因。`,
      "管理员强制关闭事故",
      {
        type: "warning",
        inputType: "textarea",
        confirmButtonText: "确认强制关闭",
        inputValidator: (v) =>
          (!!v?.trim() && v.trim().length <= 2000) ||
          "请填写 1–2000 字关闭原因",
      },
    );
    await api(`/incidents/${id}/status`, "POST", {
      status: "closed",
      force: true,
      comment: value.trim(),
    });
    ElMessage.success("已强制关闭事故，关联告警继续监控");
  });
}
function addComment() {
  void action(async () => {
    await api(`/incidents/${id}/comments`, "POST", {
      comment: await comment("添加处理备注"),
    });
    ElMessage.success("备注已保存");
  });
}
function requestApproval() {
  void action(async () => {
    if (
      !(await confirmOperation(
        "依据当前事故诊断申请执行审批？批准后仍需人工发起执行。",
        "申请审批",
      ))
    )
      return;
    const r = await api<Models["ApprovalResponse"]>(
      `/incidents/${id}/approvals`,
      "POST",
      { analysis_id: analysis.value!.id },
    );
    ElMessage.success("执行审批申请已提交");
    await router.push("/approvals/" + r.id);
  });
}
function generate() {
  void action(async () => {
    if (
      !(await confirmOperation(
        `为事故“${row.value?.title}”生成复盘报告？生成时将调用报告模型。`,
        "生成报告",
      ))
    )
      return;
    const r = await api<Models["ReportResponse"]>(
      `/incidents/${id}/reports`,
      "POST",
      { force: false },
    );
    ElMessage.success("复盘报告生成任务已提交");
    await router.push("/reports/" + r.id);
  });
}
async function openAssign() {
  assignee.value = row.value?.assignee_id || null;
  assignOpen.value = true;
}
function saveAssign() {
  void action(async () => {
    await api(`/incidents/${id}`, "PATCH", {
      assignee_id: assignee.value || null,
    });
    assignOpen.value = false;
    ElMessage.success("负责人已更新");
  });
}
async function openMerge() {
  try {
    mergeCandidates.value = (
      await all<Models["IncidentResponse"]>("/incidents")
    ).filter((i) => i.id !== id && !["closed", "merged"].includes(i.status));
    mergeTarget.value = "";
    mergeComment.value = "";
    mergeOpen.value = true;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "加载失败");
  }
}
function merge() {
  void action(async () => {
    if (!mergeTarget.value || !mergeComment.value.trim())
      throw new Error("请选择目标事故并填写说明");
    const r = await api<Models["IncidentResponse"]>(
      `/incidents/${id}/merge`,
      "POST",
      { target_incident_id: mergeTarget.value, comment: mergeComment.value },
    );
    mergeOpen.value = false;
    await router.push("/incidents/" + r.id);
  });
}
async function analysisHistory() {
  try {
    history.value = await all(`/incidents/${id}/analyses`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "加载失败");
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/incidents" class="subtle-link"
        >← 返回事故列表</router-link
      >
      <div class="heading-copy">
        <h1>{{ row?.title || "事故详情" }}</h1>
        <PageIntro />
        <p>{{ id }}</p>
      </div>
    </div>

    <div class="actions">
      <el-button @click="refresh">刷新</el-button
      ><el-button
        v-if="auth.can('incidents:handle') && writable"
        @click="openAssign"
        >分配负责人</el-button
      ><el-button
        v-if="auth.can('incidents:handle') && writable"
        @click="openMerge"
        >合并事故</el-button
      ><el-tooltip
        v-if="auth.can('incidents:handle') && writable"
        :content="closeReason"
        :disabled="canClose"
      >
        <span
          ><el-button
            @click="changeStatus('closed')"
            :disabled="!canClose || busy"
            >关闭事故</el-button
          ></span
        >
      </el-tooltip>
      <el-button
        v-if="auth.can('incidents:force_close') && writable && !canClose"
        type="danger"
        plain
        :disabled="busy"
        @click="forceClose"
        >强制关闭</el-button
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
  <IncidentResponsePanel
    v-if="row"
    :incident-id="id"
    :writable="['open', 'in_progress'].includes(row.status)"
    @accepted="refresh"
  />
  <IncidentStability v-if="row" :incident-id="id" />
  <section v-if="row" class="panel">
    <div v-if="row.service_monitors?.length" class="actions">
      <router-link
        v-for="monitor in row.service_monitors"
        :key="monitor.id"
        :to="{
          path: '/applications/' + monitor.application_id,
          query: {
            monitor: monitor.id,
            start: row.started_at,
            end: row.recovered_at || new Date().toISOString(),
          },
        }"
        >服务监测 · {{ monitor.name }}</router-link
      >
    </div>
    <el-alert
      v-if="closure.forced"
      type="warning"
      :closable="false"
      :title="
        closure.recovery_verified
          ? '管理员强制关闭 · 关闭时恢复已核验'
          : '管理员强制关闭 · 关闭时恢复未核验'
      "
      :description="`${closure.actor_username} 于 ${date(closure.closed_at)} 关闭。原因：${closure.reason}`"
      show-icon
    />
    <div class="status-row">
      <StatusTag :value="row.status" /><StatusTag :value="row.severity" /><span
        class="muted"
        >负责人 {{ row.assignee_id ? assigneeName : "未分配" }}</span
      ><span class="muted">开始 {{ date(row.started_at) }}</span>
    </div>
    <dl class="detail-grid">
      <div>
        <dt>关联告警</dt>
        <dd>{{ row.alert_count }} 条 · 触发中 {{ row.firing_alert_count }}</dd>
      </div>
      <div>
        <dt>恢复核验</dt>
        <dd>
          已验证 {{ row.recovery_verified_alert_count }} · 待核验
          {{ row.recovery_pending_alert_count }} · 已终止
          {{ row.recovery_terminated_alert_count }}
        </dd>
      </div>
      <div>
        <dt>关联资源</dt>
        <dd>
          <router-link
            v-for="s in row.server_ids"
            :key="s"
            :to="'/servers/' + s"
            >服务器 {{ short(s) }} </router-link
          ><router-link
            v-for="s in row.llm_endpoint_ids"
            :key="s"
            :to="'/llm-endpoints/' + s"
            >LLM {{ short(s) }}
          </router-link>
        </dd>
      </div>
    </dl>
    <p v-if="writable && !canClose" class="muted">
      {{ closeReason
      }}<template v-if="auth.can('incidents:force_close')">
        Admin 可填写原因后强制关闭。</template
      >
    </p>
    <router-link
      v-if="row.merged_into_id"
      :to="'/incidents/' + row.merged_into_id"
      >查看合并目标事故 →</router-link
    >
    <div v-if="auth.can('incidents:handle') && writable" class="actions">
      <el-button :disabled="busy" @click="addComment">添加处理备注</el-button
      ><el-button
        v-if="row.status === 'open'"
        :disabled="busy"
        @click="changeStatus('in_progress')"
        >开始处理</el-button
      ><el-button
        v-if="row.status === 'in_progress'"
        :disabled="busy"
        @click="changeStatus('open')"
        >标记待处理</el-button
      >
    </div>
  </section>
  <section class="panel">
    <el-tabs class="workspace-tabs" v-model="tab"
      ><el-tab-pane label="概览" name="overview"
        ><div class="two-equal">
          <div>
            <h2>诊断摘要</h2>
            <p>{{ diagnosis.summary || "尚无诊断结论" }}</p>
            <div class="actions">
              <el-button
                v-if="auth.can('analyses:run') && writable"
                type="primary"
                :loading="busy"
                :disabled="analysis && activeTask(analysis.status)"
                @click="tab = 'analysis'"
                >AI 只读诊断</el-button
              ><el-button @click="tab = 'analysis'">查看诊断依据</el-button>
            </div>
          </div>
          <div>
            <h2>最近处理过程</h2>
            <el-timeline
              ><el-timeline-item
                v-for="t in row?.timeline.slice(0, 5)"
                :key="t.id"
                :timestamp="date(t.occurred_at)"
                ><span>{{ t.event_type }}</span>
                <div class="form-hint">
                  {{ JSON.stringify(t.content) }}
                </div></el-timeline-item
              ></el-timeline
            ><el-button text type="primary" @click="tab = 'timeline'"
              >完整时间线</el-button
            >
          </div>
        </div></el-tab-pane
      >
      <el-tab-pane label="关联与复发" name="relations" lazy>
        <IncidentRelations :incident-id="id" :writable="writable" />
      </el-tab-pane>
      <el-tab-pane label="时间线" name="timeline"
        ><div class="panel-heading">
          <el-button @click="loadTimeline">刷新</el-button>
        </div>
        <el-timeline
          ><el-timeline-item
            v-for="t in timeline"
            :key="t.id"
            :timestamp="date(t.occurred_at)"
            ><h3>{{ t.event_type }} · {{ t.actor_type }}</h3>
            <JsonEvidence :value="t.content" /></el-timeline-item
        ></el-timeline>
        <div v-if="!timeline.length" class="empty-hint">暂无记录</div>
        <div class="pagination">
          <el-pagination
            v-model:current-page="timelinePage"
            :total="timelineTotal"
            :page-size="10"
            layout="prev, pager, next"
            @current-change="loadTimeline"
          /></div
      ></el-tab-pane>
      <el-tab-pane label="诊断" name="analysis"
        ><DiagnosisWorkbench
          v-if="auth.can('analyses:run')"
          :incident-id="id"
          :writable="writable"
          :visible="tab === 'analysis'"
          @adopted="refresh"
        />
        <div class="panel-heading">
          <div class="badge-line">
            <StatusTag v-if="analysis" :value="analysis.status" /><el-tag
              type="info"
              >只读诊断 · 人工审批</el-tag
            >
          </div>
          <div class="actions">
            <el-button @click="analysisHistory">诊断历史</el-button>
          </div>
        </div>
        <el-alert
          v-if="analysis?.error_code"
          :title="analysis.error_code"
          type="error"
          :closable="false"
          class="error-box"
        />
        <el-tag v-if="analysis">{{
          analysis.trigger === "automatic" ? "自动诊断" : "手动诊断"
        }}</el-tag>
        <DiagnosisSources
          v-if="analysis"
          :context="analysis.source_context"
          :database-context="analysis.database_context"
        />
        <SourceEvidence
          v-for="(e, i) in analysis?.evidence || []"
          :key="i"
          :evidence="e"
        />
        <div v-if="diagnosis.summary" class="text-block">
          <h3>结论</h3>
          <p>{{ diagnosis.summary }}</p>
          <h3>影响</h3>
          <p>{{ diagnosis.impact }}</p>
        </div>
        <div
          v-for="(c, index) in diagnosis.possible_causes || []"
          :key="index"
          class="text-block"
        >
          <h3>原因 {{ Number(index) + 1 }}</h3>
          <p>{{ c.description }}</p>
          <div class="form-hint">
            引用证据 {{ c.evidence_ids?.join(" · ") }}
          </div>
        </div>
        <div v-if="diagnosis.proposal" class="text-block">
          <h3>
            操作提案 · {{ diagnosis.proposal.skill }} v{{
              diagnosis.proposal.version
            }}
          </h3>
          <p>{{ diagnosis.proposal.rationale }}</p>
          <p>预计影响：{{ diagnosis.proposal.impact }}</p>
          <el-button
            v-if="auth.can('executions:run')"
            type="primary"
            :loading="busy"
            :disabled="!canRequest"
            @click="requestApproval"
            >申请审批</el-button
          >
        </div>
        <div v-if="diagnosis.data_gaps?.length" class="text-block">
          <h3>证据缺口</h3>
          <p v-for="s in diagnosis.data_gaps" :key="s">{{ label(s) }}</p>
        </div>
        <DiagnosisReview
          v-if="analysis?.status === 'completed'"
          :analysis-id="analysis.id"
        />
        <el-collapse v-if="analysis"
          ><p class="muted">
            {{
              analysis.session_id?.startsWith("conversation-run-")
                ? "正式采用的诊断快照；完整对话与运行记录按会话权限查看。"
                : analysis.trigger === "automatic"
                  ? "自动诊断记录"
                  : "旧版诊断记录"
            }}
          </p>
          <el-collapse-item title="只读证据与工具调用" name="evidence"
            ><JsonEvidence
              :value="{
                evidence: analysis.evidence,
                knowledge: analysis.knowledge,
                tool_calls: analysis.tool_calls,
              }" /></el-collapse-item></el-collapse
        ><el-table v-if="history.length" :data="history"
          ><el-table-column label="诊断时间"
            ><template #default="{ row: r }">{{
              date(r.created_at)
            }}</template></el-table-column
          ><el-table-column label="触发方式"
            ><template #default="{ row: r }">{{
              r.trigger === "automatic" ? "自动" : "手动"
            }}</template></el-table-column
          ><el-table-column label="状态"
            ><template #default="{ row: r }"
              ><StatusTag :value="r.status" /></template></el-table-column
          ><el-table-column label="结论"
            ><template #default="{ row: r }">{{
              r.result?.summary || r.error_code || "—"
            }}</template></el-table-column
          ></el-table
        >
        <div v-if="!analysis" class="empty-hint">
          尚无正式诊断；会话结论需手动采用。
        </div></el-tab-pane
      >
      <el-tab-pane label="修复交接" name="repairs" lazy>
        <RepairHandoffs
          :incident-id="id"
          :visible="tab === 'repairs'"
          :merged="row?.status === 'merged'"
          @changed="refresh"
        />
      </el-tab-pane>
      <el-tab-pane label="审批与执行" name="actions" lazy
        ><h2>审批记录</h2>
        <RelationList kind="approvals" :incident-id="id" :revision="revision" />
        <div class="spacer"></div>
        <h2>执行记录</h2>
        <RelationList
          kind="executions"
          :incident-id="id"
          :revision="revision" /></el-tab-pane
      ><el-tab-pane label="复盘报告" name="reports" lazy
        ><div class="panel-heading">
          <el-button
            v-if="auth.can('reports:run')"
            type="primary"
            :loading="busy"
            :disabled="row?.status !== 'closed'"
            @click="generate"
            >生成报告</el-button
          >
        </div>
        <p class="muted">
          关闭事故后人工生成；审核通过后可以导出与显式发送邮件。
        </p>
        <RelationList
          kind="reports"
          :incident-id="id"
          :revision="revision" /></el-tab-pane
      ><el-tab-pane label="关联告警" name="alerts" lazy
        ><RelationList
          kind="alerts"
          :incident-id="id"
          :revision="revision" /></el-tab-pane
    ></el-tabs>
  </section>
  <el-dialog v-model="assignOpen" title="分配负责人" width="460px"
    ><el-select
      v-model="assignee"
      clearable
      filterable
      placeholder="未分配"
      style="width: 100%"
      ><el-option
        v-for="a in assignees"
        :key="a.id"
        :label="a.username + ' · ' + a.role"
        :value="a.id" /></el-select
    ><template #footer
      ><el-button @click="assignOpen = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="saveAssign"
        >保存</el-button
      ></template
    ></el-dialog
  ><el-dialog v-model="mergeOpen" title="合并至另一事故" width="560px"
    ><p>当前事故合并后将只读，处理过程保留。请选择可处理的目标事故。</p>
    <el-select v-model="mergeTarget" filterable style="width: 100%"
      ><el-option
        v-for="i in mergeCandidates"
        :key="i.id"
        :label="i.title + ' · ' + short(i.id)"
        :value="i.id" /></el-select
    ><el-input
      v-model="mergeComment"
      type="textarea"
      :rows="3"
      placeholder="合并原因"
      style="margin-top: 18px"
    /><template #footer
      ><el-button @click="mergeOpen = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="merge"
        >合并</el-button
      ></template
    ></el-dialog
  >
</template>
