<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import ReportImprovements from "@/features/operations/components/ReportImprovements.vue";
import { computed, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, ApiError, download, type Models } from "@/api/client";
import { activeTask, date, short } from "@/utils/display";
import {
  canEditReport,
  canExportReport,
  revisionRequest,
} from "@/utils/workflows";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import StatusTag from "@/components/common/StatusTag.vue";
import ReportBody from "@/features/reports/components/ReportBody.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import DatabaseContext from "@/features/diagnosis/components/DatabaseContext.vue";
import type { Row } from "@/types/records";
const id = String(useRoute().params.id),
  router = useRouter(),
  auth = useAuth(),
  row = ref<Models["ReportResponse"]>(),
  error = ref(""),
  busy = ref(false),
  editing = ref(false),
  editRevision = ref(0),
  draft = ref<Models["ReportContent"]>(),
  versions = ref<Models["RevisionResponse"][]>([]),
  deliveries = ref<Models["DeliveryResponse"][]>([]),
  chosenVersion = ref<Models["RevisionResponse"]>(),
  sendOpen = ref(false),
  recipients = ref(""),
  sendComment = ref(""),
  sendError = ref(""),
  sendKey = ref(""),
  sendBody = ref<Models["SendReport"]>(),
  attempt = ref(0),
  view = ref("report");
const content = computed(() => (row.value?.content || {}) as Row),
  records = computed(
    () => ((row.value?.snapshot as Row)?.records || []) as Row[],
  ),
  evidenceOptions = computed(() =>
    records.value.flatMap((r) => [
      { value: String(r.id), label: `${r.kind} · ${String(r.id)}` },
      ...(r.kind === "analysis"
        ? (r.data?.evidence || [])
            .filter((e: Row) => typeof e.id === "string")
            .map((e: Row) => ({
              value: e.id,
              label: `诊断证据 · ${e.source} · ${e.id}`,
            }))
        : []),
    ]),
  ),
  allowEdit = computed(() =>
    canEditReport(row.value?.status || "", auth.can("reports:manage")),
  );
const contextSummary = computed(
  () => (row.value?.snapshot as Row)?.context_summary as Row | undefined,
);
const reportError = computed(() => {
  if (row.value?.error_code === "invalid_response")
    return "模型回复格式或证据引用未通过校验，未发布正文。";
  if (row.value?.error_code === "provider_response_too_large")
    return "模型响应超过传输上限，未发布正文。";
  if (row.value?.error_code === "model_output_limit")
    return "模型输出达到长度上限，报告被截断，未发布正文。";
  if (row.value?.error_code === "harness_failed")
    return "复盘运行未正常完成，未生成有效正文。旧版运行记录未保留具体结束原因，可返回事故重新生成。";
  if (row.value?.error_code !== "report_context_too_large")
    return row.value?.error_code;
  const size = contextSummary.value?.input_bytes;
  const limit = contextSummary.value?.limit_bytes;
  return size && limit
    ? `事故材料精简后仍过大（${Math.ceil(size / 1024)} KiB，上限 ${Math.ceil(limit / 1024)} KiB），完整证据已保留。`
    : "事故原始材料超过旧版生成限制，完整证据已保留；请返回关联事故重新生成报告。";
});
async function load() {
  try {
    row.value = await api("/reports/" + id);
    if (row.value?.revision)
      versions.value = await all("/reports/" + id + "/revisions");
    if (auth.can("reports:manage"))
      deliveries.value = await all("/reports/" + id + "/deliveries");
    attempt.value++;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  editing.value || sendOpen.value
    ? null
    : row.value &&
        (activeTask(row.value.status) ||
          deliveries.value.some((d) => activeTask(d.status)))
      ? Math.min(10000, 2000 * Math.max(1, attempt.value))
      : null,
);
function startEdit() {
  draft.value = structuredClone(
    toRaw(content.value),
  ) as Models["ReportContent"];
  editRevision.value = row.value!.revision;
  editing.value = true;
}
watch([editing, sendOpen], ([edit, send], [wasEditing, wasSending]) => {
  if (!edit && !send && (wasEditing || wasSending)) void refresh();
});
async function cancelEdit() {
  await ElMessageBox.confirm("放弃当前未保存的修改？", "结束编辑")
    .then(() => {
      editing.value = false;
    })
    .catch(() => {});
}
async function save() {
  if (busy.value || !draft.value) return;
  busy.value = true;
  try {
    row.value = await api(
      "/reports/" + id,
      "PUT",
      revisionRequest(editRevision.value, draft.value),
    );
    editing.value = false;
    await refresh();
    ElMessage.success("已保存新修订");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    busy.value = false;
  }
}
async function review(decision: "approve" | "request_changes") {
  if (busy.value) return;
  busy.value = true;
  try {
    const { value } = await ElMessageBox.prompt(
      "请填写审核意见。",
      "审核报告",
      {
        inputType: "textarea",
        inputValidator: (v) =>
          (!!v?.trim() && v.length <= 2000) || "请填写 1–2000 字审核意见",
      },
    );
    row.value = await api("/reports/" + id + "/review", "POST", {
      expected_revision: row.value!.revision,
      decision,
      comment: value,
    });
    await refresh();
    ElMessage.success(decision === "approve" ? "审核通过" : "已记录退回意见");
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      error.value = e instanceof Error ? e.message : "审核失败";
  } finally {
    busy.value = false;
  }
}
async function exportReport(format: string) {
  if (busy.value) return;
  busy.value = true;
  try {
    await download(`/reports/${id}/export?format=${format}`);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "导出失败";
  } finally {
    busy.value = false;
  }
}
async function remove() {
  if (busy.value) return;
  busy.value = true;
  try {
    await ElMessageBox.confirm(
      "删除后报告将从列表和事故报告记录中移除，修订、发送记录及审计仍会保留。",
      "删除复盘报告",
      { type: "warning" },
    );
    await api("/reports/" + id, "DELETE");
    ElMessage.success("报告已删除");
    await router.push("/reports");
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      error.value = e instanceof Error ? e.message : "删除失败";
  } finally {
    busy.value = false;
  }
}
function openSend() {
  recipients.value = "";
  sendComment.value = "";
  sendKey.value = crypto.randomUUID();
  sendBody.value = undefined;
  sendError.value = "";
  sendOpen.value = true;
}
async function send() {
  if (busy.value) return;
  busy.value = true;
  sendError.value = "";
  try {
    if (!sendBody.value) {
      const emails = recipients.value.split(/[\s,;，；]+/).filter(Boolean);
      if (!emails.length || !sendComment.value.trim())
        throw new Error("请填写收件人与发送说明");
      if (emails.length > 20) throw new Error("最多填写 20 个收件人");
      sendBody.value = {
        expected_revision: row.value!.revision,
        recipients: emails,
        comment: sendComment.value,
        idempotency_key: sendKey.value,
      };
    }
    const d = await api<Models["DeliveryResponse"]>(
      "/reports/" + id + "/send",
      "POST",
      sendBody.value,
    );
    sendOpen.value = false;
    await refresh();
    ElMessage.success(
      d.status === "sent" ? "该请求已发送" : "发送请求已排队，可查看发送记录",
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 422) sendBody.value = undefined;
    sendError.value = e instanceof Error ? e.message : "发送请求失败";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/reports" class="subtle-link"
        >← 返回复盘报告</router-link
      >
      <div class="heading-copy">
        <h1>事故复盘报告</h1>
        <PageIntro />
        <p>
          {{ short(id) }} · v{{ row?.revision ?? 0 }} ·
          <router-link v-if="row" :to="'/incidents/' + row.incident_id"
            >关联事故 {{ short(row.incident_id) }}</router-link
          >
        </p>
        <p v-if="row">
          所属服务：{{
            row.resource_context?.services?.map((s) => s.name).join("、") ||
            "未关联服务"
          }}
          <br />关联资源：{{
            row.resource_context?.resources?.map((r) => r.name).join("、") ||
            "未识别资源"
          }}
        </p>
        <p
          v-if="row?.resource_context?.source === 'current_assets'"
          class="muted"
        >
          历史报告未冻结服务信息，以上按证据中的资源与现有资产关联展示。
        </p>
      </div>
    </div>

    <div class="actions">
      <el-button
        v-if="row && auth.can('reports:manage')"
        type="danger"
        :disabled="
          busy ||
          editing ||
          activeTask(row.status) ||
          deliveries.some((d) => activeTask(d.status))
        "
        @click="remove"
        >删除报告</el-button
      >
      <el-button :disabled="editing" @click="refresh">刷新</el-button
      ><el-button v-if="allowEdit && !editing" @click="startEdit"
        >编辑草稿</el-button
      ><template v-if="canExportReport(row?.status || '')"
        ><el-button :loading="busy" @click="exportReport('pdf')"
          >导出 PDF</el-button
        ><el-button :loading="busy" @click="exportReport('docx')"
          >导出 Word</el-button
        ><el-button :loading="busy" @click="exportReport('json')"
          >下载原始证据</el-button
        ><el-button
          v-if="auth.can('reports:manage')"
          type="primary"
          :disabled="busy"
          @click="openSend"
          >发起邮件</el-button
        ></template
      >
    </div>
  </div>
  <p v-if="row?.status === 'approved'" class="muted">
    PDF / Word 包含正文、关键时间线和证据摘要；完整原始证据可单独下载为 JSON。
  </p>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <div class="badge-line">
    <StatusTag :value="row?.status || 'unknown'" /><span class="muted"
      >生成于 {{ date(row?.finished_at) }} · 审核于
      {{ date(row?.reviewed_at) }}</span
    >
  </div>
  <el-alert
    v-if="row?.error_code"
    :title="reportError"
    type="error"
    :closable="false"
    class="error-box"
  />
  <el-alert
    v-if="contextSummary && !row?.error_code"
    :title="`生成材料已从 ${Math.ceil(contextSummary.snapshot_bytes / 1024)} KiB 精简至 ${Math.ceil(contextSummary.input_bytes / 1024)} KiB，完整原始证据保留在证据快照中。`"
    type="info"
    :closable="false"
    class="error-box"
  />
  <section class="panel report-workspace">
    <el-tabs class="page-tabs" v-model="view"
      ><el-tab-pane label="报告正文" name="report"
        ><el-form
          v-if="editing && draft"
          label-position="top"
          class="report-editor"
          ><el-form-item label="事故摘要" required
            ><el-input
              v-model="draft.summary"
              type="textarea"
              :rows="3"
              :maxlength="4000" /></el-form-item
          ><el-form-item label="影响范围" required
            ><el-input
              v-model="draft.impact"
              type="textarea"
              :rows="3"
              :maxlength="4000"
          /></el-form-item>
          <h2>原因与证据</h2>
          <div
            v-for="(cause, index) in draft.causes"
            :key="index"
            class="report-array"
          >
            <el-input
              v-model="cause.description"
              type="textarea"
              :rows="3"
              placeholder="原因描述"
            /><el-select v-model="cause.confidence" style="width: 180px"
              ><el-option label="已确认" value="confirmed" /><el-option
                label="疑似"
                value="suspected" /><el-option
                label="未知"
                value="unknown" /></el-select
            ><el-select
              v-model="cause.evidence_ids"
              multiple
              filterable
              style="width: 100%"
              placeholder="选择冻结快照中的证据"
              ><el-option
                v-for="e in evidenceOptions"
                :key="e.value"
                :label="e.label"
                :value="e.value" /></el-select
            ><el-button
              text
              type="danger"
              @click="draft.causes.splice(index, 1)"
              >移除此原因</el-button
            >
          </div>
          <el-button
            @click="
              draft.causes.push({
                description: '',
                confidence: 'suspected',
                evidence_ids: [],
              })
            "
            >添加原因</el-button
          >
          <div class="spacer"></div>
          <el-form-item label="响应与恢复过程" required
            ><el-input
              v-model="draft.response_and_recovery"
              type="textarea"
              :rows="5"
              :maxlength="4000"
          /></el-form-item>
          <h2>经验教训</h2>
          <div v-for="(_, i) in draft.lessons" :key="i" class="report-array">
            <el-input
              v-model="draft.lessons[i]"
              type="textarea"
              :rows="2"
            /><el-button text type="danger" @click="draft.lessons.splice(i, 1)"
              >移除</el-button
            >
          </div>
          <el-button @click="draft.lessons.push('')">添加经验</el-button>
          <div class="spacer"></div>
          <h2>改进建议</h2>
          <div
            v-for="(item, i) in draft.improvements"
            :key="i"
            class="report-array"
          >
            <el-input
              v-model="item.action"
              type="textarea"
              :rows="2"
              placeholder="建议措施"
            /><el-input v-model="item.owner" placeholder="负责人" /><el-input
              v-model="item.deadline"
              placeholder="计划完成时间"
            /><el-button
              text
              type="danger"
              @click="draft.improvements.splice(i, 1)"
              >移除</el-button
            >
          </div>
          <el-button
            @click="
              draft.improvements.push({
                action: '',
                owner: '待分配',
                deadline: '待确定',
              })
            "
            >添加改进建议</el-button
          >
          <div class="spacer"></div>
          <h2>证据缺口</h2>
          <div v-for="(_, i) in draft.data_gaps" :key="i" class="report-array">
            <el-input
              v-model="draft.data_gaps[i]"
              type="textarea"
              :rows="2"
            /><el-button
              text
              type="danger"
              @click="draft.data_gaps.splice(i, 1)"
              >移除</el-button
            >
          </div>
          <el-button @click="draft.data_gaps.push('')">添加缺口</el-button>
          <div class="form-hint">
            系统记录的证据缺口不能通过编辑消除，后端会校验。
          </div>
          <div class="spacer"></div>
          <el-form-item label="正文引用证据"
            ><el-select
              v-model="draft.evidence_ids"
              multiple
              filterable
              style="width: 100%"
              ><el-option
                v-for="e in evidenceOptions"
                :key="e.value"
                :label="e.label"
                :value="e.value" /></el-select
          ></el-form-item>
          <div class="actions">
            <el-button :disabled="busy" @click="cancelEdit">放弃修改</el-button
            ><el-button type="primary" :loading="busy" @click="save"
              >保存修订 v{{ editRevision + 1 }}</el-button
            >
          </div></el-form
        >
        <ReportBody
          v-else-if="content.summary"
          :content="content"
          :snapshot="row?.snapshot"
          :revision="row?.revision"
          :status="row?.status"
        />
        <div v-else class="empty-hint">
          {{
            activeTask(row?.status || "")
              ? "正在生成报告，请等待任务完成"
              : "暂无报告正文"
          }}
        </div>
        <div v-if="allowEdit && !editing" class="text-block">
          <h3>人工审核</h3>
          <p>
            审核当前 v{{ row?.revision }}。审核通过后开放导出和人工邮件发送。
          </p>
          <p v-if="row?.review_comment" class="muted">
            最近意见：{{ row.review_comment }}
          </p>
          <div class="actions">
            <el-button :loading="busy" @click="review('request_changes')"
              >退回修改</el-button
            ><el-button
              type="primary"
              :loading="busy"
              @click="review('approve')"
              >审核通过</el-button
            >
          </div>
        </div></el-tab-pane
      >
      <el-tab-pane label="整改与知识" name="improvements"
        ><ReportImprovements v-if="row" :report="row"
      /></el-tab-pane>
      <el-tab-pane label="版本历史" name="versions"
        ><el-table :data="versions" empty-text="暂无修订"
          ><el-table-column
            prop="revision"
            label="版本"
            width="100"
          /><el-table-column label="保存时间" min-width="180"
            ><template #default="{ row: r }">{{
              date(r.created_at)
            }}</template></el-table-column
          ><el-table-column label="作者"
            ><template #default="{ row: r }">{{
              short(r.author_id)
            }}</template></el-table-column
          ><el-table-column label="操作" width="220" fixed="right"
            ><template #default="{ row: r }"
              ><div class="table-actions">
                <el-button text type="primary" @click="chosenVersion = r"
                  >查看修订与当前版本</el-button
                >
              </div></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      ><el-tab-pane label="冻结证据" name="evidence"
        ><p class="muted">
          证据快照与事实附录只读。快照摘要：{{ row?.snapshot_hash }}
        </p>
        <template
          v-for="record in records.filter((r) => r.kind === 'analysis')"
          :key="record.id"
        >
          <DatabaseContext :context="record.data?.database_context" />
          <DatabaseContext
            v-for="e in record.data?.evidence || []"
            :key="e.id"
            :evidence="e"
          />
        </template>
        <JsonEvidence :value="row?.snapshot" /></el-tab-pane
      ><el-tab-pane
        v-if="auth.can('reports:manage')"
        label="发送记录"
        name="deliveries"
        ><el-table :data="deliveries" empty-text="尚未发送邮件"
          ><el-table-column label="收件人" min-width="260"
            ><template #default="{ row: d }">{{
              d.recipients.join("；")
            }}</template></el-table-column
          ><el-table-column
            prop="revision"
            label="版本"
            width="90"
          /><el-table-column label="状态" width="130"
            ><template #default="{ row: d }"
              ><StatusTag :value="d.status" /></template></el-table-column
          ><el-table-column prop="error_code" label="说明" /><el-table-column
            label="完成时间"
            min-width="180"
            ><template #default="{ row: d }">{{
              date(d.finished_at)
            }}</template></el-table-column
          ></el-table
        ><el-alert
          v-if="deliveries.some((d) => d.status === 'unknown')"
          title="存在结果未知的派发，需先核对投递记录，不能直接判断未发送。"
          type="warning"
          :closable="false" /></el-tab-pane
    ></el-tabs>
  </section>
  <el-dialog
    :model-value="!!chosenVersion"
    title="修订对照"
    width="1000px"
    @update:model-value="chosenVersion = undefined"
    ><div class="two-equal">
      <div>
        <h2>v{{ chosenVersion?.revision }}</h2>
        <JsonEvidence :value="chosenVersion?.content" />
      </div>
      <div>
        <h2>当前 v{{ row?.revision }}</h2>
        <JsonEvidence :value="content" />
      </div></div></el-dialog
  ><el-dialog
    v-model="sendOpen"
    title="人工发起复盘邮件"
    width="560px"
    :close-on-click-modal="false"
    ><el-alert
      title="发送当前已审核版本的 PDF。请确认收件人与发送说明。"
      type="info"
      :closable="false"
      class="error-box"
    /><el-alert
      v-if="sendError"
      :title="sendError"
      type="error"
      :closable="false"
      class="error-box"
    /><el-form label-position="top"
      ><el-form-item label="收件人" required
        ><el-input
          v-model="recipients"
          :disabled="!!sendBody"
          type="textarea"
          :rows="3"
          placeholder="填写 1–20 个邮箱，用分号或换行分隔" /></el-form-item
      ><el-form-item label="发送说明" required
        ><el-input
          v-model="sendComment"
          :disabled="!!sendBody"
          type="textarea"
          :rows="3"
          :maxlength="2000" /></el-form-item
    ></el-form>
    <p v-if="sendBody" class="form-hint">
      再次提交复用相同请求，防止网络异常造成重复发送。
    </p>
    <template #footer
      ><el-button :disabled="busy" @click="sendOpen = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="send">{{
        sendBody ? "重试原发送请求" : "确认发送"
      }}</el-button></template
    ></el-dialog
  >
</template>
