<script setup lang="ts">
import { ref } from "vue";
import { api, all } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import type { Row } from "@/types/records";
const props = defineProps<{ resultId: string }>();
const auth = useAuth(),
  rows = ref<Row[]>([]),
  incidents = ref<Row[]>([]),
  error = ref(""),
  selected = ref<Row>(),
  evidence = ref(""),
  incidentId = ref("");
const states: Record<string, string> = {
  observed: "观测完整",
  incomplete: "观测不完整",
  excluded: "未纳入统计",
};
async function load() {
  try {
    rows.value = await api(`/predictions/${props.resultId}/outcomes`);
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
const { refresh } = usePoll(load, () => 30000);
async function open(row: Row) {
  try {
    incidents.value = await all("/incidents");
    selected.value = row;
    evidence.value = "";
    incidentId.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
async function link() {
  try {
    await api(`/prediction-outcomes/${selected.value!.id}/incidents`, "POST", {
      incident_id: incidentId.value,
      expected_version: selected.value!.version,
      evidence: evidence.value,
    });
    selected.value = undefined;
    await refresh();
  } catch (e) {
    error.value = String(e);
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h2>后续观测对照</h2>
      <el-button @click="refresh">刷新</el-button>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    /><el-table :data="rows" empty-text="观测窗口尚未完成"
      ><el-table-column label="预测窗口"
        ><template #default="{ row }"
          >{{ row.horizon_hours }} 小时</template
        ></el-table-column
      ><el-table-column label="观测"
        ><template #default="{ row }">{{
          states[row.status]
        }}</template></el-table-column
      ><el-table-column label="阈值命中"
        ><template #default="{ row }">{{
          row.status === "observed"
            ? row.observation.threshold_match
              ? "一致"
              : "不一致"
            : "未知"
        }}</template></el-table-column
      ><el-table-column label="提前量"
        ><template #default="{ row }">{{
          row.observation.lead_seconds == null
            ? "—"
            : row.observation.lead_seconds + " 秒"
        }}</template></el-table-column
      ><el-table-column label="真实事故"
        ><template #default="{ row }"
          ><router-link
            v-for="item in row.incident_links"
            :key="item.incident_id"
            :to="'/incidents/' + item.incident_id"
            >{{ item.incident_id.slice(0, 8) }} </router-link
          ><el-button v-if="auth.can('analyses:run')" text @click="open(row)"
            >关联事故</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-dialog
      :model-value="!!selected"
      title="确认事故关联"
      @close="selected = undefined"
      ><el-form label-width="80px"
        ><el-form-item label="事故" required
          ><el-select v-model="incidentId" filterable
            ><el-option
              v-for="r in incidents"
              :key="r.id"
              :value="r.id"
              :label="r.title" /></el-select></el-form-item
        ><el-form-item label="依据" required
          ><el-input
            v-model="evidence"
            type="textarea"
            :maxlength="4000" /></el-form-item></el-form
      ><template #footer
        ><el-button
          type="primary"
          :disabled="!incidentId || !evidence.trim()"
          @click="link"
          >确认关联</el-button
        ></template
      ></el-dialog
    >
  </section>
</template>
