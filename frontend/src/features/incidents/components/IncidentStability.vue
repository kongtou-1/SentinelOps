<script setup lang="ts">
import { ref } from "vue";
import { all } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
const props = defineProps<{ incidentId: string }>();
const rows = ref<Row[]>([]),
  error = ref("");
async function load() {
  try {
    rows.value = await all(`/incidents/${props.incidentId}/alerts`);
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
usePoll(load, () => 30000);
function elapsed(row: Row) {
  return row.stability_started_at && row.stability_last_evidence_at
    ? Math.max(
        0,
        (new Date(row.stability_last_evidence_at).getTime() -
          new Date(row.stability_started_at).getTime()) /
          1000,
      )
    : 0;
}
const states: Record<string, string> = {
  pending: "待核验",
  verified: "已稳定核验",
  retired: "已终止",
};
</script>
<template>
  <section class="panel">
    <h2>业务恢复与持续稳定</h2>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    /><el-table :data="rows"
      ><el-table-column label="告警"
        ><template #default="{ row }"
          ><router-link :to="'/alerts/' + row.id">{{
            row.annotations?.summary ||
            row.labels?.alertname ||
            row.id.slice(0, 8)
          }}</router-link></template
        ></el-table-column
      ><el-table-column label="告警状态"
        ><template #default="{ row }">{{
          row.status === "firing" ? "触发中" : "已解除"
        }}</template></el-table-column
      ><el-table-column label="业务恢复"
        ><template #default="{ row }">{{
          row.status === "firing"
            ? "仍有异常"
            : states[row.recovery_status] || "待核验"
        }}</template></el-table-column
      ><el-table-column label="冻结稳定窗口"
        ><template #default="{ row }">{{
          row.rule_snapshot?.stability_seconds == null
            ? "历史策略"
            : row.rule_snapshot.stability_seconds + " 秒"
        }}</template></el-table-column
      ><el-table-column label="已证实连续正常"
        ><template #default="{ row }"
          >{{ elapsed(row) }} 秒</template
        ></el-table-column
      ><el-table-column label="最近证据"
        ><template #default="{ row }">{{
          date(row.stability_last_evidence_at || row.recovery_evidence_at)
        }}</template></el-table-column
      ></el-table
    >
  </section>
</template>
