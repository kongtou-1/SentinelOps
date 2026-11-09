<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { useRoute, useRouter } from "vue-router";
import { all, api, query, request, type Models } from "@/api/client";
import ExportCsvButton from "@/components/records/ExportCsvButton.vue";
import { itemExportColumns } from "@/features/inspections/exports";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import {
  errorNames,
  errorText,
  coverage,
  trialLimit,
  runConclusion,
  health,
  runName,
  runResult,
  stateNames,
  tone,
  type Item,
  type Run,
} from "@/features/inspections/ui";
import { itemTitle } from "@/features/inspections/display";
import RunConclusion from "@/features/inspections/components/RunConclusion.vue";
import ItemDrawer from "@/features/inspections/components/ItemDrawer.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import CheckResults from "@/features/inspections/components/CheckResults.vue";
import ExecutionEvidence from "@/features/inspections/components/ExecutionEvidence.vue";
import RunTrajectory from "@/features/inspections/components/RunTrajectory.vue";
import { activeRun } from "@/features/inspections/progress";
import AgentResults from "@/features/knowledge/components/AgentResults.vue";
function backToTasks() {
  const target = String(route.query.return_to || "");
  return target === "/inspections" ||
    target.startsWith("/inspections?") ||
    target === "/inspections/plans" ||
    target.startsWith("/inspections/plans?")
    ? target
    : "/inspections";
}
const triggers: Record<string, string> = {
  manual: "手动执行",
  automatic: "定时执行",
  event: "事件触发",
  scan: "扫描触发",
};
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const run = ref<Run>(),
  tab = ref("overview"),
  rows = ref<Item[]>([]),
  search = ref(""),
  result = ref(""),
  page = ref(1),
  size = ref(20),
  total = ref(0),
  error = ref(""),
  loading = ref(false),
  busy = ref(false),
  drawer = ref(false),
  selected = ref("");
const processCheck = ref<Models["InspectionCheckResultResponse"]>();
const detailedEvidence = ref(false);
function viewProcess(check: Models["InspectionCheckResultResponse"]) {
  processCheck.value = check;
  detailedEvidence.value = true;
  drawer.value = false;
  tab.value = "process";
}
const counts = computed(() => (run.value ? health(run.value) : {})),
  scope = computed(() => (run.value ? coverage(run.value) : undefined)),
  active = computed(
    () =>
      run.value &&
      ["queued", "collecting", "evaluating"].includes(run.value.status),
  );
const coveragePercent = computed(() => {
  const value = scope.value;
  return value?.expected_is_final && value.checks_expected > 0
    ? Math.min(
        100,
        Math.floor((100 * value.checks_completed) / value.checks_expected),
      )
    : undefined;
});
let revision = 0;
let openedRun = "";
function currentFilters() {
  return { search: search.value, result: result.value };
}
function exportRows(signal: AbortSignal) {
  return all<Item>(
    `/inspection-runs/${route.params.id}/items` + query(currentFilters()),
    signal,
  );
}
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const [detail, items] = await Promise.all([
      api<Run>(`/inspection-runs/${route.params.id}`),
      request<Item[]>(
        `/inspection-runs/${route.params.id}/items` +
          query({
            ...currentFilters(),
            offset: (page.value - 1) * size.value,
            limit: size.value,
          }),
      ),
    ]);
    if (current !== revision) return;
    if (openedRun !== detail.id) {
      openedRun = detail.id;
      processCheck.value = undefined;
      detailedEvidence.value = false;
      tab.value = activeRun(detail.status) ? "process" : "overview";
    }
    run.value = detail;
    rows.value = items.data;
    total.value = items.total || 0;
    error.value = "";
  } catch (e) {
    if (current === revision) error.value = errorText(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch([search, result, size], () => {
  page.value = 1;
  void load();
});
watch(page, () => void load());
usePoll(load, () => (active.value ? 3000 : null));
function inspect(item: Item) {
  selected.value = item.id;
  drawer.value = true;
}
async function stop() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "停止本次巡检？已产生的检查结果会保留，后续定时巡检仍按原计划运行。",
        "停止执行",
      ))
    )
      return;
    await api(`/inspection-runs/${run.value!.id}/cancel`, "POST");
    ElMessage.success("本次巡检已停止");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function resume() {
  if (busy.value || !run.value) return;
  busy.value = true;
  try {
    const next = await api<Run>(
      `/inspection-runs/${run.value.id}/resume`,
      "POST",
    );
    await router.push(`/inspections/runs/${next.id}`);
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function remove() {
  try {
    await ElMessageBox.confirm("删除本次执行记录及其对象结果？", "删除记录", {
      type: "warning",
    });
    busy.value = true;
    await api(`/inspection-runs/${run.value!.id}`, "DELETE");
    ElMessage.success("执行记录已删除");
    await router.push("/inspections");
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="inspection-page">
    <header class="page-header run-summary-header">
      <div class="page-heading">
        <div>
          <router-link class="subtle-link" :to="backToTasks()"
            >← 巡检任务</router-link
          >
          <div class="heading-copy">
            <h1>{{ run ? runName(run) : "巡检结果" }}</h1>
            <PageIntro />
            <span
              v-if="run"
              class="inspection-state"
              :class="tone(run.status)"
              >{{ stateNames[run.status] }}</span
            >
          </div>
        </div>

        <div class="actions">
          <el-button
            v-if="
              run &&
              !active &&
              run.run_kind !== 'trial' &&
              scope?.discovery_complete &&
              scope?.checks_unfinished &&
              auth.can('inspections:run')
            "
            :loading="busy"
            @click="resume"
            >补检未完成项</el-button
          >
          <el-button @click="load">刷新</el-button
          ><el-button
            v-if="active && auth.can('inspections:run')"
            :loading="busy"
            @click="stop"
            >停止执行</el-button
          >
        </div>
      </div>
      <RunConclusion v-if="run" :run="run" />
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="run">
      <el-alert
        v-if="run.run_kind === 'trial'"
        :title="`试运行 · 最多 ${trialLimit(run)} 个对象 · 未覆盖全部对象`"
        type="info"
        :closable="false"
      />
      <el-alert
        v-if="run.snapshot.resume"
        title="补检 · 沿用原始观察数据"
        type="info"
        :closable="false"
      />
      <el-button
        v-if="run.snapshot.resume"
        link
        @click="
          router.push(
            `/inspections/runs/${(run.snapshot.resume as Record<string, unknown>).parent_run_id}`,
          )
        "
        >查看原执行</el-button
      >
      <el-alert
        v-if="run.error_code"
        :type="
          scope?.checks_errors
            ? 'error'
            : run.error_stage === 'agent'
              ? 'warning'
              : run.status === 'failed'
                ? 'error'
                : 'info'
        "
        :title="
          scope?.checks_errors || run.error_stage === 'agent'
            ? runResult(run)
            : errorNames[run.error_code] || runResult(run)
        "
        :closable="false"
      />
      <section v-if="scope && tab !== 'process'" class="inspection-panel">
        <div class="coverage-heading">
          <h3>检查覆盖</h3>
          <div v-if="coveragePercent !== undefined" class="coverage-meter">
            <el-progress
              :percentage="coveragePercent"
              :stroke-width="4"
              :show-text="false"
              color="var(--el-color-primary)"
            />
            <span class="coverage-percentage">{{ coveragePercent }}%</span>
          </div>
        </div>
        <div class="inspection-facts">
          <div>
            <span>发现范围</span
            ><strong
              >{{ scope.discovery_complete ? "已完成发现" : "发现未完成" }} ·
              {{ scope.objects_discovered
              }}<template v-if="scope.objects_expected !== null">
                / {{ scope.objects_expected }}</template
              >
              个对象</strong
            >
          </div>
          <div>
            <span>{{
              run.run_kind === "trial" ? "样本检查覆盖" : "检查覆盖"
            }}</span
            ><strong
              >已完成 {{ scope.checks_completed }} /
              {{
                scope.expected_is_final
                  ? scope.checks_expected
                  : `至少 ${scope.checks_expected}`
              }}
              项</strong
            >
          </div>
          <div>
            <span>未完成检查</span
            ><strong>{{ scope.checks_unfinished }} 项</strong>
          </div>
          <div>
            <span>集合检查</span
            ><strong>{{ scope.collection_checks }} 项</strong>
          </div>
        </div>
      </section>
      <el-tabs v-model="tab">
        <el-tab-pane label="结果概览" name="overview"
          ><section class="inspection-panel">
            <template v-if="run.snapshot.checks"
              ><h3>逐项检查结果</h3>
              <CheckResults
                v-if="tab === 'overview'"
                :run-id="run.id"
                @process="viewProcess"
                show-process
                :active="!!active" /></template
            ><el-empty
              v-else
              description="本次执行没有逐项检查结果，请查看对象明细"
            /></section
        ></el-tab-pane>
        <el-tab-pane label="对象明细" name="objects">
          <div class="inspection-toolbar">
            <div class="inspection-filters">
              <ExportCsvButton
                :filename="(run ? runName(run) : '巡检') + '-对象结果'"
                :columns="itemExportColumns"
                :load="exportRows"
                :disabled="loading || !!error || !run"
              />
              <el-input
                v-model="search"
                placeholder="搜索对象名称或编号"
                clearable
              /><el-select v-model="result" clearable placeholder="全部结果"
                ><el-option label="正常" value="pass" /><el-option
                  label="异常"
                  value="fail" /><el-option label="待确认" value="unknown"
              /></el-select>
            </div>
          </div>
          <el-table
            v-loading="loading"
            :data="rows"
            empty-text="没有符合条件的检查对象"
            row-key="id"
            @row-click="inspect"
            ><el-table-column label="对象名称" min-width="250"
              ><template #default="{ row, $index }"
                ><el-button link type="primary" @click.stop="inspect(row)">{{
                  itemTitle(row, (page - 1) * size + $index + 1)
                }}</el-button></template
              ></el-table-column
            ><el-table-column label="状态" width="115"
              ><template #default="{ row }"
                ><el-tag :type="tone(row.final_status)">{{
                  stateNames[row.final_status]
                }}</el-tag></template
              ></el-table-column
            ><el-table-column
              prop="explanation"
              label="检查说明"
              min-width="360"
              show-overflow-tooltip
            /><el-table-column width="100"
              ><template #default="{ row }"
                ><el-button link @click.stop="inspect(row)"
                  >查看详情</el-button
                ></template
              ></el-table-column
            ></el-table
          >
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="size"
            :page-sizes="[20, 50, 100]"
            :total="total"
            layout="sizes, prev, pager, next"
          />
        </el-tab-pane>
        <el-tab-pane label="运行轨迹" name="process">
          <template v-if="tab === 'process'">
            <div class="inspection-toolbar">
              <el-button
                link
                type="primary"
                @click="
                  detailedEvidence = !detailedEvidence;
                  processCheck = undefined;
                "
              >
                {{ detailedEvidence ? "← 查看整轮轨迹" : "按对象查看证据 →" }}
              </el-button>
            </div>
            <ExecutionEvidence
              v-if="detailedEvidence"
              :run="run"
              :selected-check="processCheck"
            />
            <RunTrajectory v-else :key="run.id" :run="run" />
          </template>
        </el-tab-pane>
        <el-tab-pane label="Agent 结果" name="agent-results">
          <AgentResults
            v-if="tab === 'agent-results'"
            business="inspection"
            :scope-id="run.plan_id"
            :run-id="run.id"
          />
        </el-tab-pane>
      </el-tabs>
      <el-collapse
        ><el-collapse-item title="执行配置与错误详情" name="technical"
          ><el-descriptions :column="2" border
            ><el-descriptions-item label="触发方式">{{
              triggers[run.trigger] || run.trigger
            }}</el-descriptions-item
            ><el-descriptions-item label="重试次数">{{
              run.retry_count
            }}</el-descriptions-item
            ><el-descriptions-item label="错误阶段">{{
              run.error_stage || "—"
            }}</el-descriptions-item
            ><el-descriptions-item label="错误代码">{{
              run.error_code || "—"
            }}</el-descriptions-item></el-descriptions
          ><JsonEvidence :value="run.snapshot" /><el-button
            v-if="!active && auth.can('inspections:manage')"
            link
            type="danger"
            :loading="busy"
            @click="remove"
            >删除本次记录</el-button
          ></el-collapse-item
        ></el-collapse
      >
    </template>
    <ItemDrawer
      v-model="drawer"
      :item-id="selected"
      :active="!!active"
      show-process
      @process="viewProcess"
    />
  </section>
</template>
