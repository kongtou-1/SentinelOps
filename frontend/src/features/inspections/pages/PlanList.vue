<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import PlanRuns from "@/features/inspections/components/PlanRuns.vue";
import InspectionStatistics from "@/features/inspections/components/InspectionStatistics.vue";
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { all, api, query, request } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import { confirmOperation } from "@/utils/confirmOperation";
import {
  errorText,
  schedule,
  nextExecution,
  stateNames,
  tone,
  type Plan,
  type Run,
} from "@/features/inspections/ui";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const rows = ref<Plan[]>([]),
  search = ref(String(route.query.search || "")),
  enabled = ref(String(route.query.enabled || "")),
  page = ref(Math.max(1, Number(route.query.page) || 1)),
  total = ref(0),
  loading = ref(false),
  error = ref("");
const catalog = ref<Plan[]>([]);
const service = ref(
  String(route.params.serviceId || route.query.application_id || ""),
);
const environment = ref(String(route.query.environment || ""));
const serviceOptions = computed(() => [
  ...new Map(
    catalog.value
      .filter((p) => p.application_id)
      .map((p) => [p.application_id!, p.application_name || "未命名服务"]),
  ).entries(),
]);
const environments = computed(() => [
  ...new Set(catalog.value.map((p) => p.environment)),
]);
const busy = ref(""),
  pending = ref("");
const expanded = ref<string[]>(
  route.query.task ? [String(route.query.task)] : [],
);
const refresh = ref(0);
function expand(row: Plan, opened: Plan[]) {
  expanded.value = opened.some((p) => p.id === row.id) ? [row.id] : [];
  void router.replace({ query: { ...route.query, task: expanded.value[0] } });
}
function toggleRecords(row: Plan) {
  expanded.value = expanded.value.includes(row.id) ? [] : [row.id];
  void router.replace({ query: { ...route.query, task: expanded.value[0] } });
}
let revision = 0;
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const result = await request<Plan[]>(
      "/inspection-plans" +
        query({
          search: search.value,
          enabled: enabled.value,
          application_id: service.value,
          environment: environment.value,
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    if (current !== revision) return;
    rows.value = result.data;
    refresh.value++;
    total.value = result.total || 0;
    error.value = "";
  } catch (e) {
    if (current === revision) error.value = errorText(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch([search, enabled, service, environment], () => {
  expanded.value = [];
  page.value = 1;
  void load();
  void router.replace({
    query: {
      search: search.value || undefined,
      enabled: enabled.value || undefined,
      application_id: service.value || undefined,
      environment: environment.value || undefined,
    },
  });
});
watch(page, () => {
  expanded.value = [];
  void load();
  void router.replace({
    query: { ...route.query, page: page.value, task: undefined },
  });
});
usePoll(
  async () => {
    await load();
    try {
      catalog.value = await all<Plan>("/inspection-plans");
    } catch (e) {
      error.value = errorText(e);
    }
  },
  () => (busy.value ? null : 15000),
);
function edit(row?: Plan) {
  void router.push(
    row
      ? `/inspections/plans/${row.id}/edit`
      : {
          path: "/inspections/plans/new",
          query: {
            application_id: route.params.serviceId
              ? String(route.params.serviceId)
              : undefined,
          },
        },
  );
}
async function copy(row: Plan) {
  if (busy.value) return;
  busy.value = row.id;
  try {
    const saved = await api<Plan>(`/inspection-plans/${row.id}/copy`, "POST");
    ElMessage.success("已复制为暂停任务");
    await router.push(`/inspections/plans/${saved.id}/edit`);
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function state(row: Plan) {
  if (busy.value) return;
  const { id, name, enabled, version } = row;
  const action = enabled ? "暂停" : "启用";
  busy.value = row.id;
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
    busy.value = "";
    pending.value = "";
  }
}
async function remove(row: Plan) {
  if (busy.value) return;
  const { id, name, version } = row;
  busy.value = id;
  try {
    if (
      !(await confirmOperation(
        `删除巡检任务“${name}”？进行中的巡检会停止，历史记录保留，可从回收站恢复任务。`,
        "删除任务",
      ))
    )
      return;
    pending.value = "delete";
    await api(
      `/inspection-plans/${id}` + query({ expected_version: version }),
      "DELETE",
    );
    expanded.value = expanded.value.filter((key) => key !== id);
    if (String(route.query.task || "") === id)
      void router.replace({ query: { ...route.query, task: undefined } });
    ElMessage.success("任务已移入回收站");
    if (rows.value.length === 1 && page.value > 1) page.value--;
    else await load();
  } catch (e) {
    error.value = errorText(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = "";
    pending.value = "";
  }
}
async function run(row: Plan) {
  if (busy.value) return;
  const { id, name } = row;
  busy.value = row.id;
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
    const result = await api<Run>(`/inspection-plans/${id}/runs`, "POST");
    ElMessage.success("已进入本次巡检记录，可查看执行进度");
    await router.push(`/inspections/runs/${result.id}`);
  } catch (e) {
    error.value = errorText(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = "";
    pending.value = "";
  }
}
</script>
<template>
  <section class="inspection-page">
    <header class="page-header">
      <div class="page-heading">
        <h1>巡检任务</h1>
        <PageIntro />

        <div class="actions">
          <el-button
            v-if="auth.can('users:manage')"
            @click="router.push('/recycle-bin')"
            >回收站</el-button
          >
          <el-button :loading="loading" @click="load">刷新</el-button>
          <el-button
            v-if="auth.can('inspections:manage')"
            type="primary"
            :disabled="!!busy"
            @click="edit()"
            >创建任务</el-button
          >
        </div>
      </div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <InspectionStatistics kind="plans" :plans="catalog" />
    <div class="inspection-toolbar">
      <div class="inspection-filters">
        <el-input
          v-model="search"
          placeholder="搜索任务名称"
          clearable
          aria-label="搜索任务"
        /><el-select v-model="enabled" placeholder="全部状态" clearable
          ><el-option label="已启用" value="true" /><el-option
            label="已暂停"
            value="false"
        /></el-select>
        <el-select
          v-model="service"
          placeholder="全部关联服务"
          clearable
          filterable
          aria-label="关联服务"
          ><el-option
            v-for="[id, name] in serviceOptions"
            :key="id"
            :value="id"
            :label="name"
        /></el-select>
        <el-select
          v-model="environment"
          placeholder="全部环境"
          clearable
          aria-label="环境"
          ><el-option
            v-for="env in environments"
            :key="env"
            :value="env"
            :label="env"
        /></el-select>
      </div>
      <span class="inspection-count">共 {{ total }} 个任务</span>
    </div>
    <el-table
      v-loading="loading"
      :data="rows"
      row-key="id"
      :expand-row-keys="expanded"
      @expand-change="expand"
      empty-text="暂无巡检任务"
    >
      <el-table-column type="expand" width="44"
        ><template #default="{ row }"
          ><PlanRuns
            v-if="expanded.includes(row.id)"
            :key="row.id"
            :plan-id="row.id"
            :plan-name="row.name"
            :refresh="refresh"
            :return-to="route.fullPath"
            @changed="load" /></template
      ></el-table-column>
      <el-table-column label="任务名称 / 关联服务" min-width="230">
        <template #default="{ row }">
          <router-link
            class="name-link"
            :to="{
              path: `/inspections/plans/${row.id}`,
              query: { return_to: route.fullPath },
            }"
            >{{ row.name }}</router-link
          >
          <div class="inspection-subline">
            {{ row.application_name || "独立巡检" }} · {{ row.environment }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="执行方式" min-width="160">
        <template #default="{ row }">{{ schedule(row) }}</template>
      </el-table-column>
      <el-table-column label="下次执行" min-width="180">
        <template #default="{ row }">{{ nextExecution(row) }}</template>
      </el-table-column>
      <el-table-column label="最近巡检时间" min-width="180">
        <template #default="{ row }">{{
          row.last_run_at ? date(row.last_run_at) : "—"
        }}</template>
      </el-table-column>
      <el-table-column label="最近执行结果" min-width="170">
        <template #default="{ row }">
          <router-link
            v-if="row.last_run_id"
            class="result-link"
            :to="`/inspections/runs/${row.last_run_id}`"
          >
            <el-tag
              :type="
                tone(
                  row.last_status === 'completed'
                    ? row.last_result
                    : row.last_status,
                )
              "
            >
              {{
                row.last_status === "completed"
                  ? stateNames[row.last_result || "unknown"]
                  : stateNames[row.last_status || "unknown"]
              }}
            </el-tag>
          </router-link>
          <span v-else>尚未执行</span>
        </template>
      </el-table-column>
      <el-table-column label="任务状态" width="100">
        <template #default="{ row }">
          <span
            class="inspection-state"
            :class="row.enabled ? 'success' : 'info'"
            >{{ row.enabled ? "已启用" : "已暂停" }}</span
          >
        </template>
      </el-table-column>
      <el-table-column label="操作" width="310" fixed="right">
        <template #default="{ row }">
          <div class="task-actions">
            <el-button
              v-if="auth.can('inspections:run')"
              type="primary"
              link
              :disabled="!!busy"
              :loading="busy === row.id && pending === 'run'"
              @click="run(row)"
              >立即执行</el-button
            ><el-button
              link
              :aria-expanded="expanded.includes(row.id)"
              @click="toggleRecords(row)"
              >巡检记录</el-button
            ><el-button
              v-if="auth.can('inspections:manage')"
              link
              :disabled="!!busy"
              :loading="busy === row.id && pending === 'state'"
              @click="state(row)"
              >{{ row.enabled ? "暂停任务" : "启用任务" }}</el-button
            ><el-dropdown v-if="auth.can('inspections:manage')" trigger="click"
              ><el-button link :disabled="!!busy">更多</el-button
              ><template #dropdown
                ><el-dropdown-menu
                  ><el-dropdown-item @click="edit(row)">编辑</el-dropdown-item
                  ><el-dropdown-item @click="copy(row)">复制</el-dropdown-item
                  ><el-dropdown-item divided @click="remove(row)"
                    >删除任务</el-dropdown-item
                  ></el-dropdown-menu
                ></template
              ></el-dropdown
            >
          </div>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="total, prev, pager, next"
    />
  </section>
</template>
<style scoped>
.result-link {
  text-decoration: none;
}
.task-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  white-space: nowrap;
}
.task-actions .el-button + .el-button {
  margin-left: 0;
}
</style>
