<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, request, query } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
const rows = ref<Row[]>([]),
  unread = ref(false),
  total = ref(0),
  page = ref(1),
  deleting = ref(""),
  error = ref("");
const labels: Record<string, string> = {
  access_pending: "权限待审批",
  access_approved: "权限已批准",
  access_rejected: "权限已拒绝",
  incident_opened: "事故待处理",
  incident_overdue: "接单超时",
  improvement_overdue: "整改到期",
  budget_warning: "预算提醒",
  backup_overdue: "备份超期",
};
const mail: Record<string, string> = {
  disabled: "未启用",
  queued: "待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "失败",
  unknown: "结果未知",
};
async function load() {
  try {
    const result = await request<Row[]>(
      "/notifications" +
        query({
          unread_only: unread.value,
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    rows.value = result.data;
    total.value = result.total || 0;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => 30000);
async function read(row: Row) {
  try {
    await api(`/notifications/${row.id}/read`, "POST");
    window.dispatchEvent(new Event("notifications-read"));
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  }
}
async function remove(row: Row) {
  try {
    await ElMessageBox.confirm(
      "删除这条待办？关联的事故等业务记录会保留。",
      "删除待办",
      {
        type: "warning",
        confirmButtonText: "删除待办",
        cancelButtonText: "取消",
      },
    );
    deleting.value = row.id;
    await api(`/notifications/${row.id}`, "DELETE");
    ElMessage.success("待办已删除");
    window.dispatchEvent(new Event("notifications-read"));
    if (page.value > 1 && rows.value.length === 1) page.value -= 1;
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      error.value = e instanceof Error ? e.message : "删除失败";
  } finally {
    deleting.value = "";
  }
}
function target(row: Row) {
  if (row.resource_kind === "access_request") return "/access";
  if (row.resource_kind === "incident") return `/incidents/${row.resource_id}`;
  if (row.resource_kind === "improvement_task") return "/improvements";
  return "/system";
}
</script>
<template>
  <section class="page-header">
    <div class="page-heading">
      <h1>我的待办</h1>
      <PageIntro />

      <div class="actions">
        <el-switch
          v-model="unread"
          active-text="仅未读"
          @change="
            page = 1;
            refresh();
          "
        /><el-button @click="refresh">刷新</el-button>
      </div>
    </div>
  </section>
  <section class="panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table :data="rows" empty-text="暂无待办"
      ><el-table-column label="内容" min-width="240"
        ><template #default="s"
          ><router-link
            :to="target(s.row)"
            @click="!s.row.read_at && read(s.row)"
            >{{ s.row.title }}</router-link
          ></template
        ></el-table-column
      ><el-table-column label="类型"
        ><template #default="s">{{
          labels[s.row.kind] ||
          (s.row.kind.startsWith("improvement_due_")
            ? "整改到期"
            : s.row.kind.startsWith("improvement_review_")
              ? "整改待验收"
              : s.row.kind)
        }}</template></el-table-column
      ><el-table-column label="时间" min-width="175"
        ><template #default="s">{{
          date(s.row.created_at)
        }}</template></el-table-column
      ><el-table-column label="阅读状态"
        ><template #default="s">{{
          s.row.read_at ? "已读" : "未读"
        }}</template></el-table-column
      ><el-table-column label="邮件"
        ><template #default="s">{{
          mail[s.row.mail_status]
        }}</template></el-table-column
      ><el-table-column label="操作" min-width="180"
        ><template #default="s"
          ><el-button v-if="!s.row.read_at" link @click="read(s.row)"
            >标为已读</el-button
          ><el-button
            link
            type="danger"
            :loading="deleting === s.row.id"
            :disabled="!!deleting"
            @click="remove(s.row)"
            >删除待办</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="20"
      @current-change="refresh"
    />
  </section>
</template>
