<script setup lang="ts">
import { ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date, short } from "@/utils/display";
import type { Row } from "@/types/records";
const props = defineProps<{ incidentId: string; writable: boolean }>();
const auth = useAuth(),
  rows = ref<Row[]>([]),
  error = ref("");
const kinds: Record<string, string> = {
  related: "同服务关联",
  dependency: "依赖关联",
  recurrence: "短期复发",
};
const states: Record<string, string> = {
  candidate: "待确认",
  confirmed: "已确认",
  dismissed: "已排除",
};
async function load() {
  try {
    rows.value = await api(
      `/incidents/${props.incidentId}/relations?limit=100`,
    );
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => null);
async function decide(row: Row, status: string) {
  try {
    const result = await ElMessageBox.prompt(
      "处理说明",
      status === "confirmed" ? "确认关联" : "排除关联",
      { inputValidator: (value) => !!value?.trim() || "请填写说明" },
    );
    await api(`/incident-relations/${row.id}/decision`, "POST", {
      expected_version: row.version,
      status,
      comment: result.value,
    });
    ElMessage.success(
      status === "confirmed" ? "事故关联已确认" : "事故关联已排除",
    );
    await refresh();
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message);
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h2>事故关联</h2>
      <el-button @click="refresh">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table :data="rows" empty-text="暂无关联候选">
      <el-table-column label="关联事故"
        ><template #default="s"
          ><router-link
            :to="`/incidents/${s.row.incident_id === incidentId ? s.row.related_incident_id : s.row.incident_id}`"
            >{{
              short(
                s.row.incident_id === incidentId
                  ? s.row.related_incident_id
                  : s.row.incident_id,
              )
            }}</router-link
          ></template
        ></el-table-column
      >
      <el-table-column label="类型"
        ><template #default="s">{{
          kinds[s.row.kind]
        }}</template></el-table-column
      >
      <el-table-column label="状态"
        ><template #default="s">{{
          states[s.row.status]
        }}</template></el-table-column
      >
      <el-table-column label="记录时间"
        ><template #default="s">{{
          date(s.row.created_at)
        }}</template></el-table-column
      >
      <el-table-column
        v-if="writable && auth.can('incidents:handle')"
        label="操作"
        ><template #default="s"
          ><template v-if="s.row.incident_id === incidentId"
            ><el-button link @click="decide(s.row, 'confirmed')">确认</el-button
            ><el-button link @click="decide(s.row, 'dismissed')"
              >排除</el-button
            ></template
          ></template
        ></el-table-column
      >
    </el-table>
  </section>
</template>
