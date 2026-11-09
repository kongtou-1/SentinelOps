<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, query } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import type { Row } from "@/types/records";
const auth = useAuth(),
  route = useRoute(),
  cases = ref<Row[]>([]),
  runs = ref<Row[]>([]),
  caseId = ref(""),
  error = ref(""),
  chosen = ref<Row[]>([]),
  comparison = ref<Row>(),
  details = ref<Row>();
const summary = ref<Row>({}),
  skills = ref<Row[]>([]);
const createOpen = ref(!!route.query.analysis),
  replayOpen = ref(false),
  reviewTarget = ref<Row>(),
  busy = ref(false);
const casePreview = ref<Row>();
const form = ref({
  analysis_id: String(route.query.analysis || ""),
  title: "",
  previous_id: "",
  human_label: "",
  tools: "",
  terms: "",
  confirm_sanitized: false,
});
const replayForm = ref({
  provider_kind: "mock",
  skill_ids: [] as string[],
  prompt: "根据冻结案例进行只读诊断，先加载 Skill 并引用证据，输出诊断 JSON。",
});
const reviewForm = ref({
  verdict: "inconclusive",
  confirmed_cause: "",
  evidence: "",
});
const verdicts: Record<string, string> = {
  correct: "正确",
  partial: "部分正确",
  incorrect: "错误",
  inconclusive: "无法判断",
};
const statuses: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
};
function sourceLabel(row: Row) {
  return row.provider_kind === "baseline" || row.provider_kind === "mock"
    ? "模拟基线"
    : row.provider_kind === "mock_provider" ||
        row.config?.profile?.provider_kind === "mock"
      ? "模拟 Provider"
      : "真实模型";
}
async function load() {
  try {
    [summary.value, skills.value] = await Promise.all([
      api<Row>("/evaluations/summary"),
      api<Row[]>("/skills?limit=100"),
    ]);
    cases.value = await api("/evaluation-cases?limit=100");
    runs.value = await api(
      "/evaluation-runs" + query({ case_id: caseId.value, limit: 100 }),
    );
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
const { refresh } = usePoll(load, () =>
  createOpen.value || replayOpen.value || reviewTarget.value ? null : 10000,
);
const split = (s: string) =>
  s
    .split(/[,，\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
watch(
  () => form.value.analysis_id,
  () => {
    casePreview.value = undefined;
    form.value.confirm_sanitized = false;
  },
);
async function previewCase() {
  busy.value = true;
  error.value = "";
  casePreview.value = undefined;
  form.value.confirm_sanitized = false;
  const identifier = form.value.analysis_id;
  try {
    const result = await api<Row>(`/analyses/${identifier}/evaluation-preview`);
    if (form.value.analysis_id === identifier) casePreview.value = result;
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function create() {
  if (
    !casePreview.value ||
    casePreview.value.analysis_id !== form.value.analysis_id
  )
    return;
  busy.value = true;
  try {
    await api("/evaluation-cases", "POST", {
      analysis_id: form.value.analysis_id,
      title: form.value.title,
      previous_id: form.value.previous_id || null,
      human_label: form.value.human_label,
      confirm_sanitized: form.value.confirm_sanitized,
      expected_snapshot_hash: casePreview.value.content_hash,
      checks: {
        required_tools: split(form.value.tools),
        cause_terms: split(form.value.terms),
      },
    });
    createOpen.value = false;
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function replay() {
  busy.value = true;
  try {
    if (replayForm.value.provider_kind === "model")
      await ElMessageBox.confirm(
        "使用当前模型配置执行一次回放，可能产生费用。",
        "发起真实模型回放",
        { confirmButtonText: "确认发起", cancelButtonText: "取消" },
      );
    await api(`/evaluation-cases/${caseId.value}/replays`, "POST", {
      ...replayForm.value,
      idempotency_key: crypto.randomUUID(),
      confirm_paid: replayForm.value.provider_kind === "model",
    });
    replayOpen.value = false;
    ElMessage.success("评测回放任务已提交");
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function compare() {
  try {
    comparison.value = await api(
      "/evaluation-runs/compare" +
        query({ left: chosen.value[0]!.id, right: chosen.value[1]!.id }),
    );
  } catch (e) {
    error.value = String(e);
  }
}
async function review() {
  try {
    await api(`/evaluation-runs/${reviewTarget.value!.id}/review`, "POST", {
      ...reviewForm.value,
      expected_version: reviewTarget.value!.version,
    });
    reviewTarget.value = undefined;
    await refresh();
  } catch (e) {
    error.value = String(e);
  }
}
</script>
<template>
  <section class="page-header">
    <div class="page-heading">
      <h1>效果评价</h1>
      <PageIntro />

      <div class="actions">
        <el-button @click="refresh">刷新</el-button>
        <el-button
          v-if="auth.can('knowledge:manage')"
          @click="createOpen = true"
          >收录案例</el-button
        ><el-button
          v-if="auth.can('analyses:run')"
          :disabled="!caseId"
          type="primary"
          @click="replayOpen = true"
          >发起回放</el-button
        >
      </div>
    </div>
  </section>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <section class="panel">
    <h2>最近 30 天</h2>
    <el-descriptions :column="3" border
      ><el-descriptions-item label="完整预测窗口">{{
        summary.observed_horizons ?? 0
      }}</el-descriptions-item
      ><el-descriptions-item label="越界判断一致率">{{
        summary.threshold_match_rate == null
          ? "未知"
          : (summary.threshold_match_rate * 100).toFixed(1) + "%"
      }}</el-descriptions-item
      ><el-descriptions-item label="不完整窗口">{{
        summary.prediction_outcomes?.incomplete || 0
      }}</el-descriptions-item>
      <el-descriptions-item label="预测越界命中率">{{
        summary.breach_precision == null
          ? "未知"
          : (summary.breach_precision * 100).toFixed(1) + "%"
      }}</el-descriptions-item
      ><el-descriptions-item label="实际越界召回率">{{
        summary.breach_recall == null
          ? "未知"
          : (summary.breach_recall * 100).toFixed(1) + "%"
      }}</el-descriptions-item
      ><el-descriptions-item label="平均提前量">{{
        summary.mean_lead_seconds == null
          ? "未知"
          : summary.mean_lead_seconds.toFixed(1) + " 秒"
      }}</el-descriptions-item
      ><el-descriptions-item label="人工确认事故">{{
        summary.confirmed_incidents ?? 0
      }}</el-descriptions-item></el-descriptions
    ><el-table :data="summary.replays || []"
      ><el-table-column label="回放来源"
        ><template #default="{ row }">{{
          sourceLabel(row)
        }}</template></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          statuses[row.status]
        }}</template></el-table-column
      ><el-table-column prop="count" label="次数"
    /></el-table>
  </section>
  <section class="panel">
    <h2>版本化案例</h2>
    <el-select
      v-model="caseId"
      clearable
      filterable
      placeholder="全部案例"
      @change="refresh"
      ><el-option
        v-for="c in cases"
        :key="c.id"
        :value="c.id"
        :label="c.title + ' · v' + c.version" /></el-select
    ><el-table :data="cases"
      ><el-table-column prop="title" label="案例" /><el-table-column
        prop="version"
        label="版本"
      /><el-table-column prop="human_label" label="人工标签" /><el-table-column
        label="操作"
        ><template #default="{ row }"
          ><el-button text @click="details = row">冻结数据</el-button
          ><el-button
            v-if="auth.can('knowledge:manage')"
            text
            @click="
              form.previous_id = row.id;
              form.analysis_id = row.analysis_id;
              form.title = row.title;
              createOpen = true;
            "
            >新增版本</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
  <section class="panel">
    <div class="panel-heading">
      <h2>回放结果</h2>
      <el-button :disabled="chosen.length !== 2" @click="compare"
        >比较所选</el-button
      >
    </div>
    <el-table :data="runs" @selection-change="chosen = $event"
      ><el-table-column type="selection" /><el-table-column label="来源"
        ><template #default="{ row }">{{
          sourceLabel(row)
        }}</template></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          statuses[row.status]
        }}</template></el-table-column
      ><el-table-column label="模型"
        ><template #default="{ row }">{{
          row.config.profile.model
        }}</template></el-table-column
      ><el-table-column label="结构"
        ><template #default="{ row }">{{
          row.result.checks?.structure_valid == null
            ? "—"
            : row.result.checks.structure_valid
              ? "通过"
              : "不通过"
        }}</template></el-table-column
      ><el-table-column label="引用"
        ><template #default="{ row }">{{
          row.result.checks?.references_valid == null
            ? "未知"
            : row.result.checks.references_valid
              ? "通过"
              : "不通过"
        }}</template></el-table-column
      ><el-table-column label="耗时"
        ><template #default="{ row }"
          >{{ row.result.duration_ms ?? "—" }} ms</template
        ></el-table-column
      ><el-table-column label="人工结论"
        ><template #default="{ row }">{{
          verdicts[row.review.verdict] || "待评价"
        }}</template></el-table-column
      ><el-table-column label="创建时间"
        ><template #default="{ row }">{{
          date(row.created_at)
        }}</template></el-table-column
      ><el-table-column label="操作"
        ><template #default="{ row }"
          ><el-button text @click="details = row">查看</el-button
          ><el-button
            v-if="
              auth.can('analyses:run') &&
              !['queued', 'running'].includes(row.status)
            "
            text
            @click="reviewTarget = row"
            >评价</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
  <el-dialog v-model="createOpen" title="收录故障案例"
    ><el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    /><el-form label-width="100px"
      ><el-form-item label="正式诊断 ID" required
        ><el-input v-model="form.analysis_id" /></el-form-item
      ><el-form-item label="冻结证据"
        ><el-button
          :loading="busy"
          :disabled="!form.analysis_id"
          @click="previewCase"
          >预览冻结证据</el-button
        ></el-form-item
      ><el-collapse v-if="casePreview"
        ><el-collapse-item title="冻结工具记录" name="records"
          ><JsonEvidence
            :value="casePreview.snapshot" /></el-collapse-item></el-collapse
      ><el-form-item label="标题" required
        ><el-input v-model="form.title" :maxlength="200" /></el-form-item
      ><el-form-item label="人工标签" required
        ><el-input
          v-model="form.human_label"
          type="textarea"
          :maxlength="4000" /></el-form-item
      ><el-form-item label="必需工具"
        ><el-input v-model="form.tools" type="textarea" /></el-form-item
      ><el-form-item label="根因关键词"
        ><el-input v-model="form.terms" type="textarea" /></el-form-item
      ><el-checkbox v-model="form.confirm_sanitized"
        >已核对冻结证据并确认完成脱敏</el-checkbox
      ></el-form
    ><template #footer
      ><el-button
        type="primary"
        :loading="busy"
        :disabled="!form.confirm_sanitized || !casePreview"
        @click="create"
        >保存版本</el-button
      ></template
    ></el-dialog
  >
  <el-dialog v-model="replayOpen" title="发起案例回放"
    ><el-form label-width="100px"
      ><el-form-item label="来源" required
        ><el-select v-model="replayForm.provider_kind"
          ><el-option label="模拟基线" value="mock" /><el-option
            label="真实模型"
            value="model" /></el-select></el-form-item
      ><el-form-item label="Skill 版本"
        ><el-select v-model="replayForm.skill_ids" multiple clearable
          ><el-option
            v-for="skill in skills.filter(
              (s) =>
                s.enabled &&
                (!s.expires_at ||
                  new Date(s.expires_at).getTime() > Date.now()),
            )"
            :key="skill.id"
            :value="skill.id"
            :label="
              skill.title + ' · v' + skill.version
            " /></el-select></el-form-item
      ><el-form-item label="提示词" required
        ><el-input
          v-model="replayForm.prompt"
          type="textarea"
          :rows="6"
          :maxlength="8000" /></el-form-item></el-form
    ><template #footer
      ><el-button type="primary" :loading="busy" @click="replay"
        >发起一次回放</el-button
      ></template
    ></el-dialog
  >
  <el-dialog
    :model-value="!!reviewTarget"
    title="回放人工评价"
    @close="reviewTarget = undefined"
    ><el-form label-width="100px"
      ><el-form-item label="结论" required
        ><el-select v-model="reviewForm.verdict"
          ><el-option
            v-for="(v, k) in verdicts"
            :key="k"
            :label="v"
            :value="k" /></el-select></el-form-item
      ><el-form-item label="确认根因"
        ><el-input
          v-model="reviewForm.confirmed_cause"
          type="textarea"
          :maxlength="4000" /></el-form-item
      ><el-form-item label="依据" required
        ><el-input
          v-model="reviewForm.evidence"
          type="textarea"
          :maxlength="8000" /></el-form-item></el-form
    ><template #footer
      ><el-button
        type="primary"
        :disabled="!reviewForm.evidence.trim()"
        @click="review"
        >提交评价</el-button
      ></template
    ></el-dialog
  >
  <el-dialog
    :model-value="!!details"
    title="冻结记录"
    width="80%"
    @close="details = undefined"
    ><JsonEvidence :value="details || {}"
  /></el-dialog>
  <el-dialog
    :model-value="!!comparison"
    title="版本比较"
    width="90%"
    @close="comparison = undefined"
    ><el-tag>{{
      comparison?.comparable_provider ? "相同来源" : "来源不同，分开统计"
    }}</el-tag>
    <div class="two-equal">
      <JsonEvidence :value="comparison?.left || {}" /><JsonEvidence
        :value="comparison?.right || {}"
      /></div
  ></el-dialog>
</template>
