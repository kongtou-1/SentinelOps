<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { useRoute, useRouter } from "vue-router";
import { api, query, request } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { activeRun } from "@/features/inspections/progress";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import {
  errorText,
  runResult,
  schedule,
  nextExecution,
  type Plan,
  type Run,
  type DataSource,
} from "@/features/inspections/ui";
import RunConclusion from "@/features/inspections/components/RunConclusion.vue";
import PlanConfiguration from "@/features/inspections/components/PlanConfiguration.vue";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const plan = ref<Plan>(),
  runs = ref<Run[]>([]),
  latestRun = ref<Run>(),
  sourceName = ref(""),
  error = ref(""),
  tab = ref("overview"),
  historyPage = ref(1),
  historyTotal = ref(0),
  busy = ref(false),
  pending = ref("");
async function load() {
  try {
    const [p, r] = await Promise.all([
      api<Plan>(`/inspection-plans/${route.params.planId}`),
      request<Run[]>(
        "/inspection-runs" +
          query({
            plan_id: route.params.planId,
            run_kind: "standard",
            offset: (historyPage.value - 1) * 20,
            limit: 20,
          }),
      ),
    ]);
    plan.value = p;
    runs.value = r.data;
    latestRun.value =
      historyPage.value === 1
        ? r.data[0]
        : (
            await api<Run[]>(
              "/inspection-runs" +
                query({
                  plan_id: route.params.planId,
                  run_kind: "standard",
                  limit: 1,
                }),
            )
          )[0];
    historyTotal.value = r.total || 0;
    if (p.data_source_id) {
      const source = await api<DataSource>(
        `/inspection-data-sources/${p.data_source_id}`,
      );
      sourceName.value = source.name;
    }
    error.value = "";
  } catch (e) {
    error.value = errorText(e);
  }
}
watch(historyPage, () => void load());
usePoll(load, () => (busy.value ? null : 15000));
function backToList() {
  const target = String(route.query.return_to || "/inspections");
  return target === "/inspections" ||
    target.startsWith("/inspections?") ||
    target === "/inspections/plans" ||
    target.startsWith("/inspections/plans?") ||
    target.startsWith("/inspections/services/")
    ? target
    : "/inspections";
}
async function start() {
  if (busy.value || !plan.value) return;
  const { id, name } = plan.value;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        `立即执行巡检任务“${name}”？如已有进行中的巡检，将进入该记录。`,
        "立即执行",
      ))
    )
      return;
    pending.value = "run";
    error.value = "";
    const r = await api<Run>(`/inspection-plans/${id}/runs`, "POST");
    ElMessage.success("已进入本次巡检记录，可查看执行进度");
    await router.push(`/inspections/runs/${r.id}`);
  } catch (e) {
    error.value = errorText(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = false;
    pending.value = "";
  }
}
async function state() {
  if (busy.value || !plan.value) return;
  const { id, name, enabled, version } = plan.value;
  const action = enabled ? "暂停" : "启用";
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        enabled
          ? `暂停巡检任务“${name}”？暂停后不再自动触发，进行中的巡检会继续。`
          : `启用巡检任务“${name}”？启用后将按计划配置触发巡检。`,
        `${action}任务`,
      ))
    )
      return;
    pending.value = "state";
    error.value = "";
    await api(`/inspection-plans/${id}/state`, "PUT", {
      enabled: !enabled,
      expected_version: version,
    });
    ElMessage.success(`巡检任务“${name}”已${action}`);
    await load();
  } catch (e) {
    error.value = errorText(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = false;
    pending.value = "";
  }
}
async function remove() {
  if (busy.value) return;
  try {
    await ElMessageBox.confirm(
      `删除巡检任务“${plan.value!.name}”？`,
      "删除任务",
      { type: "warning" },
    );
    busy.value = true;
    await api(
      `/inspection-plans/${plan.value!.id}` +
        query({ expected_version: plan.value!.version }),
      "DELETE",
    );
    ElMessage.success("巡检任务已删除");
    await router.push("/inspections/plans");
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="inspection-page">
    <header class="page-header">
      <div class="page-heading">
        <div>
          <router-link class="subtle-link" :to="backToList()"
            >← 巡检任务</router-link
          >
          <div class="heading-copy">
            <h1>{{ plan?.name || "任务详情" }}</h1>
            <PageIntro />
            <span
              v-if="plan"
              class="inspection-state"
              :class="plan.enabled ? 'success' : 'info'"
              >{{ plan.enabled ? "已启用" : "已暂停" }}</span
            >
          </div>
        </div>

        <div v-if="plan" class="actions">
          <el-button
            v-if="auth.can('inspections:manage')"
            :disabled="busy"
            @click="router.push(`/inspections/plans/${plan.id}/edit`)"
            >编辑</el-button
          ><el-button
            v-if="auth.can('inspections:manage')"
            :disabled="busy"
            :loading="pending === 'state'"
            @click="state"
            >{{ plan.enabled ? "暂停任务" : "启用任务" }}</el-button
          ><el-button
            v-if="auth.can('inspections:run')"
            type="primary"
            :disabled="busy"
            :loading="pending === 'run'"
            @click="start"
            >立即执行</el-button
          >
          <el-dropdown v-if="auth.can('inspections:manage')" trigger="click">
            <el-button :disabled="busy">更多</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="remove">删除任务</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <RunConclusion v-if="latestRun" :run="latestRun" latest />
    <section v-else-if="plan" class="inspection-panel inspection-count">
      尚未执行
    </section>
    <el-tabs v-if="plan" v-model="tab">
      <el-tab-pane label="概览" name="overview"
        ><section class="inspection-panel">
          <h3>任务配置</h3>
          <div class="inspection-facts">
            <div>
              <span>执行安排</span><strong>{{ schedule(plan) }}</strong>
            </div>
            <div>
              <span>下次执行</span><strong>{{ nextExecution(plan) }}</strong>
            </div>
            <div>
              <span>对象来源</span
              ><strong>{{
                plan.object_mode === "fixed"
                  ? `${(plan.fixed_objects || []).length} 个固定对象`
                  : sourceName || "任务中配置的 API 或数据库查询"
              }}</strong>
            </div>
            <div>
              <span>执行限制</span
              ><strong
                >{{ plan.max_records }} 对象 · {{ plan.max_agent_items }} 次
                Agent · {{ plan.timeout_seconds }} 秒</strong
              >
            </div>
          </div>
        </section>
        <section class="inspection-panel recent-runs">
          <div class="inspection-heading">
            <h3>最近巡检</h3>
            <el-button link type="primary" @click="tab = 'history'"
              >查看全部 →</el-button
            >
          </div>
          <el-table :data="runs.slice(0, 3)" empty-text="尚未执行">
            <el-table-column label="执行时间"
              ><template #default="{ row }"
                ><router-link :to="`/inspections/runs/${row.id}`">{{
                  date(row.started_at || row.created_at)
                }}</router-link></template
              ></el-table-column
            >
            <el-table-column label="检查结论"
              ><template #default="{ row }">{{
                runResult(row)
              }}</template></el-table-column
            >
            <el-table-column label="对象数"
              ><template #default="{ row }">{{
                row.summary.objects || 0
              }}</template></el-table-column
            >
          </el-table>
        </section></el-tab-pane
      >
      <el-tab-pane label="检查内容" name="configuration">
        <PlanConfiguration v-if="tab === 'configuration'" :plan="plan" />
      </el-tab-pane>
      <el-tab-pane label="本任务记录" name="history"
        ><el-table :data="runs" empty-text="本任务尚无正式巡检记录"
          ><el-table-column label="执行时间" min-width="185"
            ><template #default="{ row }">{{
              date(row.started_at || row.created_at)
            }}</template></el-table-column
          ><el-table-column label="对象数量" width="120"
            ><template #default="{ row }">{{
              row.summary.objects || 0
            }}</template></el-table-column
          ><el-table-column label="执行说明" min-width="250"
            ><template #default="{ row }">{{
              runResult(row)
            }}</template></el-table-column
          ><el-table-column label="操作" width="140"
            ><template #default="{ row }"
              ><router-link
                :to="`/inspections/runs/${row.id}`"
                class="name-link"
                >{{
                  activeRun(row.status) ? "查看进度" : "查看检查结果"
                }}</router-link
              ></template
            ></el-table-column
          ></el-table
        ><el-pagination
          v-model:current-page="historyPage"
          :page-size="20"
          :total="historyTotal"
          layout="prev, pager, next"
      /></el-tab-pane>
    </el-tabs>
  </section>
</template>
