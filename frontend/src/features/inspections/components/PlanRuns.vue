<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { all, api, query, request } from "@/api/client";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import ExportCsvButton from "@/components/records/ExportCsvButton.vue";
import { runExportColumns } from "@/features/inspections/exports";
import { date } from "@/utils/display";
import { activeRun } from "@/features/inspections/progress";
import {
  errorText,
  health,
  runResult,
  stateNames,
  tone,
  type Run,
} from "@/features/inspections/ui";
const auth = useAuth();
const emit = defineEmits<{ changed: [] }>();
const busy = ref("");
const props = defineProps<{
  planId: string;
  planName: string;
  refresh: number;
  returnTo: string;
}>();
const rows = ref<Run[]>([]),
  status = ref(""),
  runKind = ref(""),
  hasObjects = ref("true"),
  page = ref(1),
  total = ref(0),
  loading = ref(false),
  error = ref("");
const active = computed(() =>
  rows.value.some((r) =>
    ["queued", "collecting", "evaluating"].includes(r.status),
  ),
);
let revision = 0;
function filters() {
  return {
    plan_id: props.planId,
    status: status.value,
    run_kind: runKind.value,
    has_objects: hasObjects.value,
  };
}
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const result = await request<Run[]>(
      "/inspection-runs" +
        query({ ...filters(), offset: (page.value - 1) * 10, limit: 10 }),
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
async function remove(row: Run) {
  if (busy.value) return;
  busy.value = row.id;
  try {
    if (
      !(await confirmOperation(
        `删除“${props.planName}”在 ${date(row.started_at || row.created_at)} 的巡检记录及对象结果？可从回收站恢复。`,
        "删除巡检记录",
      ))
    )
      return;
    await api(`/inspection-runs/${row.id}`, "DELETE");
    ElMessage.success("记录已移入回收站");
    if (rows.value.length === 1 && page.value > 1) page.value--;
    else await load();
    emit("changed");
  } catch (e) {
    error.value = errorText(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = "";
  }
}
function exportRows(signal: AbortSignal) {
  return all<Run>("/inspection-runs" + query(filters()), signal);
}
watch([status, runKind, hasObjects], () => {
  if (page.value !== 1) page.value = 1;
  else void load();
});
watch(page, () => void load());
watch(
  () => props.refresh,
  () => void load(),
);
usePoll(load, () => (active.value ? 3000 : null));
</script>
<template>
  <section class="task-run-history" :aria-label="`${planName}的巡检记录`">
    <div class="inspection-toolbar">
      <h3>巡检记录</h3>
      <div class="inspection-filters">
        <el-select
          v-model="status"
          placeholder="全部巡检状态"
          clearable
          aria-label="巡检状态"
        >
          <el-option
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
            :value="s"
          />
        </el-select>
        <el-select
          v-model="runKind"
          placeholder="全部类型"
          clearable
          aria-label="巡检类型"
          ><el-option label="正式巡检" value="standard" /><el-option
            label="试运行"
            value="trial"
        /></el-select>
        <el-select v-model="hasObjects" aria-label="检查对象筛选">
          <el-option label="有检查对象" value="true" />
          <el-option label="无检查对象" value="false" />
          <el-option label="全部记录" value="" />
        </el-select>
        <ExportCsvButton
          :filename="`${planName}-巡检记录`"
          :columns="runExportColumns"
          :load="exportRows"
          :disabled="loading || !!error"
        />
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table
      v-loading="loading"
      :data="rows"
      empty-text="暂无巡检记录"
      row-key="id"
      ><el-table-column label="巡检时间" min-width="185"
        ><template #default="{ row }"
          >{{ date(row.started_at || row.created_at)
          }}<el-tag
            v-if="row.run_kind === 'trial'"
            type="info"
            class="trial-tag"
            >试运行</el-tag
          ></template
        ></el-table-column
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
      ><el-table-column label="操作" width="180" fixed="right"
        ><template #default="{ row }"
          ><router-link
            class="name-link"
            :to="{
              path: `/inspections/runs/${row.id}`,
              query: { return_to: returnTo },
            }"
            >{{ activeRun(row.status) ? "查看进度" : "查看结果" }}</router-link
          >
          <el-button
            v-if="auth.can('inspections:manage')"
            link
            type="danger"
            class="delete-record"
            :disabled="
              !!busy ||
              ['queued', 'collecting', 'evaluating'].includes(row.status)
            "
            :loading="busy === row.id"
            @click="remove(row)"
            >删除</el-button
          >
        </template></el-table-column
      >
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="10"
      layout="total, prev, pager, next"
    />
  </section>
</template>
<style scoped>
.task-run-history {
  padding: 12px 24px 12px 48px;
  background: var(--el-fill-color-light);
  border-left: 3px solid var(--el-color-primary);
  display: grid;
  gap: 10px;
}
h3 {
  margin: 0;
  font-size: 15px;
}
.delete-record {
  margin-left: 16px;
}
.trial-tag {
  margin-left: 10px;
}
@media (max-width: 760px) {
  .task-run-history {
    padding: 12px 16px;
  }
}
</style>
