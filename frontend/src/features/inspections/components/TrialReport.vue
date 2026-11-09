<script setup lang="ts">
import { computed, ref } from "vue";
import { api, query, request, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import {
  errorText,
  runResult,
  stateNames,
  tone,
  type Item,
  type Run,
} from "@/features/inspections/ui";
import ItemDrawer from "./ItemDrawer.vue";
const props = defineProps<{ runId: string }>();
const run = ref<Run>(),
  rows = ref<Item[]>([]),
  checks = ref<Models["InspectionCheckResultResponse"][]>([]),
  error = ref("");
const selected = ref(""),
  drawer = ref(false);
const active = computed(
  () =>
    !run.value ||
    ["queued", "collecting", "evaluating"].includes(run.value.status),
);
async function load() {
  try {
    [run.value, rows.value] = await Promise.all([
      api<Run>(`/inspection-runs/${props.runId}`),
      api<Item[]>(`/inspection-runs/${props.runId}/items?limit=10`),
    ]);
    const result = await request<Models["InspectionCheckResultResponse"][]>(
      `/inspection-runs/${props.runId}/check-results` + query({ limit: 100 }),
    );
    checks.value = result.data;
    error.value = "";
  } catch (e) {
    error.value = errorText(e);
  }
}
usePoll(load, () => (active.value ? 2000 : null));
</script>
<template>
  <section class="inspection-panel trial-report">
    <header class="inspection-heading">
      <h3>试运行结果 · 最多 3 个对象</h3>
      <router-link
        class="name-link"
        :to="`/inspections/runs/${runId}`"
        target="_blank"
        >打开完整报告 ↗</router-link
      >
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="run"
      ><el-tag :type="tone(run.status)">{{ stateNames[run.status] }}</el-tag>
      <p>{{ runResult(run) }}</p>
      <el-table :data="checks" empty-text="等待检查结果"
        ><el-table-column
          prop="object_key"
          label="对象"
          min-width="120" /><el-table-column label="检查项" min-width="150"
          ><template #default="{ row }">{{
            row.result.name || row.check_id
          }}</template></el-table-column
        ><el-table-column label="执行" width="100"
          ><template #default="{ row }">{{
            {
              pending: "待执行",
              running: "执行中",
              completed: "已完成",
              error: "执行错误",
            }[row.execution_status as string]
          }}</template></el-table-column
        ><el-table-column label="结论" width="100"
          ><template #default="{ row }"
            ><el-tag :type="tone(row.status)">{{
              stateNames[row.status]
            }}</el-tag></template
          ></el-table-column
        ><el-table-column prop="summary" label="说明" min-width="220"
      /></el-table>
      <div class="trial-objects">
        <el-button
          v-for="item in rows"
          :key="item.id"
          @click="
            selected = item.id;
            drawer = true;
          "
          >查看对象 {{ item.record_id }} 的数据与过程</el-button
        >
      </div></template
    >
    <ItemDrawer v-model="drawer" :item-id="selected" :active="active" />
  </section>
</template>
<style scoped>
.trial-report header {
  margin-bottom: 20px;
}
.trial-objects {
  margin-top: 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
