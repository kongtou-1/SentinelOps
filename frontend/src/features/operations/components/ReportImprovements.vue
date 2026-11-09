<script setup lang="ts">
import { ref } from "vue";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import type { Row } from "@/types/records";
const props = defineProps<{ report: Row }>();
const auth = useAuth(),
  selected = ref<Row[]>([]),
  error = ref(""),
  busy = ref(false),
  draftOpen = ref(false),
  title = ref(""),
  content = ref("");
async function importItems() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        `将所选 ${selected.value.length} 项整改内容建立为正式整改任务？`,
        "建立整改任务",
      ))
    )
      return;
    const items = props.report.content?.improvements || [];
    await api(`/reports/${props.report.id}/improvements`, "POST", {
      expected_revision: props.report.revision,
      item_indexes: selected.value.map((row) => items.indexOf(row)),
    });
    ElMessage.success("整改任务已建立");
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function draft() {
  busy.value = true;
  try {
    await api(`/reports/${props.report.id}/knowledge-draft`, "POST", {
      expected_revision: props.report.revision,
      title: title.value,
      content: content.value,
    });
    draftOpen.value = false;
    ElMessage.success("已保存停用知识草稿");
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <div class="actions">
        <router-link
          :to="{ path: '/improvements', query: { report: report.id } }"
          ><el-button>查看整改任务</el-button></router-link
        ><el-button
          v-if="auth.can('incidents:handle') && report.status === 'approved'"
          :disabled="!selected.length"
          :loading="busy"
          @click="importItems"
          >建立所选整改</el-button
        ><el-button
          v-if="auth.can('knowledge:manage') && report.status === 'approved'"
          @click="
            title = report.content?.title || '复盘知识';
            content = selected.map((r) => r.action).join('\n');
            draftOpen = true;
          "
          >提炼知识草稿</el-button
        >
      </div>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    /><el-table
      :data="report.content?.improvements || []"
      @selection-change="selected = $event"
      ><el-table-column type="selection" /><el-table-column
        prop="action"
        label="改进项" /><el-table-column
        prop="owner"
        label="报告负责人" /><el-table-column
        prop="deadline"
        label="报告期限" /></el-table
    ><el-dialog v-model="draftOpen" title="停用知识草稿"
      ><el-form label-width="80px"
        ><el-form-item label="标题" required
          ><el-input v-model="title" :maxlength="200" /></el-form-item
        ><el-form-item label="内容" required
          ><el-input
            v-model="content"
            type="textarea"
            :rows="10"
            :maxlength="100000" /></el-form-item></el-form
      ><router-link to="/knowledge">知识文档</router-link
      ><template #footer
        ><el-button
          type="primary"
          :disabled="!title.trim() || !content.trim()"
          :loading="busy"
          @click="draft"
          >保存停用草稿</el-button
        ></template
      ></el-dialog
    >
  </section>
</template>
