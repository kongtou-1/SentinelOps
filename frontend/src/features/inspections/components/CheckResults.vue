<script setup lang="ts">
import { ref, watch } from "vue";
import { query, request, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import {
  errorNames,
  errorText,
  stateNames,
  tone,
} from "@/features/inspections/ui";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import InspectionChecks from "./InspectionChecks.vue";
const props = defineProps<{
  runId: string;
  itemId?: string;
  scope?: "object" | "collection";
  active?: boolean;
  showProcess?: boolean;
}>();
const emit = defineEmits<{
  process: [check: Models["InspectionCheckResultResponse"]];
}>();
const rows = ref<Models["InspectionCheckResultResponse"][]>([]),
  page = ref(1),
  total = ref(0),
  loading = ref(false),
  error = ref("");
let revision = 0;
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const result = await request<Models["InspectionCheckResultResponse"][]>(
      `/inspection-runs/${props.runId}/check-results` +
        query({
          item_id: props.itemId,
          scope: props.scope,
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
watch(page, () => void load());
watch(
  () => props.active,
  (active) => {
    if (!active) void load();
  },
);
usePoll(load, () => (props.active ? 3000 : null));
</script>
<template>
  <div class="check-results">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table
      v-loading="loading"
      :data="rows"
      row-key="id"
      empty-text="暂无检查结果"
    >
      <el-table-column type="expand"
        ><template #default="{ row }"
          ><div class="result-details">
            <el-alert
              v-if="row.error_code"
              :title="errorNames[row.error_code] || row.error_code"
              type="warning"
              :closable="false"
            /><template v-if="row.result.findings?.length"
              ><h4>发现的问题</h4>
              <p v-for="finding in row.result.findings" :key="finding.code">
                {{ finding.summary }}
              </p></template
            ><InspectionChecks
              v-if="row.result.conditions?.length"
              :checks="row.result.conditions"
            /><el-collapse
              ><el-collapse-item title="结构化结果与证据引用" :name="row.id"
                ><JsonEvidence :value="row.result" /></el-collapse-item
            ></el-collapse></div></template
      ></el-table-column>
      <el-table-column label="检查项" min-width="160"
        ><template #default="{ row }">{{
          row.result.name || row.check_id
        }}</template></el-table-column
      >
      <el-table-column v-if="!itemId" label="对象" min-width="130"
        ><template #default="{ row }">{{
          row.scope === "collection" ? "本次对象集合" : row.object_key
        }}</template></el-table-column
      >
      <el-table-column label="类型" width="85"
        ><template #default="{ row }">{{
          row.kind === "rule" ? "规则" : "Agent"
        }}</template></el-table-column
      >
      <el-table-column label="执行状态" width="105"
        ><template #default="{ row }">{{
          {
            pending: "待执行",
            running: "执行中",
            completed: "已完成",
            error: "执行错误",
          }[row.execution_status as string]
        }}</template></el-table-column
      >
      <el-table-column label="结论" width="90"
        ><template #default="{ row }"
          ><el-tag :type="tone(row.status)">{{
            stateNames[row.status]
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="检查说明" min-width="240">
        <template #default="{ row }">
          <span v-if="row.error_code" class="check-error">{{
            errorNames[row.error_code] || row.error_code
          }}</span>
          <span v-else>{{ row.summary }}</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="showProcess"
        label="操作"
        width="100"
        fixed="right"
      >
        <template #default="{ row }"
          ><el-button link type="primary" @click="emit('process', row)"
            >查看过程</el-button
          ></template
        >
      </el-table-column>
    </el-table>
    <el-pagination
      v-if="total > 20"
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="prev, pager, next"
    />
  </div>
</template>
<style scoped>
.check-results {
  display: grid;
  gap: 16px;
}
.check-error {
  color: var(--el-color-danger);
}
.result-details {
  padding: 18px 24px;
}
</style>
