<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import InspectionStatistics from "@/features/inspections/components/InspectionStatistics.vue";
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { all, api, query, request } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { activeRun } from "@/features/inspections/progress";
import ExportCsvButton from "@/components/records/ExportCsvButton.vue";
import { runExportColumns } from "@/features/inspections/exports";
import { date } from "@/utils/display";
import {
  errorText,
  health,
  runName,
  runResult,
  stateNames,
  tone,
  type Plan,
  type Run,
} from "@/features/inspections/ui";
const router = useRouter();
const route = useRoute(),
  auth = useAuth();
const rows = ref<Run[]>([]),
  plans = ref<Plan[]>([]),
  search = ref(String(route.query.search || "")),
  plan = ref(String(route.params.planId || route.query.plan_id || "")),
  status = ref(String(route.query.status || "")),
  runKind = ref(String(route.query.run_kind || "")),
  hasObjects = ref(String(route.query.has_objects ?? "true")),
  page = ref(Math.max(1, Number(route.query.page) || 1)),
  total = ref(0),
  error = ref(""),
  loading = ref(false);
let revision = 0;
function currentFilters() {
  return {
    search: search.value,
    plan_id: plan.value,
    status: status.value,
    run_kind: runKind.value,
    has_objects: hasObjects.value,
  };
}
function exportRows(signal: AbortSignal) {
  return all<Run>("/inspection-runs" + query(currentFilters()), signal);
}
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const result = await request<Run[]>(
      "/inspection-runs" +
        query({
          ...currentFilters(),
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    if (current !== revision) return;
    rows.value = result.data;
    total.value = result.total || 0;
    error.value = "";
  } catch (e) {
    if (current === revision) error.value = errorText(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch([search, plan, status, runKind, hasObjects], () => {
  page.value = 1;
  void load();
});
watch(page, () => void load());
watch([search, plan, status, runKind, hasObjects, page], () => {
  void router.replace({ query: { ...currentFilters(), page: page.value } });
});
usePoll(
  async () => {
    await load();
    if (!plans.value.length)
      try {
        plans.value = await all<Plan>("/inspection-plans");
      } catch (e) {
        error.value = errorText(e);
      }
  },
  () => 15000,
);
</script>
<template>
  <section class="inspection-page">
    <header class="page-header">
      <div class="page-heading">
        <h1>巡检记录</h1>
        <PageIntro />

        <div class="actions">
          <ExportCsvButton
            filename="巡检记录"
            :columns="runExportColumns"
            :load="exportRows"
            :disabled="loading || !!error"
          />
          <el-button @click="load">刷新</el-button>
        </div>
      </div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <InspectionStatistics kind="runs" />
    <div class="inspection-toolbar">
      <div class="inspection-filters">
        <el-input
          v-model="search"
          placeholder="搜索任务名称"
          clearable
        /><el-select v-model="plan" placeholder="全部任务" clearable filterable
          ><el-option
            v-for="p in plans"
            :key="p.id"
            :label="p.name"
            :value="p.id" /></el-select
        ><el-select v-model="status" placeholder="全部执行状态" clearable
          ><el-option
            v-for="s in [
              'queued',
              'collecting',
              'evaluating',
              'completed',
              'partial',
              'failed',
              'cancelled',
            ]"
            :key="s"
            :label="stateNames[s]"
            :value="s" /></el-select
        ><el-select v-model="runKind" placeholder="全部执行类型" clearable
          ><el-option label="正式执行" value="standard" /><el-option
            label="试运行"
            value="trial"
        /></el-select>
        <el-select v-model="hasObjects" aria-label="检查对象筛选">
          <el-option label="有检查对象" value="true" />
          <el-option label="无检查对象" value="false" />
          <el-option label="全部记录" value="" />
        </el-select>
      </div>
      <span class="inspection-count">共 {{ total }} 条</span>
    </div>
    <el-table
      v-loading="loading"
      :data="rows"
      empty-text="暂无巡检记录"
      row-key="id"
      ><el-table-column label="巡检任务" min-width="210"
        ><template #default="{ row }"
          ><router-link
            v-if="row.plan_id && row.run_kind !== 'trial'"
            class="name-link"
            :to="`/inspections/plans/${row.plan_id}`"
            >{{ runName(row) }}</router-link
          ><span v-else>{{ runName(row) }}</span
          ><el-tag
            v-if="row.run_kind === 'trial'"
            type="info"
            style="margin-left: 8px"
            >试运行</el-tag
          ></template
        ></el-table-column
      ><el-table-column label="执行时间" min-width="185"
        ><template #default="{ row }">{{
          date(row.started_at || row.created_at)
        }}</template></el-table-column
      ><el-table-column label="检查对象" width="105"
        ><template #default="{ row }">{{
          row.summary.objects || 0
        }}</template></el-table-column
      ><el-table-column label="正常 / 异常" width="130"
        ><template #default="{ row }"
          >{{ health(row).pass || 0 }} / {{ health(row).fail || 0 }}</template
        ></el-table-column
      ><el-table-column label="执行状态" width="120"
        ><template #default="{ row }"
          ><el-tag :type="tone(row.status)">{{
            stateNames[row.status]
          }}</el-tag></template
        ></el-table-column
      ><el-table-column label="执行说明" min-width="245"
        ><template #default="{ row }">{{
          runResult(row)
        }}</template></el-table-column
      ><el-table-column label="操作" width="110" fixed="right"
        ><template #default="{ row }"
          ><router-link class="name-link" :to="`/inspections/runs/${row.id}`">{{
            activeRun(row.status) ? "查看进度" : "查看结果"
          }}</router-link></template
        ></el-table-column
      >
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="20"
      layout="total, prev, pager, next"
    />
  </section>
</template>
