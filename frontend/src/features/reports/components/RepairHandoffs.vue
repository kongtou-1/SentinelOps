<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { all, api, download, query, request, type Models } from "@/api/client";
import { date, label, short } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import DiagnosisSources from "@/features/diagnosis/components/DiagnosisSources.vue";
import SourceEvidence from "@/features/diagnosis/components/SourceEvidence.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import type { Row } from "@/types/records";

const props = defineProps<{
  incidentId: string;
  visible: boolean;
  merged: boolean;
}>();
const emit = defineEmits<{ changed: [] }>();
const auth = useAuth(),
  rows = ref<Models["HandoffResponse"][]>([]),
  selected = ref<Models["HandoffResponse"]>(),
  page = ref(1),
  total = ref(0),
  busy = ref(false),
  error = ref(""),
  drawer = ref(false),
  dialog = ref(""),
  analyses = ref<Models["AnalysisResponse"][]>([]),
  applications = ref<Models["ApplicationResponse"][]>([]),
  releases = ref<Models["ReleaseResponse"][]>([]),
  deployments = ref<Models["DeploymentResponse"][]>([]),
  resultApp = ref("");
const createForm = reactive({
  analysis_id: "",
  application_id: "",
  reproduction: "",
  criteria: "",
});
const resultForm = reactive({
  repair_commit: "",
  change_summary: "",
  test_result: "",
  release_id: "",
  deployment_id: "",
});
const verifyForm = reactive({
  result_id: "",
  outcome: "runtime_recovered",
  observed_at: "",
  evidence: "",
  acceptance_results: "",
});
const outcomes: Record<string, string> = {
  runtime_recovered: "服务恢复（人工登记）",
  fix_validated: "修复验收通过（人工登记）",
  failed: "验证未通过",
  inconclusive: "验证尚无结论",
};
const writable = computed(() => auth.can("repairs:write") && !props.merged);
const sourceApps = computed(() => {
  const context = analyses.value.find((a) => a.id === createForm.analysis_id)
    ?.source_context as Row | undefined;
  const unique = new Map<string, string>();
  for (const b of context?.bindings || [])
    unique.set(b.application_id, b.identity?.service_code || b.application_id);
  return [...unique].map(([id, name]) => ({ id, name }));
});
watch(
  () => createForm.analysis_id,
  () => {
    createForm.application_id =
      sourceApps.value.length === 1 ? sourceApps.value[0]!.id : "";
  },
);
const resultRows = computed(() => (selected.value?.results || []) as Row[]);
const packet = computed(() => (selected.value?.packet || {}) as Row);
const verificationRows = computed(
  () => (selected.value?.verifications || []) as Row[],
);
const deploymentOptions = computed(() =>
  deployments.value.filter((d) => d.release_id === resultForm.release_id),
);

async function load() {
  try {
    const r = await request<Models["HandoffResponse"][]>(
      `/incidents/${props.incidentId}/repair-handoffs` +
        query({ offset: (page.value - 1) * 10, limit: 10 }),
    );
    rows.value = r.data;
    total.value = r.total ?? 0;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
watch(
  () => props.visible,
  (v) => {
    if (v) void load();
  },
  { immediate: true },
);
async function act(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  try {
    await work();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    busy.value = false;
  }
}
function inspect(row: Models["HandoffResponse"]) {
  selected.value = row;
  drawer.value = true;
}
function openCreate() {
  void act(async () => {
    analyses.value = (
      await all<Models["AnalysisResponse"]>(
        `/incidents/${props.incidentId}/analyses`,
      )
    ).filter((a) => a.status === "completed");
    if (!analyses.value.length)
      throw new Error("请先完成正式诊断；私有会话结论需要手动采用。");
    Object.assign(createForm, {
      analysis_id: analyses.value[0]!.id,
      application_id: "",
      reproduction: "",
      criteria: "",
    });
    dialog.value = "create";
  });
}
async function saved(value: Models["HandoffResponse"]) {
  selected.value = value;
  drawer.value = true;
  dialog.value = "";
  await load();
  emit("changed");
  ElMessage.success("记录已保存");
}
function generate() {
  void act(async () => {
    const criteria = createForm.criteria
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!criteria.length) throw new Error("请填写具体的验收条件，每行一条。");
    if (sourceApps.value.length > 1 && !createForm.application_id)
      throw new Error("请选择本次修复的服务。");
    await saved(
      await api(`/incidents/${props.incidentId}/repair-handoffs`, "POST", {
        analysis_id: createForm.analysis_id,
        application_id: createForm.application_id || null,
        reproduction: createForm.reproduction,
        acceptance_criteria: criteria,
      }),
    );
  });
}
async function loadReleases() {
  resultForm.release_id = "";
  resultForm.deployment_id = "";
  releases.value = [];
  deployments.value = [];
  if (resultApp.value)
    [releases.value, deployments.value] = await Promise.all([
      all<Models["ReleaseResponse"]>(
        `/applications/${resultApp.value}/releases`,
      ),
      all<Models["DeploymentResponse"]>(
        `/applications/${resultApp.value}/deployments`,
      ),
    ]);
}
function openResult() {
  void act(async () => {
    Object.assign(resultForm, {
      repair_commit: "",
      change_summary: "",
      test_result: "",
      release_id: "",
      deployment_id: "",
    });
    applications.value = await all("/applications");
    resultApp.value =
      selected.value?.application_id ||
      String(
        resultRows.value.find((r) => r.application_id)?.application_id || "",
      );
    await loadReleases();
    dialog.value = "result";
  });
}
function addResult() {
  void act(async () => {
    if (!resultForm.change_summary.trim() || !resultForm.test_result.trim())
      throw new Error("请填写修改说明和测试命令、结果及未验证范围。");
    await saved(
      await api(`/repair-handoffs/${selected.value!.id}/results`, "POST", {
        ...resultForm,
        repair_commit: resultForm.repair_commit.trim() || null,
        release_id: resultForm.release_id || null,
        deployment_id: resultForm.deployment_id || null,
      }),
    );
  });
}
function openVerify() {
  Object.assign(verifyForm, {
    result_id: resultRows.value.at(-1)?.id || "",
    outcome: "runtime_recovered",
    observed_at: new Date().toISOString(),
    evidence: "",
    acceptance_results: "",
  });
  dialog.value = "verify";
}
function addVerification() {
  void act(async () => {
    if (
      !verifyForm.result_id ||
      !verifyForm.evidence.trim() ||
      !verifyForm.acceptance_results.trim() ||
      !verifyForm.observed_at
    )
      throw new Error("请选择修复记录，填写证据时间、验证证据和逐条验收结果。");
    await saved(
      await api(
        `/repair-handoffs/${selected.value!.id}/verifications`,
        "POST",
        verifyForm,
      ),
    );
  });
}
function exportFile(format: string) {
  void act(async () => {
    await download(
      `/repair-handoffs/${selected.value!.id}/export?format=${format}`,
    );
  });
}
</script>

<template>
  <div class="panel-heading">
    <div class="actions">
      <el-button @click="load">刷新交接</el-button
      ><el-button
        v-if="writable"
        type="primary"
        :loading="busy"
        @click="openCreate"
        >生成修复交接</el-button
      >
    </div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-table :data="rows">
    <el-table-column label="生成时间"
      ><template #default="{ row }">{{
        date(row.created_at)
      }}</template></el-table-column
    >
    <el-table-column label="正式诊断"
      ><template #default="{ row }">{{
        short(row.analysis_id)
      }}</template></el-table-column
    >
    <el-table-column label="修复 / 验证"
      ><template #default="{ row }"
        >{{ row.results.length }} / {{ row.verifications.length }}</template
      ></el-table-column
    >
    <el-table-column label="查看" width="120"
      ><template #default="{ row }"
        ><el-button link type="primary" @click="inspect(row)"
          >交接详情</el-button
        ></template
      ></el-table-column
    >
    <template #empty>尚无修复交接；请先完成正式诊断。</template>
  </el-table>
  <div class="pagination">
    <el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="10"
      layout="prev, pager, next"
      @current-change="load"
    />
  </div>
  <el-drawer v-model="drawer" title="修复交接详情" size="min(100vw, 960px)">
    <template v-if="selected">
      <div class="actions">
        <el-button :loading="busy" @click="exportFile('markdown')"
          >导出 Markdown</el-button
        ><el-button :loading="busy" @click="exportFile('json')"
          >导出 JSON</el-button
        ><el-button v-if="writable" :loading="busy" @click="openResult"
          >登记修复结果</el-button
        ><el-button
          v-if="writable"
          :disabled="!resultRows.length"
          @click="openVerify"
          >登记验证</el-button
        >
      </div>
      <div class="text-block">
        <h3>冻结诊断</h3>
        <p>{{ packet.diagnosis?.summary }}</p>
        <p class="form-hint">
          {{ selected.id }} · {{ date(String(packet.captured_at)) }}
        </p>
        <p class="hash">SHA256 {{ selected.packet_hash }}</p>
        <p>{{ packet.boundary }}</p>
      </div>
      <DiagnosisSources :context="packet.source_context" />
      <div class="text-block">
        <h3>复现线索</h3>
        <p class="prewrap">{{ packet.reproduction || "未提供" }}</p>
        <h3>验收条件</h3>
        <p v-for="(s, i) in packet.acceptance_criteria" :key="i">
          {{ Number(i) + 1 }}. {{ s }}
        </p>
        <h3>证据缺口</h3>
        <p v-for="(s, i) in packet.data_gaps" :key="i">
          {{ label(String(s)) }}
        </p>
      </div>
      <SourceEvidence
        v-for="(e, i) in packet.evidence"
        :key="i"
        :evidence="e"
      />
      <h3>修复结果</h3>
      <p v-if="!resultRows.length" class="empty-hint">
        尚未登记修改和测试结果。
      </p>
      <div v-for="r in resultRows" :key="String(r.id)" class="text-block">
        <p>
          {{ date(String(r.recorded_at)) }} · {{ short(String(r.id)) }} · 登记人
          {{ short(String(r.recorded_by)) }}
        </p>
        <p class="prewrap">{{ r.change_summary }}</p>
        <p>提交：{{ r.repair_commit || "未登记 Git 提交" }}</p>
        <p class="prewrap">测试（人工提供）：{{ r.test_result }}</p>
        <p>
          发布：{{ r.release?.version || "未关联" }} · 部署：{{
            short(r.deployment?.id)
          }}
        </p>
        <p v-if="r.association_basis === 'human_declared'" class="form-hint">
          服务关联由人工声明，原诊断缺少服务绑定。
        </p>
        <el-collapse>
          <el-collapse-item title="版本身份与关联依据">
            <JsonEvidence :value="r" />
          </el-collapse-item>
        </el-collapse>
      </div>
      <h3>部署后验证</h3>
      <p class="form-hint">
        服务恢复与代码修复验收分别记录。测试和验收结论由登记人提供，不自动改变事故、审批或执行状态。
      </p>
      <p v-if="!verificationRows.length" class="empty-hint">尚无验证记录。</p>
      <div v-for="v in verificationRows" :key="String(v.id)" class="text-block">
        <h3>{{ outcomes[String(v.outcome)] }}</h3>
        <p>
          证据时间 {{ date(String(v.observed_at)) }} · 登记人
          {{ short(String(v.recorded_by)) }}
        </p>
        <p class="prewrap">{{ v.evidence }}</p>
        <p class="prewrap">逐条验收：{{ v.acceptance_results }}</p>
        <el-collapse>
          <el-collapse-item title="告警恢复核验与登记详情">
            <JsonEvidence :value="v" />
          </el-collapse-item>
        </el-collapse>
      </div>
      <el-collapse
        ><el-collapse-item title="完整冻结材料与运行证据"
          ><JsonEvidence :value="packet" /></el-collapse-item
      ></el-collapse>
    </template>
  </el-drawer>
  <el-dialog
    :model-value="!!dialog"
    :title="
      dialog === 'create'
        ? '生成修复交接'
        : dialog === 'result'
          ? '登记修复结果'
          : '登记部署后验证'
    "
    width="min(92vw, 720px)"
    append-to-body
    @close="dialog = ''"
  >
    <el-form v-if="dialog === 'create'" label-position="top">
      <el-form-item label="正式诊断" required
        ><el-select v-model="createForm.analysis_id"
          ><el-option
            v-for="a in analyses"
            :key="a.id"
            :value="a.id"
            :label="
              date(a.created_at) +
              ' · ' +
              String(a.result.summary || short(a.id))
            " /></el-select
      ></el-form-item>
      <el-form-item v-if="sourceApps.length" label="修复服务"
        ><el-select v-model="createForm.application_id"
          ><el-option
            v-for="a in sourceApps"
            :key="a.id"
            :value="a.id"
            :label="a.name" /></el-select
      ></el-form-item>
      <el-form-item label="复现步骤或线索（没有时可留空）"
        ><el-input
          v-model="createForm.reproduction"
          type="textarea"
          :rows="3"
          maxlength="10000"
      /></el-form-item>
      <el-form-item label="验收条件（每行一条）" required
        ><el-input
          v-model="createForm.criteria"
          type="textarea"
          :rows="4"
          placeholder="例如：请求超时测试通过；新版本连续观测 10 分钟无错误"
      /></el-form-item>
    </el-form>
    <el-form v-else-if="dialog === 'result'" label-position="top">
      <el-form-item label="修改说明" required
        ><el-input
          v-model="resultForm.change_summary"
          type="textarea"
          maxlength="10000"
      /></el-form-item>
      <el-form-item label="完整 Git commit（本地目录修复可留空）"
        ><el-input v-model="resultForm.repair_commit" maxlength="64"
      /></el-form-item>
      <el-form-item label="测试命令、实际结果和未验证范围" required
        ><el-input
          v-model="resultForm.test_result"
          type="textarea"
          :rows="3"
          maxlength="10000"
      /></el-form-item>
      <el-form-item label="服务"
        ><el-select
          v-model="resultApp"
          :disabled="
            !!selected?.application_id ||
            resultRows.some((r) => r.application_id)
          "
          @change="act(loadReleases)"
          ><el-option
            v-for="a in applications"
            :key="a.id"
            :label="a.name"
            :value="a.id" /></el-select
      ></el-form-item>
      <el-form-item label="新发布（由管理员在应用与服务中登记）"
        ><el-select
          v-model="resultForm.release_id"
          clearable
          @change="resultForm.deployment_id = ''"
          ><el-option
            v-for="r in releases"
            :key="r.id"
            :value="r.id"
            :label="r.version" /></el-select
      ></el-form-item>
      <el-form-item label="新部署"
        ><el-select v-model="resultForm.deployment_id" clearable
          ><el-option
            v-for="d in deploymentOptions"
            :key="d.id"
            :value="d.id"
            :label="
              d.environment + ' · ' + date(d.started_at) + ' · ' + short(d.id)
            " /></el-select
      ></el-form-item>
    </el-form>
    <el-form v-else label-position="top">
      <el-form-item label="对应修复结果" required
        ><el-select v-model="verifyForm.result_id"
          ><el-option
            v-for="r in resultRows"
            :key="String(r.id)"
            :value="r.id"
            :label="
              short(String(r.id)) + ' · ' + String(r.change_summary)
            " /></el-select
      ></el-form-item>
      <el-form-item label="人工验证结论" required
        ><el-select v-model="verifyForm.outcome"
          ><el-option
            v-for="(s, k) in outcomes"
            :key="k"
            :value="k"
            :label="s" /></el-select
      ></el-form-item>
      <el-form-item label="证据观测时间" required
        ><el-date-picker
          v-model="verifyForm.observed_at"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
      /></el-form-item>
      <el-form-item label="验证方法、观测数据及证据位置" required
        ><el-input
          v-model="verifyForm.evidence"
          type="textarea"
          :rows="3"
          maxlength="10000"
      /></el-form-item>
      <el-form-item label="逐条验收条件与实际结果" required
        ><el-input
          v-model="verifyForm.acceptance_results"
          type="textarea"
          :rows="3"
          maxlength="10000"
      /></el-form-item>
    </el-form>
    <template #footer
      ><el-button @click="dialog = ''">取消</el-button
      ><el-button
        type="primary"
        :loading="busy"
        @click="
          dialog === 'create'
            ? generate()
            : dialog === 'result'
              ? addResult()
              : addVerification()
        "
        >保存</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.prewrap {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.hash {
  font-family: monospace;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.actions {
  flex-wrap: wrap;
}
</style>
