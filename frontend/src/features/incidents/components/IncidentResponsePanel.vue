<script setup lang="ts">
import { ref } from "vue";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date, short } from "@/utils/display";
import type { Row } from "@/types/records";
const props = defineProps<{ incidentId: string; writable: boolean }>();
const emit = defineEmits<{ accepted: [] }>();
const auth = useAuth(),
  data = ref<Row>(),
  error = ref(""),
  busy = ref(false);
async function load() {
  try {
    data.value = await api(`/incidents/${props.incidentId}/response`);
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => (props.writable ? 30000 : null));
async function accept() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "确认由你接单并开始处理此事故？",
        "接单并开始处理",
      ))
    )
      return;
    await api(`/incidents/${props.incidentId}/accept`, "POST");
    ElMessage.success("已接单，事故进入处理中");
    await refresh();
    emit("accepted");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "接单失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading response-heading">
      <h2>事故响应</h2>
      <div class="actions">
        <el-button @click="refresh">刷新</el-button
        ><el-button
          v-if="writable && !data?.accepted_at && auth.can('incidents:handle')"
          type="primary"
          :loading="busy"
          @click="accept"
          >接单并开始处理</el-button
        >
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-descriptions v-if="data" :column="3" border>
      <el-descriptions-item label="响应状态">{{
        data.accepted_at
          ? "已接单"
          : data.enabled
            ? data.overdue
              ? "接单超时"
              : "待接单"
            : "历史事故"
      }}</el-descriptions-item>
      <el-descriptions-item label="响应期限">{{
        date(data.due_at)
      }}</el-descriptions-item>
      <el-descriptions-item label="升级时间">{{
        date(data.escalated_at)
      }}</el-descriptions-item>
      <el-descriptions-item label="接单人">{{
        short(data.accepted_by)
      }}</el-descriptions-item>
      <el-descriptions-item label="接单时间">{{
        date(data.accepted_at)
      }}</el-descriptions-item>
      <el-descriptions-item label="通知状态">{{
        data.suppression?.length ? "已抑制" : "正常"
      }}</el-descriptions-item>
    </el-descriptions>
    <el-table v-if="data?.suppression?.length" :data="data.suppression"
      ><el-table-column label="原因"
        ><template #default="s">{{
          s.row.reason === "explicit_parent_rule"
            ? "父规则告警触发中"
            : s.row.reason
        }}</template></el-table-column
      ><el-table-column label="结束时间"
        ><template #default="s">{{
          date(s.row.ends_at)
        }}</template></el-table-column
      ></el-table
    >
  </section>
</template>
<style scoped>
.response-heading {
  flex-wrap: wrap;
}
.response-heading .actions {
  margin-left: auto;
}
</style>
